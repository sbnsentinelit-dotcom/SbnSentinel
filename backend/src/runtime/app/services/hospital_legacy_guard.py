"""Prevent unscoped legacy surfaces from exposing enabled hospital records."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.organization import OrganizationClinicModel


def hospital_legacy_guard(request: Request, actor=Depends(get_current_user), db=Depends(get_db)):
    if request.url.path.endswith("/auth/me") or "/hospital/" in request.url.path:
        return
    from app.services.hospital_scope_service import hospital_facility_ids
    facilities = hospital_facility_ids()
    from app.models.hospital_processing import FacilityCrosswalk
    explicit = request.query_params.get("clinic_id")
    actor_clinic = getattr(actor, "clinic_id", None)
    target = explicit or actor_clinic
    if target:
        crosswalk = db.get(FacilityCrosswalk, target)
        if crosswalk and crosswalk.status == "RESOLVED":
            target = crosswalk.clinic_id
    if target and db.scalar(select(OrganizationClinicModel.id).where(
            OrganizationClinicModel.id == target, OrganizationClinicModel.id.in_(facilities)).limit(1)):
        raise HTTPException(403, "Use the hospital scoped API for this facility")
    if not target and actor.org_id and db.scalar(select(OrganizationClinicModel.id).where(
            OrganizationClinicModel.organization_id == actor.org_id,
            OrganizationClinicModel.id.in_(facilities)).limit(1)):
        raise HTTPException(403, "An explicit scoped hospital surface is required")
    # Direct IDs must not bypass the list/query guard.
    from app.models.signal import SignalModel
    from app.models.event import OperationalEventModel
    from app.models.encounter import EncounterModel
    from app.models.financial_reporting import ReportSnapshotModel
    for parameter, model, key in (("signal_id", SignalModel, SignalModel.id),
                                  ("event_id", OperationalEventModel, OperationalEventModel.id),
                                  ("encounter_id", EncounterModel, EncounterModel.id),
                                  ("snapshot_id", ReportSnapshotModel, ReportSnapshotModel.snapshot_id)):
        identifier = request.path_params.get(parameter)
        if identifier and db.scalar(select(model.clinic_id).where(key == identifier,
                                                                  model.clinic_id.in_(facilities)).limit(1)):
            raise HTTPException(403, "Record requires the hospital scoped API")
