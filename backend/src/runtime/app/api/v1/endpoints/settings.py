from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.settings import SettingsModel
from app.models.user import User, UserRole, ROLE_LABELS, canonical_role
from app.api.deps import get_current_user, RoleChecker
from app.schemas.settings import SettingsUpdate, SettingsResponse
from app.core.email import send_email
from app.core.config import settings as app_config
from app.core import passwords
from app.core.access import (OPERATIONS_ROLES, SETTINGS_WRITERS, USER_ADMINS, CLINIC_BOUND, ORG_WIDE,
                             changed_fields, check_assignable, clinic_for, is_platform, record,
                             require_clinic, require_org, role_of, target_org, user_for)
from app.services import session_service

router = APIRouter()

SETTINGS_READ_ROLES = OPERATIONS_ROLES + [UserRole.READ_ONLY_AUDITOR.value]
_SETTING_FIELDS = tuple(SettingsUpdate.model_fields.keys())


# ---------------------------------------------------------------------------------------------------
# Configuration: one row per organization, and one per clinic that keeps its own practice details.
# ---------------------------------------------------------------------------------------------------

def _owner(db: Session, user: User, org_id: Optional[str], clinic_id: Optional[str]):
    """(org_id, clinic_id) of the configuration this caller reads or writes."""
    if role_of(user) in CLINIC_BOUND:
        own = require_clinic(user)
        if clinic_id and clinic_id != own:
            raise HTTPException(status_code=403, detail="Cross-scope access denied")
        return require_org(user), own
    org = target_org(user, org_id)
    if clinic_id:
        clinic = clinic_for(db, user, clinic_id)
        if clinic.org_id != org:
            raise HTTPException(status_code=403, detail="Cross-scope access denied")
        return org, clinic.id
    return org, None


def _row(db: Session, org_id: str, clinic_id: Optional[str], create: bool) -> Optional[SettingsModel]:
    row = db.query(SettingsModel).filter(SettingsModel.org_id == org_id, SettingsModel.clinic_id == clinic_id).first() \
        if clinic_id else db.query(SettingsModel).filter(SettingsModel.org_id == org_id,
                                                         SettingsModel.clinic_id.is_(None)).first()
    if row is not None or not create:
        return row
    # A clinic starts from its organization's configuration; an organization from its own name.
    base = _row(db, org_id, None, create=False) if clinic_id else None
    row = SettingsModel(org_id=org_id, clinic_id=clinic_id)
    if base is not None:
        for f in _SETTING_FIELDS:
            setattr(row, f, getattr(base, f))
    from app.models.clinic import ClinicModel
    from app.models.organization import OrganizationModel
    clinic = db.query(ClinicModel).filter(ClinicModel.id == clinic_id).first() if clinic_id else None
    org = db.query(OrganizationModel).filter(OrganizationModel.id == org_id).first()
    if clinic is not None:
        row.practice_name = clinic.name
        if clinic.phone:
            row.practice_phone = clinic.phone
    elif base is None and org is not None:
        row.practice_name = org.name
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return _row(db, org_id, clinic_id, create=False)
    db.refresh(row)
    return row


def _response(row: SettingsModel) -> SettingsModel:
    from app.core.config import get_deployment_currency
    row.currency = get_deployment_currency()
    row.deployment_region = getattr(app_config, "DEPLOYMENT_REGION", "US")
    row.scope = "clinic" if row.clinic_id else "organization"
    return row


@router.get("", response_model=SettingsResponse, dependencies=[Depends(RoleChecker(SETTINGS_READ_ROLES))])
def get_settings(org_id: Optional[str] = None, clinic_id: Optional[str] = None, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """The configuration of the caller's organization (or clinic, for clinic roles or with clinic_id)."""
    owner_org, owner_clinic = _owner(db, current_user, org_id, clinic_id)
    return _response(_row(db, owner_org, owner_clinic, create=True))


@router.post("", response_model=SettingsResponse, dependencies=[Depends(RoleChecker(SETTINGS_WRITERS))])
def update_settings(payload: SettingsUpdate, org_id: Optional[str] = None, clinic_id: Optional[str] = None,
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Update the configuration of the caller's organization or clinic; the change is audited."""
    owner_org, owner_clinic = _owner(db, current_user, org_id, clinic_id)
    row = _row(db, owner_org, owner_clinic, create=True)
    update_data = payload.model_dump(exclude_unset=True)
    before = {k: getattr(row, k) for k in update_data}
    for key, value in update_data.items():
        setattr(row, key, value)
    diff = changed_fields(before, {k: getattr(row, k) for k in update_data},
                          [k for k in update_data if k != "payment_card"])
    if "payment_card" in update_data and before.get("payment_card") != row.payment_card:
        diff = "; ".join(x for x in (diff, "payment_card changed") if x)
    if diff:
        record(db, current_user, "SETTINGS_UPDATED", "Configuration", org_id=owner_org, clinic_id=owner_clinic,
               target_type="clinic_settings" if owner_clinic else "organization_settings", target_id=row.id, detail=diff)
    db.commit()
    db.refresh(row)
    return _response(row)


# ---------------------------------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------------------------------

def _member(db: Session, u: User) -> Dict[str, Any]:
    from app.models.clinic import ClinicModel
    clinic = db.query(ClinicModel).filter(ClinicModel.id == u.clinic_id).first() if u.clinic_id else None
    pending = not u.is_active and not u.hashed_password
    return {
        "id": str(u.id),
        "name": u.full_name or "Unknown",
        "email": u.email,
        "role": canonical_role(u.role),
        "role_label": ROLE_LABELS.get(canonical_role(u.role), u.role),
        "org_id": u.org_id,
        "clinic_id": u.clinic_id,
        "clinic": clinic.name if clinic else None,
        "status": "Pending" if pending else ("Active" if u.is_active else "Inactive"),
    }


@router.get("/roles", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def assignable_roles(current_user: User = Depends(get_current_user)):
    """The roles this administrator may give, with whether each needs a clinic."""
    from app.core.access import ASSIGNABLE
    return [{"role": r, "label": ROLE_LABELS[r], "needs_clinic": r in CLINIC_BOUND}
            for r in ASSIGNABLE.get(role_of(current_user), [])]


@router.get("/team", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def get_team_members(org_id: Optional[str] = None, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Users the caller administers: the organization's (platform: all, or one organization's); a
    Clinic Administrator sees their clinic's."""
    query = db.query(User).filter(User.role != UserRole.SYSTEM_ADMINISTRATOR.value)
    if is_platform(current_user):
        if org_id:
            query = query.filter(User.org_id == org_id)
    else:
        query = query.filter(User.org_id == target_org(current_user, org_id))
        if role_of(current_user) == UserRole.CLINIC_ADMINISTRATOR.value:
            query = query.filter(User.clinic_id == require_clinic(current_user))
    return [_member(db, u) for u in query.order_by(User.id).all()]


def _clinic_for_role(db: Session, actor: User, role: str, org: str, clinic_id: Optional[str]) -> Optional[str]:
    """The clinic a user of this role is assigned to: clinic roles need one inside the organization
    (a Clinic Administrator can only assign their own); organization-wide roles have none."""
    if role in ORG_WIDE or role == UserRole.ORGANIZATION_ADMINISTRATOR.value:
        return None
    if role_of(actor) == UserRole.CLINIC_ADMINISTRATOR.value:
        own = require_clinic(actor)
        if clinic_id and clinic_id != own:
            raise HTTPException(status_code=403, detail="You can only assign users to your own clinic")
        return own
    from app.models.clinic import ClinicModel
    if not clinic_id:
        clinics = db.query(ClinicModel).filter(ClinicModel.org_id == org, ClinicModel.status == "Active").all()
        if len(clinics) != 1:
            raise HTTPException(status_code=400, detail=f"Choose the clinic for the {ROLE_LABELS.get(role, role)}")
        return clinics[0].id
    clinic = clinic_for(db, actor, clinic_id)
    if clinic.org_id != org:
        raise HTTPException(status_code=403, detail="Cross-scope access denied")
    if clinic.status != "Active":
        raise HTTPException(status_code=409, detail="The clinic is inactive")
    return clinic.id


def _send_invitation(user: User, code: str, token: str, inviter: User) -> bool:
    url = passwords.link("/", invite_token=token)
    hours = int(getattr(app_config, "INVITE_EXPIRE_HOURS", 72))
    html_content = f"""
    <html><body>
        <h2>Welcome to SBN Sentinel</h2>
        <p>Hi {user.full_name or ''},</p>
        <p>{inviter.full_name or inviter.email} invited you to SBN Sentinel as {ROLE_LABELS.get(canonical_role(user.role), user.role)}.</p>
        <p><a href="{url}">Accept the invitation</a>: you will accept the terms of use and choose your password.
        The link works once and expires in {hours} hours.</p>
        <p>Or choose "Activate Account" on the sign-in screen and enter this code: <strong>{code}</strong></p>
    </body></html>
    """
    return bool(send_email(to_email=user.email, subject="You are invited to SBN Sentinel", body=html_content, is_html=True))


class InviteIn(BaseModel):
    email: str
    name: Optional[str] = None
    role: Optional[str] = None
    clinic_id: Optional[str] = None
    org_id: Optional[str] = None


@router.post("/team/invite", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def invite_team_member(payload: InviteIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Invite a user into the caller's organization with one of the approved roles. The user receives a
    link to accept the terms and set a password; until then the account is pending."""
    email = (payload.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email is required")
    role = canonical_role(payload.role or UserRole.STAFF_USER.value)
    valid = [e.value for e in UserRole]
    if role not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of {valid}")
    check_assignable(current_user, role)
    org = target_org(current_user, payload.org_id)
    from app.core.access import org_is_suspended
    if org_is_suspended(db, org):
        raise HTTPException(status_code=409, detail="The organization is suspended")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    clinic_id = _clinic_for_role(db, current_user, role, org, payload.clinic_id)

    new_user = User(email=email, full_name=(payload.name or "").strip() or "New Staff", role=role, hashed_password="",
                    is_active=False, org_id=org, clinic_id=clinic_id, invited_by=current_user.email,
                    updated_at=datetime.utcnow())
    db.add(new_user)
    db.flush()
    _, code, token = passwords.issue(db, email, "invite",
                                     timedelta(hours=int(getattr(app_config, "INVITE_EXPIRE_HOURS", 72))))
    record(db, current_user, "USER_INVITED", "User Management", org_id=org, clinic_id=clinic_id, target_type="user",
           target_id=new_user.id, detail=f"{email} as {role}")
    db.commit()
    db.refresh(new_user)
    sent = _send_invitation(new_user, code, token, current_user)
    out = _member(db, new_user)
    out["email_sent"] = sent
    return out


@router.post("/team/{user_id}/resend-invite", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def resend_invitation(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    target = user_for(db, current_user, user_id)
    if target.is_active or target.hashed_password:
        raise HTTPException(status_code=409, detail="This user has already activated their account")
    _, code, token = passwords.issue(db, target.email, "invite",
                                     timedelta(hours=int(getattr(app_config, "INVITE_EXPIRE_HOURS", 72))))
    record(db, current_user, "USER_INVITE_RESENT", "User Management", org_id=target.org_id, clinic_id=target.clinic_id,
           target_type="user", target_id=target.id)
    db.commit()
    return {"email_sent": _send_invitation(target, code, token, current_user)}


class MemberUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    clinic_id: Optional[str] = None


@router.patch("/team/{user_id}", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def update_team_member(user_id: int, payload: MemberUpdate, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Change a user's name, role or clinic. A role can be removed by setting it to Unassigned."""
    target = user_for(db, current_user, user_id)
    data = payload.model_dump(exclude_unset=True)
    before = {"full_name": target.full_name, "role": canonical_role(target.role), "clinic_id": target.clinic_id}
    if "role" in data or "clinic_id" in data:
        if target.id == current_user.id:
            raise HTTPException(status_code=403, detail="You cannot change your own role or clinic")
        role = canonical_role(data.get("role") or target.role)
        if role != UserRole.UNASSIGNED.value:
            check_assignable(current_user, role)
        org = target.org_id or target_org(current_user, None)
        clinic_id = None if role == UserRole.UNASSIGNED.value else \
            _clinic_for_role(db, current_user, role, org, data.get("clinic_id", target.clinic_id))
        target.role = role
        target.clinic_id = clinic_id
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        target.full_name = name
    after = {"full_name": target.full_name, "role": canonical_role(target.role), "clinic_id": target.clinic_id}
    diff = changed_fields(before, after, ("full_name", "role", "clinic_id"))
    if diff:
        target.updated_at = datetime.utcnow()
        action = "USER_ROLE_REMOVED" if after["role"] == UserRole.UNASSIGNED.value and before["role"] != after["role"] \
            else ("USER_ROLE_CHANGED" if before["role"] != after["role"] else "USER_UPDATED")
        if before["role"] != after["role"]:
            # New permissions apply from the next sign-in: open sessions end.
            session_service.end_all(db, target.id, "role_changed")
        record(db, current_user, action, "User Management", org_id=target.org_id, clinic_id=target.clinic_id,
               target_type="user", target_id=target.id, detail=diff)
    db.commit()
    return _member(db, target)


@router.post("/team/{user_id}/reset-password", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def admin_reset_password(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Send the user a password reset link. The administrator never sees or sets the password."""
    target = user_for(db, current_user, user_id)
    if not target.is_active:
        raise HTTPException(status_code=409, detail="This user is not active")
    _, code, token = passwords.issue(db, target.email, "reset_password",
                                     timedelta(minutes=app_config.OTP_EXPIRE_MINUTES))
    record(db, current_user, "USER_PASSWORD_RESET_SENT", "User Management", org_id=target.org_id,
           clinic_id=target.clinic_id, target_type="user", target_id=target.id)
    db.commit()
    url = passwords.link("/", reset_token=token)
    sent = bool(send_email(target.email, "SBN Sentinel - Reset Password", f"""
    <h2>SBN Sentinel Reset Password</h2>
    <p>An administrator asked for your password to be reset.</p>
    <p><a href="{url}">Choose a new password</a> (the link works once and expires in {app_config.OTP_EXPIRE_MINUTES} minutes),
    or enter this code in the app: <strong>{code}</strong></p>""", is_html=True))
    return {"email_sent": sent}


@router.delete("/team/{user_id}", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def revoke_team_member(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Revoke access for a team member: the account is deactivated and its sessions end."""
    user = user_for(db, current_user, user_id)
    if is_platform(user):
        raise HTTPException(status_code=403, detail="Cannot revoke super admin")
    if user.id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot revoke your own access")
    user.is_active = False
    user.token_invalid_before = datetime.utcnow()
    user.updated_at = datetime.utcnow()
    ended = session_service.end_all(db, user.id, "access_removed")
    record(db, current_user, "USER_ACCESS_REVOKED", "User Management", org_id=user.org_id, clinic_id=user.clinic_id,
           target_type="user", target_id=user.id, detail=f"{ended} session(s) ended")
    db.commit()
    return {"message": "Access revoked successfully"}


@router.post("/team/{user_id}/reactivate", dependencies=[Depends(RoleChecker(USER_ADMINS))])
def reactivate_team_member(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = user_for(db, current_user, user_id)
    if user.is_active:
        raise HTTPException(status_code=409, detail="This user is already active")
    if not user.hashed_password:
        raise HTTPException(status_code=409, detail="This user has not accepted the invitation yet")
    user.is_active = True
    user.updated_at = datetime.utcnow()
    record(db, current_user, "USER_ACCESS_RESTORED", "User Management", org_id=user.org_id, clinic_id=user.clinic_id,
           target_type="user", target_id=user.id)
    db.commit()
    return _member(db, user)


# ---------------------------------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------------------------------

@router.get("/integrations", dependencies=[Depends(RoleChecker(SETTINGS_WRITERS))])
def get_integrations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Get clinic integrations status based on actual connector runtime state."""
    if getattr(app_config, "SOURCE_PROFILE", "") == "workbook" or not getattr(app_config, "ENABLE_PRACTICE_FUSION", False):
        return []
    from app.services.connector_manager import connector_runtime_state

    pf_state = connector_runtime_state.get("PRACTICE_FUSION")
    if not pf_state:
        return []
    return [
        {
            "id": "PRACTICE_FUSION",
            "name": "Practice Fusion EHR",
            "type": "EHR",
            "connected": pf_state.capability_state == "AUTHORIZED_READY" and not pf_state.is_stale,
            "lastSync": pf_state.last_verified_at.isoformat() + "Z" if pf_state.last_verified_at else "Never"
        }
    ]


@router.post("/integrations/{integration_id}/toggle", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value]))])
def toggle_integration(integration_id: str, db: Session = Depends(get_db)):
    """Toggle the connected status of an integration. Removed per F-20 (No shadow state allowed)."""
    raise HTTPException(status_code=403, detail="Integrations cannot be manually toggled. They are governed by actual connector state.")


@router.post("/send-sms-reminder")
def trigger_sms_reminder(payload: Dict[str, Any]):
    """
    Trigger automated 24-hour appointment reminder SMS via Twilio / Outreach Engine.
    """
    raise HTTPException(status_code=501, detail="Outbound SMS is disabled in V1 per security audit (F-01).")


@router.post("/send-email-report")
def trigger_email_report(payload: Dict[str, Any] = None):
    """
    Trigger automated Daily Secure Email Executive Report via Gmail SMTP Gateway.
    """
    raise HTTPException(status_code=501, detail="Outbound Email is disabled in V1 per security audit (F-01).")
