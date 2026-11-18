"""H04 reviewed profiles and actual bounded read verification on existing connectors."""
from datetime import datetime, timezone, timedelta
from dataclasses import fields
from app.models.connector import ConnectorModel
from app.integrations.hospital_read.manifest import HospitalReadProfile
from app.integrations.hospital_read.rest_source import RestSource
from app.integrations.hospital_read.mappings import normalize_record
from app.integrations.core.source_authority import resolve_secret
from app.services.hospital_scope_service import resolve_hospital_scope, require_capability, HospitalAccessDenied
from app.services.hospital_audit import record_hospital_audit


def assert_approved_origin(profile):
    import os
    from urllib.parse import urlsplit
    origin = urlsplit(profile.base_url)
    normalized = f"{origin.scheme}://{origin.netloc.lower()}"
    allowed = {value.strip().rstrip("/").lower() for value in
               os.getenv("HSP01_APPROVED_SOURCE_ORIGINS", "").split(",") if value.strip()}
    if normalized not in allowed:
        raise PermissionError("Source origin requires server-operator approval")


def serialize_profile(profile):
    value = {field.name: getattr(profile, field.name) for field in fields(profile)}
    value["endpoints"] = dict(profile.endpoints)
    value["status_map"] = {kind: dict(mapping) for kind, mapping in profile.status_map.items()}
    value["department_map"] = dict(profile.department_map)
    return value


def configure_source(db, actor, profile, *, secret_ref, auth_header, auth_prefix, reason):
    assert_approved_origin(profile)
    scope = resolve_hospital_scope(db, actor, {"clinic_id": profile.clinic_id}, for_update=True)
    require_capability(scope, "source.configure")
    if scope.org_id != profile.org_id or not scope.all_departments:
        raise HospitalAccessDenied("Facility-wide source configuration required")
    from app.models.hospital_dimensions import HospitalFacilityEnrollment
    if not db.get(HospitalFacilityEnrollment, profile.clinic_id):
        db.add(HospitalFacilityEnrollment(clinic_id=profile.clinic_id, basis_ref=reason))
        db.flush()
        record_hospital_audit(db, scope, actor.id, "HOSPITAL_FACILITY_ENROLLED", profile.clinic_id,
                              reason=reason, new={"clinic_id": profile.clinic_id})
    if not set(profile.department_map.values()).issubset(scope.allowed_department_ids):
        raise ValueError("Department crosswalk points outside the authorized facility")
    if not secret_ref.startswith(("env://", "vault://")) or auth_header not in {"Authorization", "X-API-Key"}:
        raise ValueError("Reviewed authentication header and secret reference required")
    if not isinstance(auth_prefix, str) or "\r" in auth_prefix or "\n" in auth_prefix or len(auth_prefix) > 40:
        raise ValueError("Invalid authentication prefix")
    connector = db.get(ConnectorModel, profile.connector_id)
    definition = serialize_profile(profile)
    auth = {"header": auth_header, "prefix": auth_prefix}
    if connector:
        if connector.vendor_id != "hospital_read" or connector.clinic_id != profile.clinic_id:
            raise ValueError("Existing connector ownership/vendor cannot be reassigned")
        old = (connector.config or {}).get("hospital_profile")
        if old and old["revision"] == profile.revision and old != definition:
            raise ValueError("Profile content changed under the same revision")
        if (old and old["mapping_id"] == profile.mapping_id and old["mapping_version"] == profile.mapping_version
            and any(old.get(field) != definition.get(field) for field in
                    ("status_map", "department_map", "department_ids_are_canonical", "attribution_time_basis"))):
            raise ValueError("Mapping content changed under the same version")
        if old == definition and connector.secret_ref == secret_ref and (connector.config or {}).get("hospital_auth") == auth:
            return connector
    else:
        connector = ConnectorModel(id=profile.connector_id, name=profile.connector_id,
                                   vendor_id="hospital_read", type="hospital_read", clinic_id=profile.clinic_id)
        db.add(connector)
    connector.config = {**(connector.config or {}), "hospital_profile": definition,
                        "hospital_auth": auth, "hospital_readiness": None, "jurisdiction": "UAE"}
    connector.secret_ref = secret_ref
    connector.state, connector.status, connector.permitted_resources = "CONFIGURED", "Configured", []
    record_hospital_audit(db, scope, actor.id, "HOSPITAL_SOURCE_CONFIGURED", connector.id,
                          reason=reason, new={"profile_revision": profile.revision, "mapping_version": profile.mapping_version})
    return connector


class SourceReadFailed(RuntimeError):
    """A fenced failed verification was recorded; caller must commit its audit."""


def verification_basis(connector):
    import json
    return json.dumps([connector.config, connector.secret_ref, connector.state,
                       connector.permitted_resources], sort_keys=True)


async def verify_source(db, actor, connector_id, client):
    from app.core.config import settings
    if settings.DEPLOYMENT_REGION != "UAE":
        raise PermissionError("Hospital source jurisdiction not approved")
    connector = db.get(ConnectorModel, connector_id)
    if not connector or connector.vendor_id != "hospital_read":
        raise ValueError("Hospital connector unavailable")
    scope = resolve_hospital_scope(db, actor, {"clinic_id": connector.clinic_id})
    require_capability(scope, "source.configure")
    if not scope.all_departments or connector.state not in {"CONFIGURED", "AUTHORIZED_READY", "DEGRADED"}:
        raise HospitalAccessDenied("Source verification unavailable")
    original_basis, grant_revision = verification_basis(connector), scope.grant_revision
    profile = HospitalReadProfile(**connector.config["hospital_profile"])
    assert_approved_origin(profile)
    auth = connector.config["hospital_auth"]
    secret_ref = connector.secret_ref
    # Resolve only in memory and send only to the reviewed exact origin.
    headers = {auth["header"]: auth["prefix"] + resolve_secret(secret_ref)}
    # Release the read transaction before network work; final publication is fenced.
    db.rollback()
    verified, failed = [], False
    try:
        for kind in profile.endpoints:
            iterator = RestSource(profile, client, headers).iter_pages(kind)
            try:
                page = await anext(iterator)
                for raw in page.records:
                    normalize_record(raw, resource_kind=kind, context=profile)
                verified.append(kind)
            finally:
                await iterator.aclose()
    except Exception:
        failed = True
    scope = resolve_hospital_scope(db, actor, {"clinic_id": profile.clinic_id}, for_update=True)
    require_capability(scope, "source.configure")
    from sqlalchemy import select
    connector = db.scalars(select(ConnectorModel).where(ConnectorModel.id == connector_id)
                           .with_for_update().execution_options(populate_existing=True)).one()
    if (not scope.all_departments or scope.grant_revision != grant_revision
            or verification_basis(connector) != original_basis):
        raise PermissionError("Source configuration changed during verification")
    if failed:
        connector.state, connector.status = "DEGRADED", "Disconnected"
        connector.config = {**connector.config, "hospital_readiness": None}
        record_hospital_audit(db, scope, actor.id, "HOSPITAL_SOURCE_READ_FAILED", connector.id,
                              reason="Actual bounded source read failed")
        raise SourceReadFailed("Source read failed")
    proof = {"revision": profile.revision, "verified_at": datetime.now(timezone.utc).isoformat(),
             "bindings": {kind: profile.cursor_binding(kind) for kind in verified},
             "secret_ref": connector.secret_ref, "auth": auth}
    connector.config = {**connector.config, "hospital_readiness": proof}
    connector.permitted_resources = verified
    connector.state, connector.status = "AUTHORIZED_READY", "Connected"
    record_hospital_audit(db, scope, actor.id, "HOSPITAL_SOURCE_READ_VERIFIED", connector.id,
                          reason="Actual bounded authentication/read contract", new={"resources": verified, "profile_revision": profile.revision})
    return proof


def assert_profile_ready(db, profile, kind):
    assert_approved_origin(profile)
    connector = db.get(ConnectorModel, profile.connector_id, populate_existing=True)
    if (not connector or connector.vendor_id != "hospital_read" or connector.state != "AUTHORIZED_READY"
            or connector.clinic_id != profile.clinic_id or kind not in (connector.permitted_resources or [])):
        raise PermissionError("Hospital source grant unavailable")
    config = connector.config or {}
    proof = config.get("hospital_readiness") or {}
    if (config.get("hospital_profile") != serialize_profile(profile) or config.get("jurisdiction") != "UAE"
        or proof.get("bindings", {}).get(kind) != profile.cursor_binding(kind)
            or proof.get("secret_ref") != connector.secret_ref or proof.get("auth") != config.get("hospital_auth")):
        raise PermissionError("Hospital source readiness invalidated")
    try:
        verified_at = datetime.fromisoformat(proof["verified_at"])
        if verified_at.tzinfo is None or datetime.now(timezone.utc) - verified_at > timedelta(hours=1):
            raise ValueError("Expired proof")
    except (ValueError, KeyError, TypeError):
        raise PermissionError("Hospital read proof unavailable or expired") from None
