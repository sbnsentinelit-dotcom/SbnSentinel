"""
Live Patient Flow API. Reads never write: the snapshot is calculated from the clinic's visit
events. Staff actions are recorded as events or as actions with their connector receipts.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import RoleChecker
from app.db.database import get_db
from app.models.clinic import ClinicModel
from app.models.patient_flow import ClinicRoomModel
from app.models.user import User, UserRole
from app.services import patient_flow_service as flow

router = APIRouter()

_READ_ROLES = [UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
               UserRole.CLINIC_MANAGER.value, UserRole.FRONT_DESK.value, UserRole.READ_ONLY_AUDITOR.value]
_WRITE_ROLES = [UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
                UserRole.CLINIC_MANAGER.value, UserRole.FRONT_DESK.value]


def _clinic_for(user: User, db: Session, clinic_id: Optional[str]) -> str:
    """The clinic the request is about: the user's own clinic; a system administrator names one."""
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        if clinic_id:
            return clinic_id
        first = db.query(ClinicModel).order_by(ClinicModel.id).first()
        if not first:
            raise HTTPException(status_code=404, detail="No clinic configured")
        return first.id
    own = getattr(user, "clinic_id", None)
    if not own:
        raise HTTPException(status_code=403, detail="Missing required clinic scope")
    if clinic_id and clinic_id != own:
        raise HTTPException(status_code=403, detail="Cannot access another clinic's patient flow")
    return own


def _actor(user: User) -> str:
    return getattr(user, "email", None) or str(getattr(user, "id", "unknown"))


def _refused(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(status_code=409, detail=str(exc))


@router.get("")
@router.get("/")
def get_patient_flow(period: str = Query("Today"), department: Optional[str] = Query(None),
                     priority: Optional[str] = Query(None), clinic_id: Optional[str] = Query(None),
                     db: Session = Depends(get_db), current_user: User = Depends(RoleChecker(_READ_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    return flow.snapshot(db, clinic, period=period, department=department, priority=priority)


@router.get("/visits/{visit_id}/actions")
def get_visit_actions(visit_id: str, clinic_id: Optional[str] = Query(None), db: Session = Depends(get_db),
                      current_user: User = Depends(RoleChecker(_READ_ROLES))):
    return flow.visit_actions(db, _clinic_for(current_user, db, clinic_id), visit_id)


class StatusReport(BaseModel):
    status: str


@router.post("/visits/{visit_id}/status")
def post_visit_status(visit_id: str, body: StatusReport, clinic_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db), current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    try:
        return flow.report_status(db, clinic, visit_id, body.status, _actor(current_user))
    except (LookupError, flow.PatientFlowError) as exc:
        _refused(exc)


class RoomAssignment(BaseModel):
    room: str


@router.post("/visits/{visit_id}/room")
def post_visit_room(visit_id: str, body: RoomAssignment, clinic_id: Optional[str] = Query(None),
                    db: Session = Depends(get_db), current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    try:
        return flow.assign_room(db, clinic, visit_id, body.room, _actor(current_user))
    except (LookupError, flow.PatientFlowError) as exc:
        _refused(exc)


class ActionRequest(BaseModel):
    idempotency_key: Optional[str] = None


@router.post("/visits/{visit_id}/call")
def post_visit_call(visit_id: str, body: ActionRequest, clinic_id: Optional[str] = Query(None),
                    db: Session = Depends(get_db), current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    try:
        return flow.call_patient(db, clinic, visit_id, _actor(current_user), body.idempotency_key)
    except LookupError as exc:
        _refused(exc)


@router.post("/suggestions/{suggestion_id}/approve")
def post_suggestion_approve(suggestion_id: str, body: ActionRequest, clinic_id: Optional[str] = Query(None),
                            db: Session = Depends(get_db),
                            current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    try:
        return flow.act_on_suggestion(db, clinic, suggestion_id, "approve", _actor(current_user), body.idempotency_key)
    except flow.PatientFlowError as exc:
        _refused(exc)


@router.post("/suggestions/{suggestion_id}/dismiss")
def post_suggestion_dismiss(suggestion_id: str, body: ActionRequest, clinic_id: Optional[str] = Query(None),
                            db: Session = Depends(get_db),
                            current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    try:
        return flow.act_on_suggestion(db, clinic, suggestion_id, "dismiss", _actor(current_user), body.idempotency_key)
    except flow.PatientFlowError as exc:
        _refused(exc)


class RoomCreate(BaseModel):
    name: str
    kind: str = "consult"
    doctor: Optional[str] = None


@router.post("/rooms")
def post_room(body: RoomCreate, clinic_id: Optional[str] = Query(None), db: Session = Depends(get_db),
              current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Room name is required")
    flow.ensure_rooms(db, clinic)
    if db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic, ClinicRoomModel.name == name).first():
        raise HTTPException(status_code=409, detail=f"{name} already exists")
    order = db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic).count()
    room = ClinicRoomModel(clinic_id=clinic, name=name, kind="lab" if body.kind == "lab" else "consult",
                           doctor=body.doctor, sort_order=order)
    db.add(room)
    db.commit()
    return {"id": room.id, "name": room.name, "kind": room.kind, "doctor": room.doctor}


@router.delete("/rooms/{room_id}")
def delete_room(room_id: int, clinic_id: Optional[str] = Query(None), db: Session = Depends(get_db),
                current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    room = db.query(ClinicRoomModel).filter(ClinicRoomModel.id == room_id, ClinicRoomModel.clinic_id == clinic).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found in this clinic")
    snap_rooms = flow.snapshot(db, clinic)["rooms"]
    if any(r["id"] == room_id and r["status"] == "Occupied" for r in snap_rooms):
        raise HTTPException(status_code=409, detail=f"{room.name} is occupied")
    db.delete(room)
    db.commit()
    return {"deleted": room_id}


class RoomStatus(BaseModel):
    status: str


@router.post("/rooms/{room_id}/status")
def post_room_status(room_id: int, body: RoomStatus, clinic_id: Optional[str] = Query(None),
                     db: Session = Depends(get_db), current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    try:
        return flow.set_room_status(db, clinic, room_id, body.status, _actor(current_user))
    except (LookupError, flow.PatientFlowError) as exc:
        _refused(exc)


class RoomDoctor(BaseModel):
    doctor: Optional[str] = None


@router.post("/rooms/{room_id}/doctor")
def post_room_doctor(room_id: int, body: RoomDoctor, clinic_id: Optional[str] = Query(None),
                     db: Session = Depends(get_db), current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    clinic = _clinic_for(current_user, db, clinic_id)
    room = db.query(ClinicRoomModel).filter(ClinicRoomModel.id == room_id, ClinicRoomModel.clinic_id == clinic).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found in this clinic")
    room.doctor = body.doctor or None
    db.commit()
    return {"id": room.id, "name": room.name, "doctor": room.doctor}
