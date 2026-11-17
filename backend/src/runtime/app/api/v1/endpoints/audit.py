from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from app.db.database import get_db
from app.schemas.audit import AuditLogCreate, AuditLogResponse
from app.models.audit import AuditLogModel
from app.models.user import UserRole
from app.api.deps import RoleChecker
from app.services.data_audit_engine import data_audit_engine

router = APIRouter()


@router.post("/", response_model=dict)
def create_audit_log(
    audit_in: AuditLogCreate,
    background_tasks: BackgroundTasks,
    current_user=Depends(RoleChecker([
        UserRole.CLINIC_MANAGER.value,
        UserRole.ORGANIZATION_ADMINISTRATOR.value,
        UserRole.SYSTEM_ADMINISTRATOR.value
    ]))
):
    """
    Creates an immutable audit log entry.
    Actor is bound to authenticated user identity to prevent spoofing.
    """
    if hasattr(current_user, "email") and current_user.email:
        audit_in.user_system = current_user.email
    background_tasks.add_task(data_audit_engine.log_audit_event, audit_in)
    return {"status": "accepted", "message": "Audit log queued for DMAE processing."}


@router.get("/", response_model=List[AuditLogResponse])
def get_audit_logs(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user=Depends(RoleChecker([
        UserRole.CLINIC_MANAGER.value,
        UserRole.ORGANIZATION_ADMINISTRATOR.value,
        UserRole.SYSTEM_ADMINISTRATOR.value
    ]))
):
    """
    Retrieves a list of recent audit logs. (Read-only access, role-governed)
    """
    from sqlalchemy import or_
    logs = db.query(AuditLogModel).filter(or_(AuditLogModel.module.is_(None),
        AuditLogModel.module != "Hospital")).order_by(
        AuditLogModel.timestamp.desc()).offset(skip).limit(limit).all()
    return logs
