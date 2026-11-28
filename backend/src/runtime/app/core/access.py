"""MS-001 tenant scope and administrative audit.

Who may see and change what, in one place:

* Platform Administrator (stored as "System Administrator"): every organization.
* Organization Administrator and Read-Only Auditor: their own organization and all of its clinics.
* Clinic Administrator, Operations Manager, Practice Manager and Staff User: their own clinic only.

Writes are narrower than reads (see the role sets below). Every administrative change is recorded in
the audit log with its actor, action and scope via record().
"""
from datetime import datetime
from typing import Iterable, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User, UserRole, canonical_role

R = UserRole
PLATFORM = {R.SYSTEM_ADMINISTRATOR.value}
ORG_WIDE = {R.ORGANIZATION_ADMINISTRATOR.value, R.READ_ONLY_AUDITOR.value}
CLINIC_BOUND = {R.CLINIC_ADMINISTRATOR.value, R.OPERATIONS_MANAGER.value, R.PRACTICE_MANAGER.value,
                R.STAFF_USER.value}

# Role sets used by the route guards.
USER_ADMINS = [R.SYSTEM_ADMINISTRATOR.value, R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value]
CLINIC_CREATORS = [R.SYSTEM_ADMINISTRATOR.value, R.ORGANIZATION_ADMINISTRATOR.value]
CLINIC_EDITORS = [R.SYSTEM_ADMINISTRATOR.value, R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value]
SETTINGS_READERS = [R.SYSTEM_ADMINISTRATOR.value, R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value]
SETTINGS_WRITERS = SETTINGS_READERS
AUDIT_READERS = [R.SYSTEM_ADMINISTRATOR.value, R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value,
                 R.READ_ONLY_AUDITOR.value]
OPERATIONS_ROLES = [R.SYSTEM_ADMINISTRATOR.value, R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value,
                    R.OPERATIONS_MANAGER.value, R.PRACTICE_MANAGER.value, R.STAFF_USER.value]

# Roles an administrator may give: an Organization Administrator any role below the platform; a Clinic
# Administrator the operational roles of their own clinic.
ASSIGNABLE = {
    R.SYSTEM_ADMINISTRATOR.value: [R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value,
                                   R.OPERATIONS_MANAGER.value, R.PRACTICE_MANAGER.value, R.STAFF_USER.value,
                                   R.READ_ONLY_AUDITOR.value],
    R.ORGANIZATION_ADMINISTRATOR.value: [R.ORGANIZATION_ADMINISTRATOR.value, R.CLINIC_ADMINISTRATOR.value,
                                         R.OPERATIONS_MANAGER.value, R.PRACTICE_MANAGER.value, R.STAFF_USER.value,
                                         R.READ_ONLY_AUDITOR.value],
    R.CLINIC_ADMINISTRATOR.value: [R.OPERATIONS_MANAGER.value, R.PRACTICE_MANAGER.value, R.STAFF_USER.value],
}
CLINIC_REQUIRED = CLINIC_BOUND


def role_of(user: User) -> str:
    return canonical_role(user.role)


def is_platform(user: User) -> bool:
    return role_of(user) in PLATFORM


def is_org_wide(user: User) -> bool:
    return role_of(user) in ORG_WIDE


def require_org(user: User) -> str:
    """The caller's organization; a non-platform user without one has no scope at all."""
    if not user.org_id or user.org_id == "UNASSIGNED":
        raise HTTPException(status_code=403, detail="Missing required organization scope")
    return user.org_id


def require_clinic(user: User) -> str:
    if not user.clinic_id:
        raise HTTPException(status_code=403, detail="Missing required clinic scope")
    return user.clinic_id


def target_org(user: User, org_id: Optional[str]) -> str:
    """The organization a request acts on: a platform user names it; anyone else acts on their own,
    and naming another organization is refused."""
    if is_platform(user):
        if not org_id:
            raise HTTPException(status_code=400, detail="Choose an organization (org_id)")
        return org_id
    own = require_org(user)
    if org_id and org_id != own:
        raise HTTPException(status_code=403, detail="Cross-scope access denied")
    return own


def visible_clinic_ids(db: Session, user: User, org_id: Optional[str] = None) -> Optional[List[str]]:
    """Clinic ids the caller may see; None means every clinic (platform without an organization filter)."""
    from app.models.clinic import ClinicModel
    if is_platform(user):
        if not org_id:
            return None
        return [c for (c,) in db.query(ClinicModel.id).filter(ClinicModel.org_id == org_id).all()]
    own = require_org(user)
    if org_id and org_id != own:
        raise HTTPException(status_code=403, detail="Cross-scope access denied")
    if is_org_wide(user):
        return [c for (c,) in db.query(ClinicModel.id).filter(ClinicModel.org_id == own).all()]
    return [require_clinic(user)]


def clinic_for(db: Session, user: User, clinic_id: str, write: bool = False):
    """A clinic the caller may see (or change, with write=True); 404 when it does not exist or is out of scope."""
    from app.models.clinic import ClinicModel
    clinic = db.query(ClinicModel).filter(ClinicModel.id == clinic_id).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    if is_platform(user):
        return clinic
    own = require_org(user)
    role = role_of(user)
    in_scope = clinic.org_id == own and (role in ORG_WIDE or clinic.id == user.clinic_id)
    if not in_scope:
        raise HTTPException(status_code=404, detail="Clinic not found")
    if write and role not in CLINIC_EDITORS:
        raise HTTPException(status_code=403, detail="Your role cannot change clinics")
    return clinic


def check_assignable(actor: User, role: str):
    allowed = ASSIGNABLE.get(role_of(actor), [])
    if canonical_role(role) not in allowed:
        raise HTTPException(status_code=403, detail=f"Your role cannot assign the role {role}")


def user_for(db: Session, actor: User, user_id: int) -> User:
    """A user the caller administers: same organization (platform: any), and for a Clinic
    Administrator the same clinic. Never the platform administrator."""
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if is_platform(target) and not is_platform(actor):
        raise HTTPException(status_code=404, detail="User not found")
    if not is_platform(actor):
        own = require_org(actor)
        if target.org_id != own:
            raise HTTPException(status_code=404, detail="User not found")
        if role_of(actor) == R.CLINIC_ADMINISTRATOR.value and target.clinic_id != actor.clinic_id:
            raise HTTPException(status_code=404, detail="User not found")
    from app.models.user import role_rank
    if target.id != actor.id and role_rank(target.role) > role_rank(actor.role):
        raise HTTPException(status_code=403, detail="Cannot manage a user with a higher role")
    return target


def org_is_suspended(db: Session, org_id: Optional[str]) -> bool:
    if not org_id:
        return False
    from app.models.organization import OrganizationModel
    org = db.query(OrganizationModel).filter(OrganizationModel.id == org_id).first()
    return bool(org and (org.status or "").lower() == "suspended")


def record(db: Session, actor, action: str, module: str, *, org_id: Optional[str] = None,
           clinic_id: Optional[str] = None, target_type: Optional[str] = None, target_id=None,
           detail: Optional[str] = None, correlation_id: Optional[str] = None):
    """Add one audit row in the caller's transaction (it commits with the change it describes).
    The scope defaults to the actor's organization and clinic."""
    from app.models.audit import AuditLogModel
    if isinstance(actor, User):
        who = actor.email
        org_id = org_id if org_id is not None else actor.org_id
        clinic_id = clinic_id if clinic_id is not None else (None if is_org_wide(actor) or is_platform(actor)
                                                             else actor.clinic_id)
    else:
        who = str(actor or "System")
    row = AuditLogModel(user_system=who, action=action, module=module, correlation_id=correlation_id,
                        org_id=org_id, clinic_id=clinic_id, target_type=target_type,
                        target_id=None if target_id is None else str(target_id), detail=detail,
                        scope_source="event", timestamp=datetime.utcnow())
    db.add(row)
    return row


def changed_fields(before: dict, after: dict, fields: Iterable[str]) -> str:
    """'field: old -> new' for each changed field (never values of secrets)."""
    parts = []
    for f in fields:
        if before.get(f) != after.get(f):
            parts.append(f"{f}: {before.get(f)!r} -> {after.get(f)!r}")
    return "; ".join(parts)
