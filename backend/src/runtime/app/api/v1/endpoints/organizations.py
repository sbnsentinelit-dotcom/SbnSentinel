"""MS-001 Organization management: the Platform Administrator creates, views, edits, suspends and
activates organizations. An Organization Administrator can read their own organization."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import RoleChecker, get_current_user
from app.core.access import changed_fields, is_platform, record, require_org
from app.db.database import get_db
from app.models.clinic import ClinicModel
from app.models.organization import OrganizationModel
from app.models.user import User, UserRole
from app.services import session_service

router = APIRouter()
platform_only = RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value])


def _iso(d):
    return d.isoformat() + "Z" if d else None


def view(db: Session, org: OrganizationModel) -> dict:
    clinics = db.query(func.count(ClinicModel.id)).filter(ClinicModel.org_id == org.id).scalar() or 0
    active_clinics = db.query(func.count(ClinicModel.id)).filter(ClinicModel.org_id == org.id,
                                                                 ClinicModel.status == "Active").scalar() or 0
    users = db.query(func.count(User.id)).filter(User.org_id == org.id).scalar() or 0
    return {"id": org.id, "name": org.name, "status": org.status or "Active", "time_zone": org.time_zone,
            "created_at": _iso(org.created_date), "updated_at": _iso(org.updated_at),
            "suspended_at": _iso(org.suspended_at), "suspended_reason": org.suspended_reason,
            "clinics": clinics, "active_clinics": active_clinics, "users": users}


def _org(db: Session, org_id: str) -> OrganizationModel:
    org = db.query(OrganizationModel).filter(OrganizationModel.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _name_taken(db: Session, name: str, except_id: Optional[str] = None) -> bool:
    q = db.query(OrganizationModel).filter(func.lower(OrganizationModel.name) == name.strip().lower())
    if except_id:
        q = q.filter(OrganizationModel.id != except_id)
    return db.query(q.exists()).scalar()


class OrganizationIn(BaseModel):
    name: str
    time_zone: Optional[str] = "UTC"


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    time_zone: Optional[str] = None


class StatusChange(BaseModel):
    reason: Optional[str] = None


@router.get("")
def list_organizations(status: Optional[str] = None, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    q = db.query(OrganizationModel)
    if not is_platform(current_user):
        q = q.filter(OrganizationModel.id == require_org(current_user))
    if status:
        q = q.filter(func.lower(OrganizationModel.status) == status.lower())
    return [view(db, o) for o in q.order_by(OrganizationModel.name).all()]


@router.post("", status_code=201)
def create_organization(payload: OrganizationIn, db: Session = Depends(get_db), actor: User = Depends(platform_only)):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")
    if _name_taken(db, name):
        raise HTTPException(status_code=409, detail="An organization with this name already exists")
    now = datetime.utcnow()
    org = OrganizationModel(name=name, status="Active", time_zone=payload.time_zone or "UTC",
                            created_date=now, updated_at=now)
    db.add(org)
    db.flush()
    record(db, actor, "ORGANIZATION_CREATED", "Organization", org_id=org.id, target_type="organization",
           target_id=org.id, detail=f"name: {name!r}")
    db.commit()
    return view(db, org)


@router.get("/{org_id}")
def get_organization(org_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_platform(current_user) and org_id != require_org(current_user):
        raise HTTPException(status_code=404, detail="Organization not found")
    return view(db, _org(db, org_id))


@router.patch("/{org_id}")
def update_organization(org_id: str, payload: OrganizationUpdate, db: Session = Depends(get_db),
                        actor: User = Depends(platform_only)):
    org = _org(db, org_id)
    before = {"name": org.name, "time_zone": org.time_zone}
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Organization name is required")
        if _name_taken(db, name, except_id=org.id):
            raise HTTPException(status_code=409, detail="An organization with this name already exists")
        org.name = name
    if "time_zone" in data and data["time_zone"]:
        org.time_zone = data["time_zone"]
    diff = changed_fields(before, {"name": org.name, "time_zone": org.time_zone}, ("name", "time_zone"))
    if diff:
        org.updated_at = datetime.utcnow()
        record(db, actor, "ORGANIZATION_UPDATED", "Organization", org_id=org.id, target_type="organization",
               target_id=org.id, detail=diff)
    db.commit()
    return view(db, org)


@router.post("/{org_id}/suspend")
def suspend_organization(org_id: str, payload: StatusChange, db: Session = Depends(get_db),
                         actor: User = Depends(platform_only)):
    """Suspends an organization: its users can no longer sign in and their open sessions end."""
    org = _org(db, org_id)
    if (org.status or "").lower() == "suspended":
        raise HTTPException(status_code=409, detail="Organization is already suspended")
    now = datetime.utcnow()
    org.status = "Suspended"
    org.suspended_at = now
    org.suspended_reason = (payload.reason or "").strip() or None
    org.updated_at = now
    ended = 0
    for (uid,) in db.query(User.id).filter(User.org_id == org.id).all():
        ended += session_service.end_all(db, uid, "org_suspended")
    record(db, actor, "ORGANIZATION_SUSPENDED", "Organization", org_id=org.id, target_type="organization",
           target_id=org.id, detail=f"reason: {org.suspended_reason!r}; {ended} session(s) ended")
    db.commit()
    return view(db, org)


@router.post("/{org_id}/activate")
def activate_organization(org_id: str, db: Session = Depends(get_db), actor: User = Depends(platform_only)):
    org = _org(db, org_id)
    if (org.status or "Active").lower() == "active":
        raise HTTPException(status_code=409, detail="Organization is already active")
    org.status = "Active"
    org.suspended_at = None
    org.suspended_reason = None
    org.updated_at = datetime.utcnow()
    record(db, actor, "ORGANIZATION_ACTIVATED", "Organization", org_id=org.id, target_type="organization",
           target_id=org.id)
    db.commit()
    return view(db, org)
