"""Authenticated HSP01 surfaces. Explicit grants are checked on every request."""
from datetime import datetime, timezone, timedelta
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.organization import OrganizationClinicModel
from app.models.hospital_membership import HospitalMembership
from app.models.hospital_dimensions import HospitalDepartment, HospitalDepartmentVersion
from app.models.governance_storage import RecommendationModel
from app.models.signal import SignalModel
from app.models.work_assignment import WorkAssignment
from app.services.hospital_scope_service import (
    resolve_hospital_scope, require_capability, permits_department, HospitalAccessDenied,
)
from app.services.work_assignment_service import WorkAssignmentService, AssignmentConflict
from app.services.financial_reporting_service import financial_reporting_service, neutralize_formula
from app.models.financial_reporting import ReportSnapshotModel
from app.models.hospital_processing import HospitalSnapshotPage
import json

router = APIRouter()


def resolved(db, actor, clinic_id, department_id=None, *, for_update=False):
    selection = {"clinic_id": clinic_id}
    if department_id is not None:
        selection["department_ids"] = [department_id]
    try:
        return resolve_hospital_scope(db, actor, selection, for_update=for_update)
    except HospitalAccessDenied:
        raise HTTPException(403, "Hospital scope unavailable") from None


def recommendation_basis(db, recommendation):
    # Legacy signals without trusted hospital attribution remain unavailable.
    # Resolve the trusted journey reference in SQL, never scan all tenants.
    from sqlalchemy import or_
    signals = db.scalars(select(SignalModel).where(or_(
        SignalModel.metadata_data["correlation_id"].as_string() == recommendation.journey_id,
        SignalModel.metadata_data["pipeline_event_id"].as_string() == recommendation.journey_id,
    )).limit(2))
    matches = []
    for signal in signals:
        meta = signal.metadata_data or {}
        if (meta.get("correlation_id") or meta.get("pipeline_event_id")) == recommendation.journey_id:
            if meta.get("hospital_attribution_basis"):
                matches.append({"org_id": signal.org_id, "clinic_id": signal.clinic_id,
                                "department_id": meta.get("department_id")})
            if len(matches) > 1:
                break
    if len(matches) != 1:
        raise HospitalAccessDenied("Trusted recommendation ownership unresolved")
    return matches[0]


@router.get("/directory")
def directory(actor=Depends(get_current_user), db=Depends(get_db)):
    rows = db.execute(select(OrganizationClinicModel).join(HospitalMembership,
                                                           HospitalMembership.clinic_id == OrganizationClinicModel.id).where(
        HospitalMembership.actor_id == actor.id, HospitalMembership.is_active.is_(True),
        OrganizationClinicModel.organization_id == actor.org_id).limit(101)).scalars().all()
    if len(rows) > 100:
        raise HTTPException(422, "Directory requires pagination")
    result = []
    for facility in rows:
        scope = resolved(db, actor, facility.id)
        departments = db.scalars(select(HospitalDepartment).where(
            HospitalDepartment.id.in_(scope.allowed_department_ids)).order_by(HospitalDepartment.code).limit(501)).all()
        if len(departments) > 500:
            raise HTTPException(422, "Department directory requires pagination")
        from sqlalchemy import or_
        now = datetime.now(timezone.utc)
        versions = db.scalars(select(HospitalDepartmentVersion).where(
            HospitalDepartmentVersion.department_id.in_([row.id for row in departments]),
            HospitalDepartmentVersion.valid_from <= now,
            or_(HospitalDepartmentVersion.valid_to.is_(None), HospitalDepartmentVersion.valid_to > now)))
        names = {}
        for version in versions:
            names.setdefault(version.department_id, []).append(version.name)
        from app.models.hospital_processing import HospitalCheckpoint
        from sqlalchemy import func
        last_import = db.scalar(select(func.max(HospitalCheckpoint.updated_at)).where(
            HospitalCheckpoint.clinic_id == facility.id))
        if last_import is not None and last_import.tzinfo is None:
            last_import = last_import.replace(tzinfo=timezone.utc)
        result.append({"clinic_id": facility.id, "name": facility.name,
                       "last_import_at": last_import.isoformat() if last_import is not None else None,
                       "scope": scope.model_dump(mode="json"), "departments": [
                           {"id": row.id, "code": row.code,
                            "name": names[row.id][0] if len(names.get(row.id, [])) == 1 else row.code}
                           for row in departments]})
    return {"facilities": result}


@router.get("/summary")
def summary(clinic_id: str, department_id: str | None = None, currency: str = "AED",
            start: datetime | None = None, end: datetime | None = None,
            actor=Depends(get_current_user), db=Depends(get_db)):
    scope = resolved(db, actor, clinic_id, department_id)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(days=30)
    try:
        return financial_reporting_service.generate_hospital_summary(db, scope, start, end, currency)
    except HospitalAccessDenied:
        raise HTTPException(403, "Report capability unavailable") from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/queue")
def queue(clinic_id: str, department_id: str | None = None, offset: int = Query(0, ge=0),
          actor=Depends(get_current_user), db=Depends(get_db)):
    scope = resolved(db, actor, clinic_id, department_id)
    try:
        require_capability(scope, "review.read")
    except HospitalAccessDenied:
        raise HTTPException(403, "Review capability unavailable") from None
    # Assignments contain trusted scope; unassigned legacy recommendations cannot
    # be presented as hospital-owned without explicit source attribution.
    query = select(RecommendationModel, WorkAssignment).join(WorkAssignment,
                                                             WorkAssignment.recommendation_id == RecommendationModel.recommendation_id).where(
        WorkAssignment.clinic_id == scope.clinic_id)
    from app.services.hospital_scope_service import scope_predicate
    query = query.where(scope_predicate(scope, WorkAssignment.clinic_id, WorkAssignment.department_id))
    rows = db.execute(query.order_by(RecommendationModel.recommendation_id).offset(offset).limit(101)).all()
    return {"scope": scope.model_dump(mode="json"), "has_more": len(rows) > 100,
            "items": [{"recommendation_id": rec.recommendation_id, "status": rec.status,
                       "department_id": assignment.department_id, "assignee": assignment.assignee,
                       "team": assignment.team, "row_version": assignment.row_version} for rec, assignment in rows[:100]],
            "unavailable_reason": "Billing review needs an approved hospital billing policy. Recommendations without verified ownership remain unavailable."}


@router.get("/audit")
def hospital_audit(clinic_id: str, offset: int = Query(0, ge=0),
                   actor=Depends(get_current_user), db=Depends(get_db)):
    from app.models.audit import AuditLogModel
    scope = resolved(db, actor, clinic_id)
    try:
        require_capability(scope, "audit.read")
        if not scope.all_departments or not scope.full_record:
            raise HospitalAccessDenied("Facility-wide audit grant required")
    except HospitalAccessDenied:
        raise HTTPException(403, "Audit capability unavailable") from None
    rows = db.scalars(select(AuditLogModel).where(AuditLogModel.module == "Hospital",
                                                  AuditLogModel.org_id == scope.org_id, AuditLogModel.clinic_id == scope.clinic_id)
                      .order_by(AuditLogModel.timestamp.desc(), AuditLogModel.id).offset(offset).limit(101)).all()
    return {"has_more": len(rows) > 100, "rows": [{"id": row.id, "actor": row.user_system,
                                                   "action": row.action, "entity_ref": row.entity_ref, "reason": row.reason,
                                                   "timestamp": row.timestamp.isoformat(), "details": json.loads(row.details_json or "{}")}
                                                  for row in rows[:100]]}


@router.get("/jobs")
def jobs(clinic_id: str, offset: int = Query(0, ge=0), actor=Depends(get_current_user), db=Depends(get_db)):
    from app.models.event import OperationalEventModel
    from sqlalchemy import func
    scope = resolved(db, actor, clinic_id)
    try:
        require_capability(scope, "source.read")
        if not scope.all_departments:
            raise HospitalAccessDenied("Facility-wide job visibility required")
    except HospitalAccessDenied:
        raise HTTPException(403, "Job visibility unavailable") from None
    predicate = (OperationalEventModel.org_id == scope.org_id) & (OperationalEventModel.clinic_id == scope.clinic_id) & (OperationalEventModel.workflow_key == "hospital-financial")
    counts = db.execute(select(OperationalEventModel.state, func.count()).where(predicate)
                        .group_by(OperationalEventModel.state)).all()
    rows = db.scalars(select(OperationalEventModel).where(predicate).order_by(
        OperationalEventModel.received_at.desc(), OperationalEventModel.id).offset(offset).limit(101)).all()
    return {"counts": {state: count for state, count in counts}, "has_more": len(rows) > 100,
            "rows": [{"id": row.id, "state": row.state, "retry_count": row.retry_count,
                      "row_version": row.row_version,
                      "last_error": row.last_error, "received_at": row.received_at.isoformat(),
                      "next_attempt_at": row.next_attempt_at.isoformat() if row.next_attempt_at else None} for row in rows[:100]]}


class AssignRequest(BaseModel):
    clinic_id: str
    department_id: str | None = None
    expected_version: int = Field(ge=0)
    assignee: str | None = Field(default=None, max_length=200)
    team: str | None = Field(default=None, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class ResumeRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/jobs/{event_id}/resume")
def resume_delivery(event_id: str, body: ResumeRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    from app.services.hospital_worker import resume_blocked, DeliveryReconciliationRequired
    try:
        event = resume_blocked(db, actor, event_id, expected_revision=body.expected_revision, reason=body.reason)
        db.commit()
        return {"event_id": event.id, "state": event.state, "row_version": event.row_version}
    except DeliveryReconciliationRequired as error:
        db.rollback()
        raise HTTPException(409, {"code": str(error), "action": "Governed historical reconciliation required; original evidence was preserved"}) from None
    except PermissionError:
        db.rollback()
        raise HTTPException(403, "Delivery recovery unavailable") from None
    except ValueError:
        db.rollback()
        raise HTTPException(409, "Delivery binding or revision changed; refresh required") from None


class SnapshotRequest(BaseModel):
    clinic_id: str
    department_id: str | None = None
    currency: str = Field(min_length=3, max_length=3)
    start: datetime
    end: datetime


class DepartmentRequest(BaseModel):
    clinic_id: str
    code: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    valid_from: datetime
    basis_ref: str = Field(min_length=1, max_length=1000)


@router.post("/departments")
def configure_department(body: DepartmentRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    from app.services.hospital_directory_service import HospitalDirectoryService
    try:
        scope = resolve_hospital_scope(db, actor, {"clinic_id": body.clinic_id}, for_update=True)
        require_capability(scope, "directory.write")
        if not scope.all_departments:
            raise HospitalAccessDenied("Facility-wide directory grant required")
        row = HospitalDirectoryService(db).create_department(scope.org_id, scope.clinic_id,
                                                             body.code, body.name, body.valid_from, body.basis_ref)
        from app.services.hospital_audit import record_hospital_audit
        record_hospital_audit(db, scope, actor.id, "HOSPITAL_DEPARTMENT_CREATED", row.id,
                              reason=body.basis_ref, new={"clinic_id": row.clinic_id, "code": row.code})
        db.commit()
        return {"department_id": row.id, "clinic_id": row.clinic_id}
    except HospitalAccessDenied:
        db.rollback()
        raise HTTPException(403, "Directory configuration unavailable") from None
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from None


class MembershipRequest(BaseModel):
    clinic_id: str
    target_actor_id: int
    expected_revision: int = Field(ge=0)
    department_ids: list[str] = Field(max_length=500)
    capabilities: list[str] = Field(max_length=50)
    all_departments: bool = False
    allow_unattributed: bool = False
    full_record: bool = False
    is_active: bool = True
    reason: str = Field(min_length=1, max_length=2000)


class SourceProfileRequest(BaseModel):
    profile: dict
    secret_ref: str = Field(min_length=1, max_length=1000)
    auth_header: str
    auth_prefix: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=2000)


class SourceRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/sources/{connector_id}/revoke")
def revoke_source(connector_id: str, body: SourceRevokeRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    from app.models.connector import ConnectorModel
    from app.services.hospital_audit import record_hospital_audit
    connector = db.get(ConnectorModel, connector_id)
    if not connector or connector.vendor_id != "hospital_read":
        raise HTTPException(404, "Hospital source unavailable")
    scope = resolved(db, actor, connector.clinic_id, for_update=True)
    try:
        require_capability(scope, "source.configure")
        if not scope.all_departments:
            raise HospitalAccessDenied("Facility-wide configuration required")
    except HospitalAccessDenied:
        raise HTTPException(403, "Source revocation unavailable") from None
    connector.state, connector.status = "REVOKED", "Disconnected"
    connector.config = {**(connector.config or {}), "hospital_readiness": None}
    record_hospital_audit(db, scope, actor.id, "HOSPITAL_SOURCE_REVOKED", connector_id, reason=body.reason)
    db.commit()
    return {"state": "REVOKED"}


@router.post("/sources/profile")
def source_profile(body: SourceProfileRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    from app.services.hospital_source_service import configure_source
    from app.integrations.hospital_read.manifest import HospitalReadProfile
    try:
        profile = HospitalReadProfile(**body.profile)
        connector = configure_source(db, actor, profile, secret_ref=body.secret_ref,
                                     auth_header=body.auth_header, auth_prefix=body.auth_prefix, reason=body.reason)
        db.commit()
        return {"connector_id": connector.id, "state": connector.state}
    except PermissionError:
        db.rollback()
        raise HTTPException(403, "Source configuration unavailable") from None
    except (ValueError, TypeError):
        db.rollback()
        raise HTTPException(422, "Invalid reviewed hospital profile") from None


@router.post("/sources/{connector_id}/verify")
async def source_verify(connector_id: str, actor=Depends(get_current_user), db=Depends(get_db)):
    import httpx
    from app.models.connector import ConnectorModel
    from app.services.hospital_source_service import verify_source, SourceReadFailed
    connector = db.get(ConnectorModel, connector_id)
    if not connector or connector.vendor_id != "hospital_read":
        raise HTTPException(404, "Hospital source unavailable")
    scope = resolved(db, actor, connector.clinic_id)
    try:
        require_capability(scope, "source.configure")
        if not scope.all_departments:
            raise HospitalAccessDenied("Facility-wide configuration required")
    except HospitalAccessDenied:
        raise HTTPException(403, "Source verification unavailable") from None
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            proof = await verify_source(db, actor, connector_id, client)
        db.commit()
        return {"state": "AUTHORIZED_READY", "profile_revision": proof["revision"], "resources": sorted(proof["bindings"])}
    except PermissionError:
        db.rollback()
        raise HTTPException(403, "Source authority changed") from None
    except SourceReadFailed:
        db.commit()
        raise HTTPException(503, "Hospital source read verification failed") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "Hospital source read verification failed") from None


@router.post("/sources/{connector_id}/sync")
async def source_sync(connector_id: str, actor=Depends(get_current_user), db=Depends(get_db)):
    import httpx
    from app.models.connector import ConnectorModel
    from app.services.hospital_source_service import assert_profile_ready
    from app.integrations.core.source_authority import resolve_secret
    from app.integrations.hospital_read.manifest import HospitalReadProfile
    from app.integrations.hospital_read.adapter import HospitalReadAdapter
    from app.services.ingress_service import CanonicalIngressService
    connector = db.get(ConnectorModel, connector_id)
    if not connector or connector.vendor_id != "hospital_read":
        raise HTTPException(404, "Hospital source unavailable")
    scope = resolved(db, actor, connector.clinic_id)
    try:
        require_capability(scope, "source.read")
        if not scope.all_departments:
            raise HospitalAccessDenied("Facility-wide import required")
    except HospitalAccessDenied:
        raise HTTPException(403, "Source import unavailable") from None
    if db.get_bind().dialect.name != "postgresql":
        raise HTTPException(503, "Hospital live sync requires the verified PostgreSQL worker environment")
    profile = HospitalReadProfile(**connector.config["hospital_profile"])
    try:
        for kind in profile.endpoints:
            assert_profile_ready(db, profile, kind)
        auth = connector.config["hospital_auth"]
        headers = {auth["header"]: auth["prefix"] + resolve_secret(connector.secret_ref)}
        intake = CanonicalIngressService()
        intake.hospital_actor_id = actor.id
        async with httpx.AsyncClient(trust_env=False) as client:
            adapter = HospitalReadAdapter(profile, client, authorize=intake._hospital_authority, intake=intake, headers=headers)
            return await adapter.sync()
    except PermissionError:
        raise HTTPException(403, "Source authority unavailable") from None
    except Exception:
        raise HTTPException(503, "Hospital sync incomplete; committed progress is retained") from None


@router.post("/memberships")
def configure_access(body: MembershipRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    from app.services.hospital_scope_service import configure_membership
    try:
        grant = configure_membership(db, actor, **body.model_dump())
        db.commit()
        return {"grant_revision": grant.grant_revision}
    except HospitalAccessDenied:
        db.rollback()
        raise HTTPException(403, "Grant configuration unavailable") from None
    except ValueError as error:
        db.rollback()
        raise HTTPException(409, str(error)) from None


@router.post("/snapshots")
def freeze_snapshot(body: SnapshotRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    scope = resolved(db, actor, body.clinic_id, body.department_id, for_update=True)
    try:
        row = financial_reporting_service.freeze_hospital_summary(db, scope, body.start, body.end, body.currency)
        db.commit()
        return json.loads(row.payload_json)
    except HospitalAccessDenied:
        db.rollback()
        raise HTTPException(403, "Report capability unavailable") from None
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from None


@router.get("/snapshots/{snapshot_id}/csv")
def export_snapshot(snapshot_id: str, actor=Depends(get_current_user), db=Depends(get_db)):
    row = db.get(ReportSnapshotModel, snapshot_id)
    if not row or row.report_code != "HSP01_LINES_V1":
        raise HTTPException(404, "Snapshot unavailable")
    scope = resolved(db, actor, row.clinic_id)
    try:
        require_capability(scope, "report.export")
        require_capability(scope, "report.read")
    except HospitalAccessDenied:
        raise HTTPException(403, "Export capability unavailable") from None
    payload = json.loads(row.payload_json)
    if payload["scope"]["org_id"] != scope.org_id or any(
            not permits_department(scope, item["department_id"]) for item in payload["rows"]):
        raise HTTPException(403, "Snapshot exceeds current scope")
    if not set(payload["scope"]["allowed_department_ids"]).issubset(scope.allowed_department_ids):
        raise HTTPException(403, "Snapshot exceeds current scope")
    if payload["scope"].get("allow_unattributed") and not scope.allow_unattributed:
        raise HTTPException(403, "Snapshot exceeds current scope")

    def csv_stream():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["snapshot_id", "currency", "line_id", "department_id", "billed_amount", "paid_amount", "status"])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        pages = db.scalars(select(HospitalSnapshotPage).where(
            HospitalSnapshotPage.snapshot_id == snapshot_id).order_by(HospitalSnapshotPage.page_number)).yield_per(1)
        for page in pages:
            current = resolved(db, actor, row.clinic_id)
            require_capability(current, "report.read")
            require_capability(current, "report.export")
            items = json.loads(page.rows_json)
            if any(not permits_department(current, item["department_id"]) for item in items):
                raise PermissionError("Export scope revoked")
            for item in items:
                writer.writerow([neutralize_formula(value) for value in [snapshot_id, payload["currency"],
                                                                         item["line_id"], item["department_id"], item["billed_amount"], item["paid_amount"], item["status"]]])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
    return StreamingResponse(csv_stream(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="hospital-report.csv"'})


@router.post("/assignments/{recommendation_id}")
def assign(recommendation_id: str, body: AssignRequest, actor=Depends(get_current_user), db=Depends(get_db)):
    selection = {"clinic_id": body.clinic_id}
    if body.department_id:
        selection["department_ids"] = [body.department_id]
    try:
        version = WorkAssignmentService(db, recommendation_basis).assign(actor, selection, recommendation_id,
                                                                         expected_version=body.expected_version, assignee=body.assignee, team=body.team, reason=body.reason)
        db.commit()
        return {"recommendation_id": recommendation_id, "row_version": version}
    except HospitalAccessDenied:
        db.rollback()
        raise HTTPException(403, "Assignment unavailable") from None
    except AssignmentConflict:
        db.rollback()
        raise HTTPException(409, "Assignment changed; refresh required") from None
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from None
