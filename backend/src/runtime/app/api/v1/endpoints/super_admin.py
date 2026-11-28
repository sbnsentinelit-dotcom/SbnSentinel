from app.models.audit import AuditLogModel
from app.models.user import User, UserRole, ROLE_LABELS, canonical_role
from app.api.deps import RoleChecker
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from datetime import datetime

from app.db.database import get_db
from app.models.user import User  # noqa
from app.models.encounter import EncounterModel

router = APIRouter()


def require_super_admin(email: str, db: Session):
    """Simple super admin check - in production use JWT middleware."""
    user = db.query(User).filter(
        User.email == email,
        User.role == UserRole.SYSTEM_ADMINISTRATOR.value
    ).first()
    if not user:
        raise HTTPException(status_code=403, detail="Super admin access required.")
    return user


@router.get("/stats")
def get_platform_stats(db: Session = Depends(get_db)):
    """
    Platform-wide statistics for the Platform Administrator, counted from the database.
    """
    from app.models.clinic import ClinicModel
    from app.models.organization import OrganizationModel
    from app.core.access import CLINIC_BOUND
    users = db.query(User).filter(User.role != UserRole.SYSTEM_ADMINISTRATOR.value).all()
    encounters = db.query(EncounterModel.billing_status, EncounterModel.copay).all()

    paid = [c for s, c in encounters if s == "Paid"]
    platform_revenue = sum(c or 0 for c in paid)
    orgs = db.query(OrganizationModel).all()
    return {
        "total_organizations": len(orgs),
        "active_organizations": len([o for o in orgs if (o.status or "Active").lower() == "active"]),
        "suspended_organizations": len([o for o in orgs if (o.status or "").lower() == "suspended"]),
        "total_clinics": db.query(ClinicModel).count(),
        "active_clinics": db.query(ClinicModel).filter(ClinicModel.status == "Active").count(),
        "total_doctors": len([u for u in users if canonical_role(u.role) in CLINIC_BOUND]),
        "total_patients": len(encounters),
        "active_users": len([u for u in users if u.is_active]),
        "pending_invitations": len([u for u in users if not u.is_active and not u.hashed_password]),
        "paid_claims": len(paid),
        "pending_claims": len([1 for s, _ in encounters if s == "Pending"]),
        "denied_claims": len([1 for s, _ in encounters if s == "Claim Denied"]),
        "platform_revenue": round(platform_revenue, 2),
        "platform_revenue_formatted": f"${platform_revenue:,.2f}",
        # No usage meter is connected; the screen says so instead of showing an estimate.
        "ai_token_usage_today": "Not measured",
        "generated_at": datetime.utcnow().isoformat() + "Z"
    }


@router.get("/users")
def get_all_users(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    All users across the platform, with their organization and clinic.
    """
    from app.models.clinic import ClinicModel
    from app.models.organization import OrganizationModel
    clinics = {c.id: c for c in db.query(ClinicModel).all()}
    orgs = {o.id: o for o in db.query(OrganizationModel).all()}
    out = []
    for u in db.query(User).order_by(User.id).all():
        clinic = clinics.get(u.clinic_id)
        org = orgs.get(u.org_id)
        out.append({
            "id": u.id,
            "full_name": u.full_name or "Unknown",
            "email": u.email,
            "role": canonical_role(u.role),
            "role_label": ROLE_LABELS.get(canonical_role(u.role), u.role),
            "is_active": u.is_active,
            "status": "Pending" if (not u.is_active and not u.hashed_password) else ("Active" if u.is_active else "Inactive"),
            "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
            "org_id": u.org_id,
            "organization": org.name if org else None,
            "clinic_id": u.clinic_id,
            "clinic": clinic.name if clinic else (org.name if org else "Platform"),
        })
    return out


@router.patch("/users/{user_id}/toggle")
def toggle_user_status(user_id: int, db: Session = Depends(get_db),
                       actor: User = Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """Toggle active/inactive status of a user (audited; deactivation ends their sessions)."""
    from app.core.access import record
    from app.services import session_service
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        raise HTTPException(status_code=403, detail="Cannot deactivate super admin")
    if not user.is_active and not user.hashed_password:
        raise HTTPException(status_code=409, detail="This user has not accepted the invitation yet")
    user.is_active = not user.is_active
    user.updated_at = datetime.utcnow()
    if not user.is_active:
        user.token_invalid_before = datetime.utcnow()
        session_service.end_all(db, user.id, "access_removed")
    record(db, actor, "USER_ACCESS_RESTORED" if user.is_active else "USER_ACCESS_REVOKED", "Administration",
           org_id=user.org_id, clinic_id=user.clinic_id, target_type="user", target_id=user.id)
    db.commit()
    return {"id": user.id, "email": user.email, "is_active": user.is_active}


@router.post("/users/invite")
def invite_clinic_user(payload: Dict[str, Any], db: Session = Depends(get_db),
                       actor: User = Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    The Platform Administrator invites an organization's administrator (or another approved role).
    The organization is chosen by id, or created from its name when it does not exist yet. No password
    is set or shown: the user receives an invitation link, accepts the terms and chooses a password.
    """
    from datetime import timedelta
    from sqlalchemy import func
    from app.core import passwords
    from app.core.access import ASSIGNABLE, record
    from app.core.config import settings as app_config
    from app.models.organization import OrganizationModel
    from app.api.v1.endpoints.settings import _clinic_for_role, _member, _send_invitation

    email = (payload.get("email") or "").strip().lower()
    full_name = (payload.get("full_name") or "").strip() or "New Administrator"
    role = canonical_role(payload.get("role") or UserRole.ORGANIZATION_ADMINISTRATOR.value)
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email is required")
    if role not in ASSIGNABLE[UserRole.SYSTEM_ADMINISTRATOR.value]:
        raise HTTPException(status_code=400, detail=f"Invalid role {role}")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="User with this email already exists")

    org = None
    if payload.get("org_id"):
        org = db.query(OrganizationModel).filter(OrganizationModel.id == payload["org_id"]).first()
        if org is None:
            raise HTTPException(status_code=404, detail="Organization not found")
    else:
        org_name = (payload.get("organization") or payload.get("clinic_name") or "").strip()
        if not org_name:
            raise HTTPException(status_code=400, detail="Choose or name the organization")
        org = db.query(OrganizationModel).filter(func.lower(OrganizationModel.name) == org_name.lower()).first()
        if org is None:
            now = datetime.utcnow()
            org = OrganizationModel(name=org_name, status="Active", time_zone="UTC", created_date=now, updated_at=now)
            db.add(org)
            db.flush()
            record(db, actor, "ORGANIZATION_CREATED", "Organization", org_id=org.id, target_type="organization",
                   target_id=org.id, detail=f"name: {org_name!r} (with an invitation)")
    if (org.status or "").lower() == "suspended":
        raise HTTPException(status_code=409, detail="The organization is suspended")
    clinic_id = _clinic_for_role(db, actor, role, org.id, payload.get("clinic_id"))

    new_user = User(email=email, hashed_password="", full_name=full_name, role=role, is_active=False,
                    org_id=org.id, clinic_id=clinic_id, invited_by=actor.email, updated_at=datetime.utcnow())
    db.add(new_user)
    db.flush()
    _, code, token = passwords.issue(db, email, "invite",
                                     timedelta(hours=int(getattr(app_config, "INVITE_EXPIRE_HOURS", 72))))
    record(db, actor, "USER_INVITED", "Administration", org_id=org.id, clinic_id=clinic_id, target_type="user",
           target_id=new_user.id, detail=f"{email} as {role}")
    db.commit()
    db.refresh(new_user)
    sent = _send_invitation(new_user, code, token, actor)
    out = _member(db, new_user)
    out.update({"message": f"Invitation for {email} created", "user_id": new_user.id, "role": role,
                "organization": org.name, "org_id": org.id, "email_sent": sent})
    return out


@router.get("/encounters/summary")
def get_all_encounters_summary(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    Summary of all patient encounters across the platform.
    """
    encounters = db.query(EncounterModel).all()
    return [
        {
            "id": e.id,
            "patient_name": e.patient_name,
            "provider_name": e.provider_name,
            "department": e.department,
            "billing_status": e.billing_status,
            "copay": e.copay,
            "status": e.status,
            "date": e.date
        }
        for e in encounters
    ]


@router.get("/pending-approvals")
def get_pending_approvals():
    """Return pending clinic registrations."""
    return []


@router.get("/audit-logs")
def get_audit_logs(db: Session = Depends(get_db)):
    """Platform audit logs from the database: who did what, in which organization."""
    logs = db.query(AuditLogModel).order_by(AuditLogModel.timestamp.desc()).limit(50).all()
    return [
        {
            "id": log.id,
            "action": log.action,
            "resource": log.module or "-",
            "time": log.timestamp.isoformat() + "Z" if log.timestamp else "",
            "ip": log.user_system or "System",
            "actor": log.user_system or "System",
            "org_id": log.org_id,
            "clinic_id": log.clinic_id,
            "target": f"{log.target_type}:{log.target_id}" if log.target_type else None,
            "detail": log.detail,
        }
        for log in logs
    ]
