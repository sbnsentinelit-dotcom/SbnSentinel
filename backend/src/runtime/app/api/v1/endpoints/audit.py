from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import RoleChecker
from app.core.access import AUDIT_READERS, is_org_wide, is_platform, record, require_clinic, require_org
from app.db.database import get_db
from app.models.audit import AuditLogModel
from app.models.user import UserRole
from app.schemas.audit import AuditLogCreate, AuditLogResponse

router = APIRouter()


@router.post("/", response_model=dict)
def create_audit_log(
    audit_in: AuditLogCreate,
    db: Session = Depends(get_db),
    current_user=Depends(RoleChecker([
        UserRole.CLINIC_ADMINISTRATOR.value,
        UserRole.ORGANIZATION_ADMINISTRATOR.value,
        UserRole.SYSTEM_ADMINISTRATOR.value
    ]))
):
    """
    Creates an immutable audit log entry for a user action on the screen.
    The actor and scope are the authenticated user's, never taken from the request.
    """
    row = record(db, current_user, audit_in.action, audit_in.module or audit_in.resource or "Audit",
                 correlation_id=audit_in.correlation_id)
    db.commit()
    return {"status": "recorded", "id": row.id}


@router.get("/", response_model=List[AuditLogResponse])
def get_audit_logs(
    skip: int = 0,
    limit: int = 100,
    org_id: Optional[str] = None,
    clinic_id: Optional[str] = None,
    action: Optional[str] = None,
    module: Optional[str] = None,
    actor: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user=Depends(RoleChecker(AUDIT_READERS))
):
    """
    Audit history in the caller's scope, newest first (read-only). A Platform Administrator sees every
    organization (or one, with org_id); an Organization Administrator or auditor their organization;
    a Clinic Administrator their clinic, plus their own account's events.
    """
    q = db.query(AuditLogModel)
    if is_platform(current_user):
        if org_id:
            q = q.filter(AuditLogModel.org_id == org_id)
    elif is_org_wide(current_user):
        q = q.filter(AuditLogModel.org_id == require_org(current_user))
    else:
        own_clinic = require_clinic(current_user)
        q = q.filter(AuditLogModel.org_id == require_org(current_user),
                     or_(AuditLogModel.clinic_id == own_clinic, AuditLogModel.user_system == current_user.email))
    if clinic_id:
        q = q.filter(AuditLogModel.clinic_id == clinic_id)
    if action:
        q = q.filter(AuditLogModel.action.ilike(f"%{action}%"))
    if module:
        q = q.filter(AuditLogModel.module == module)
    if actor:
        q = q.filter(AuditLogModel.user_system.ilike(f"%{actor}%"))
    if since:
        q = q.filter(AuditLogModel.timestamp >= since.replace(tzinfo=None))
    if until:
        q = q.filter(AuditLogModel.timestamp <= until.replace(tzinfo=None))
    limit = max(1, min(int(limit or 100), 500))
    return q.order_by(AuditLogModel.timestamp.desc()).offset(max(0, skip)).limit(limit).all()
