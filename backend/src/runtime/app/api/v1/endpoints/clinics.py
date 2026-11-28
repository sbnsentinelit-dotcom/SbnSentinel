"""Clinic management (MS-001): every clinic belongs to one organization. A Platform Administrator sees
all (or one organization's); an Organization Administrator or auditor their organization's; a
clinic-bound role only its own clinic. Creation is for Platform and Organization Administrators;
a Clinic Administrator may edit their own clinic. Every change is audited."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import RoleChecker, get_current_user
from app.core.access import (CLINIC_CREATORS, CLINIC_EDITORS, changed_fields, clinic_for, record, target_org,
                             visible_clinic_ids)
from app.db.database import get_db
from app.models.clinic import ClinicModel
from app.models.user import User

router = APIRouter()


def _iso(d):
    return d.isoformat() + "Z" if d else None


def _sources(db: Session, clinic: ClinicModel) -> List[Dict[str, Any]]:
    """What this clinic's data actually comes from: recorded imports only (PR #8 A8). Records typed in
    by hand are not an import. A clinic with no recorded workbook import shows it as not configured."""
    from app.models.integration_evidence import DataImportModel
    rows = db.query(DataImportModel.source_kind, DataImportModel.source_label,
                    func.sum(DataImportModel.accepted_count), func.max(DataImportModel.completed_at),
                    func.count(DataImportModel.id)) \
        .filter(DataImportModel.clinic_id == clinic.id) \
        .group_by(DataImportModel.source_kind, DataImportModel.source_label).all()
    sources = [{"kind": kind, "label": label, "sha256": None, "sheets": None, "records_count": int(total or 0),
                "imports": int(n), "last_import_at": _iso(last), "status": "Imported"}
               for kind, label, total, last, n in rows]
    if not any(s["kind"] == "workbook" for s in sources):
        sources.insert(0, {"kind": "workbook", "label": "Clinic billing workbook", "sha256": None, "sheets": None,
                           "records_count": 0, "imports": 0, "last_import_at": None, "status": "Not configured"})
    return sources


def _external(db: Session, clinic: ClinicModel) -> List[Dict[str, Any]]:
    """The clinic's registered connections, with what their newest provider check proved."""
    from app.models.connector import ConnectorModel
    from app.api.v1.endpoints.reality_connectors import connector_view
    return [connector_view(db, c) for c in db.query(ConnectorModel).filter(ConnectorModel.clinic_id == clinic.id).all()]


def view(db: Session, c: ClinicModel) -> Dict[str, Any]:
    from app.models.organization import OrganizationModel
    org = db.query(OrganizationModel).filter(OrganizationModel.id == c.org_id).first() if c.org_id else None
    return {
        "id": str(c.id),
        "name": c.name,
        "address": c.address,
        "phone": c.phone,
        "status": c.status or "Active",
        "org_id": c.org_id,
        "organization": org.name if org else None,
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
        "deactivated_at": _iso(c.deactivated_at),
        "data_sources": _sources(db, c),
        "external_connectors": _external(db, c),
    }


@router.get("/", response_model=List[Dict[str, Any]])
def get_clinics(
    org_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Clinics in the caller's scope."""
    ids = visible_clinic_ids(db, current_user, org_id)
    q = db.query(ClinicModel)
    if ids is not None:
        q = q.filter(ClinicModel.id.in_(ids or ["__none__"]))
    return [view(db, c) for c in q.order_by(ClinicModel.name).all()]


@router.get("/{clinic_id}")
def get_clinic(clinic_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return view(db, clinic_for(db, current_user, clinic_id))


class ClinicIn(BaseModel):
    name: str
    address: Optional[str] = ""
    phone: Optional[str] = ""
    org_id: Optional[str] = None      # a Platform Administrator names the organization


class ClinicUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None


class ClinicStatus(BaseModel):
    status: str          # Active | Inactive
    reason: Optional[str] = None


def _duplicate(db: Session, org_id: str, name: str, except_id: Optional[str] = None) -> bool:
    q = db.query(ClinicModel).filter(ClinicModel.org_id == org_id, func.lower(ClinicModel.name) == name.lower())
    if except_id:
        q = q.filter(ClinicModel.id != except_id)
    return db.query(q.exists()).scalar()


@router.post("/", status_code=201)
def create_clinic(payload: ClinicIn, db: Session = Depends(get_db),
                  actor: User = Depends(RoleChecker(CLINIC_CREATORS))):
    """Create a clinic inside an organization."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Clinic name is required")
    org_id = target_org(actor, payload.org_id)
    from app.models.organization import OrganizationModel
    org = db.query(OrganizationModel).filter(OrganizationModel.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if (org.status or "").lower() == "suspended":
        raise HTTPException(status_code=409, detail="The organization is suspended")
    if _duplicate(db, org_id, name):
        raise HTTPException(status_code=409, detail="A clinic with this name already exists in the organization")
    now = datetime.utcnow()
    clinic = ClinicModel(name=name, address=(payload.address or "").strip(), phone=(payload.phone or "").strip(),
                         status="Active", org_id=org_id, created_at=now, updated_at=now)
    db.add(clinic)
    db.flush()
    record(db, actor, "CLINIC_CREATED", "Clinic Management", org_id=org_id, clinic_id=clinic.id,
           target_type="clinic", target_id=clinic.id, detail=f"name: {name!r}")
    db.commit()
    db.refresh(clinic)
    return view(db, clinic)


@router.patch("/{clinic_id}")
def update_clinic(clinic_id: str, payload: ClinicUpdate, db: Session = Depends(get_db),
                  actor: User = Depends(RoleChecker(CLINIC_EDITORS))):
    clinic = clinic_for(db, actor, clinic_id, write=True)
    fields = ("name", "address", "phone")
    before = {f: getattr(clinic, f) for f in fields}
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Clinic name is required")
        if _duplicate(db, clinic.org_id, name, except_id=clinic.id):
            raise HTTPException(status_code=409, detail="A clinic with this name already exists in the organization")
        clinic.name = name
    for f in ("address", "phone"):
        if f in data:
            setattr(clinic, f, (data[f] or "").strip())
    diff = changed_fields(before, {f: getattr(clinic, f) for f in fields}, fields)
    if diff:
        clinic.updated_at = datetime.utcnow()
        record(db, actor, "CLINIC_UPDATED", "Clinic Management", org_id=clinic.org_id, clinic_id=clinic.id,
               target_type="clinic", target_id=clinic.id, detail=diff)
    db.commit()
    return view(db, clinic)


@router.post("/{clinic_id}/status")
def set_clinic_status(clinic_id: str, payload: ClinicStatus, db: Session = Depends(get_db),
                      actor: User = Depends(RoleChecker(CLINIC_CREATORS))):
    """Activate or deactivate a clinic (persisted; the clinic and its history stay)."""
    clinic = clinic_for(db, actor, clinic_id, write=True)
    status = (payload.status or "").strip().capitalize()
    if status not in ("Active", "Inactive"):
        raise HTTPException(status_code=400, detail="Status must be Active or Inactive")
    if (clinic.status or "Active") == status:
        raise HTTPException(status_code=409, detail=f"Clinic is already {status}")
    now = datetime.utcnow()
    clinic.status = status
    clinic.deactivated_at = now if status == "Inactive" else None
    clinic.updated_at = now
    record(db, actor, "CLINIC_ACTIVATED" if status == "Active" else "CLINIC_DEACTIVATED", "Clinic Management",
           org_id=clinic.org_id, clinic_id=clinic.id, target_type="clinic", target_id=clinic.id,
           detail=(payload.reason or "").strip() or None)
    db.commit()
    return view(db, clinic)
