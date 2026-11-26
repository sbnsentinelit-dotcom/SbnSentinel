"""
Legacy Claims Continuity Bridge API (V3 candidate, behind LEGACY_BRIDGE_ENABLED).

Scope always comes from the signed-in user: a clinic user sees its own clinic's transitions, an
Organization Administrator its organization's, a System Administrator all. Sources deliver pages
only through a binding an administrator created. Every response is review material; nothing here
posts money, submits a claim or cancels a subscription.
"""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.legacy_bridge.intake import BindingIn, PageIn, TransitionIn
from app.models.legacy_bridge import LegacyExitInventoryModel, LegacyTransitionModel, LegacyWorkItemModel
from app.models.user import User, UserRole
from app.services import legacy_bridge_service as svc

router = APIRouter()

READ_ROLES = {UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
              UserRole.CLINIC_MANAGER.value, UserRole.READ_ONLY_AUDITOR.value}
WRITE_ROLES = {UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
               UserRole.CLINIC_MANAGER.value}
ADMIN_ROLES = {UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value}


def _fail(e: svc.BridgeError):
    raise HTTPException(status_code=e.status, detail=e.detail)


def _enabled():
    if not svc.bridge_enabled():
        raise HTTPException(status_code=404, detail="The Legacy Claims Bridge is not enabled in this deployment.")


def _reader(current_user: User = Depends(get_current_user)) -> User:
    _enabled()
    if current_user.role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot view legacy claims transitions.")
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value and not (current_user.clinic_id or current_user.org_id):
        raise HTTPException(status_code=403, detail="Missing required clinic scope")
    return current_user


def _writer(current_user: User = Depends(_reader)) -> User:
    if current_user.role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Your role cannot change legacy claims evidence.")
    return current_user


def _actor(user: User) -> str:
    return user.email or f"user-{user.id}"


def _scoped(db: Session, user: User):
    q = db.query(LegacyTransitionModel)
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        return q
    if user.role == UserRole.ORGANIZATION_ADMINISTRATOR.value and user.org_id:
        return q.filter(or_(LegacyTransitionModel.org_id == user.org_id,
                            LegacyTransitionModel.clinic_id == user.clinic_id))
    return q.filter(LegacyTransitionModel.clinic_id == (user.clinic_id or "UNASSIGNED_FORCE_FAIL"))


def _transition(db: Session, user: User, transition_id: str) -> LegacyTransitionModel:
    t = _scoped(db, user).filter(LegacyTransitionModel.id == transition_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transition not found in your scope.")
    return t


def _parse_instant(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="as_of must be an ISO 8601 instant")
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail="as_of needs an explicit UTC offset")
    return parsed


async def _body(request: Request, model):
    raw = await request.body()
    try:
        return model.model_validate_json(raw)
    except Exception as e:
        errors = getattr(e, "errors", None)
        detail = [{"loc": list(err.get("loc", ())), "msg": err.get("msg")} for err in errors()] if errors else str(e)
        raise HTTPException(status_code=422, detail=detail)


@router.get("/status")
def bridge_status(current_user: User = Depends(get_current_user)):
    enabled = svc.bridge_enabled()
    return {"enabled": enabled,
            "can_view": enabled and current_user.role in READ_ROLES,
            "can_deliver": enabled and current_user.role in WRITE_ROLES,
            "can_configure": enabled and current_user.role in ADMIN_ROLES,
            "stage": "V3 research candidate - review only"}


@router.get("/transitions")
def list_transitions(db: Session = Depends(get_db), user: User = Depends(_reader)):
    rows = _scoped(db, user).order_by(LegacyTransitionModel.created_at.desc()).all()
    return [svc.transition_view(t, svc.latest_assessment(db, t)) for t in rows]


@router.post("/transitions", status_code=201)
async def create_transition(request: Request, db: Session = Depends(get_db), user: User = Depends(_writer)):
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only administrators can open a transition.")
    data = await _body(request, TransitionIn)
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        clinic_id = data.clinic_id or user.clinic_id
        if not clinic_id:
            raise HTTPException(status_code=422, detail="clinic_id is required")
    else:
        if data.clinic_id and data.clinic_id != user.clinic_id:
            raise HTTPException(status_code=403, detail="You can only open transitions for your own clinic.")
        clinic_id = user.clinic_id
        if not clinic_id:
            raise HTTPException(status_code=403, detail="Missing required clinic scope")
    try:
        t = svc.create_transition(db, data, org_id=user.org_id or clinic_id, clinic_id=clinic_id, actor=_actor(user))
    except svc.BridgeError as e:
        _fail(e)
    return svc.transition_view(t)


@router.get("/transitions/{transition_id}")
def get_transition(transition_id: str, db: Session = Depends(get_db), user: User = Depends(_reader)):
    t = _transition(db, user, transition_id)
    return svc.transition_view(t, svc.latest_assessment(db, t))


@router.get("/transitions/{transition_id}/sources")
def list_sources(transition_id: str, db: Session = Depends(get_db), user: User = Depends(_reader)):
    """Bound sources by role. Source system names stay server-side."""
    t = _transition(db, user, transition_id)
    heads = {(h.connector, h.resource): h for h in svc.stream_heads(db, t)}
    out = []
    for b in svc.bindings(db, t):
        streams = [{"resource": res, "head": h.head_token, "updated_at": svc.iso(h.updated_at)}
                   for (conn, res), h in heads.items() if conn == b.connector]
        out.append({"binding_id": b.id, "role": b.role, "valid_from": svc.iso(b.valid_from),
                    "valid_until": svc.iso(b.valid_until), "streams": streams})
    return out


@router.post("/transitions/{transition_id}/sources", status_code=201)
async def bind_source(transition_id: str, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(_writer)):
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only administrators can bind a source.")
    t = _transition(db, user, transition_id)
    data = await _body(request, BindingIn)
    try:
        b = svc.add_binding(db, t, data, _actor(user))
    except svc.BridgeError as e:
        _fail(e)
    return {"binding_id": b.id, "role": b.role, "valid_from": svc.iso(b.valid_from),
            "valid_until": svc.iso(b.valid_until)}


@router.post("/transitions/{transition_id}/pages")
async def deliver_page(transition_id: str, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(_writer)):
    t = _transition(db, user, transition_id)
    page = await _body(request, PageIn)
    try:
        return svc.stage_page(db, t, page, _actor(user))
    except svc.BridgeError as e:
        db.rollback()
        _fail(e)


@router.get("/transitions/{transition_id}/assessment")
def get_assessment(transition_id: str, as_of: Optional[str] = Query(None), db: Session = Depends(get_db),
                   user: User = Depends(_reader)):
    """Assess the stored evidence as of an instant (default: the latest completeness statement)."""
    t = _transition(db, user, transition_id)
    try:
        a = svc.assess_transition(db, t, _actor(user), _parse_instant(as_of))
    except svc.BridgeError as e:
        _fail(e)
    return svc.assessment_view(db, t, a)


@router.get("/transitions/{transition_id}/assessments")
def list_assessments(transition_id: str, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db),
                     user: User = Depends(_reader)):
    t = _transition(db, user, transition_id)
    return svc.assessment_history(db, t, limit)


@router.post("/work-items/{item_id}/accept")
def accept_work_item(item_id: int, note: Optional[str] = Body(None, embed=True), db: Session = Depends(get_db),
                     user: User = Depends(_writer)):
    w = db.query(LegacyWorkItemModel).filter(LegacyWorkItemModel.id == item_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Work item not found.")
    _transition(db, user, w.transition_id)
    try:
        return svc.work_item_view(svc.accept_work_item(db, w, _actor(user), (note or "").strip() or None))
    except svc.BridgeError as e:
        _fail(e)


def _inventory_scope(user: User) -> Optional[List[str]]:
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        return None
    return [user.clinic_id] if user.clinic_id else []


@router.get("/exit-inventories")
def list_exit_inventories(db: Session = Depends(get_db), user: User = Depends(_reader)):
    rows = svc.exit_inventories(db, _inventory_scope(user))
    return [{"inventory_id": r.id, "subscription": r.subscription, "as_of": svc.iso(r.as_of),
             "received_at": svc.iso(r.received_at)} for r in rows]


@router.post("/exit-inventories", status_code=201)
async def deliver_exit_inventory(request: Request, db: Session = Depends(get_db), user: User = Depends(_writer)):
    if not user.clinic_id:
        raise HTTPException(status_code=403, detail="Missing required clinic scope")
    raw = await request.body()
    try:
        json.loads(raw)
        row = svc.store_exit_inventory(db, raw, org_id=user.org_id or user.clinic_id, clinic_id=user.clinic_id,
                                       actor=_actor(user))
    except ValueError:
        raise HTTPException(status_code=422, detail="The inventory must be JSON.")
    except svc.BridgeError as e:
        _fail(e)
    return {"inventory_id": row.id, "subscription": row.subscription, "as_of": svc.iso(row.as_of)}


@router.get("/exit-inventories/{inventory_id}/review")
def review_exit_inventory(inventory_id: str, db: Session = Depends(get_db), user: User = Depends(_reader)):
    scope = _inventory_scope(user)
    q = db.query(LegacyExitInventoryModel).filter(LegacyExitInventoryModel.id == inventory_id)
    if scope is not None:
        q = q.filter(LegacyExitInventoryModel.clinic_id.in_(scope))
    row = q.first()
    if not row:
        raise HTTPException(status_code=404, detail="Inventory not found in your scope.")
    return svc.exit_review(db, row)
