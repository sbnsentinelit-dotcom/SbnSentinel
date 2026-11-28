"""MS-001 Notification Center: the signals in the caller's scope as an inbox, with a read mark per
user. Two users see the same alert; when one marks it read, only that user's status changes."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.access import CLINIC_BOUND, is_platform, require_clinic, require_org, role_of
from app.db.database import get_db
from app.models.identity import NotificationReadModel
from app.models.signal import SignalModel
from app.models.user import User

router = APIRouter()


def _scoped(db: Session, user: User):
    q = db.query(SignalModel)
    if is_platform(user):
        return q
    q = q.filter(SignalModel.org_id == require_org(user))
    if role_of(user) in CLINIC_BOUND:
        q = q.filter(SignalModel.clinic_id == require_clinic(user))
    return q


def _signal(db: Session, user: User, signal_id: str) -> SignalModel:
    s = _scoped(db, user).filter(SignalModel.id == signal_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return s


def _iso(d):
    return d.isoformat() + "Z" if d else None


@router.get("")
def inbox(status: str = "all", risk_level: Optional[str] = None, type: Optional[str] = None, limit: int = 50,
          db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Notifications newest first. status: all | unread | read; filter by risk level or type."""
    if status not in ("all", "unread", "read"):
        raise HTTPException(status_code=400, detail="status must be all, unread or read")
    reads = {r.signal_id: r.read_at for r in
             db.query(NotificationReadModel).filter(NotificationReadModel.user_id == current_user.id).all()}
    q = _scoped(db, current_user)
    if risk_level:
        q = q.filter(SignalModel.risk_level == risk_level)
    if type:
        q = q.filter(SignalModel.type == type)
    total_unread = sum(1 for (sid,) in q.with_entities(SignalModel.id).all() if sid not in reads)
    if status == "unread":
        q = q.filter(~SignalModel.id.in_(list(reads) or ["__none__"]))
    elif status == "read":
        q = q.filter(SignalModel.id.in_(list(reads) or ["__none__"]))
    rows = q.order_by(SignalModel.timestamp.desc()).limit(max(1, min(int(limit or 50), 200))).all()
    return {
        "unread_count": total_unread,
        "items": [{
            "id": s.id, "source": s.source, "type": s.type, "message": s.message, "risk_level": s.risk_level,
            "timestamp": _iso(s.timestamp), "clinic_id": s.clinic_id,
            "read": s.id in reads, "read_at": _iso(reads.get(s.id)),
        } for s in rows],
    }


@router.post("/{signal_id}/read")
def mark_read(signal_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _signal(db, current_user, signal_id)
    row = db.query(NotificationReadModel).filter(NotificationReadModel.user_id == current_user.id,
                                                 NotificationReadModel.signal_id == signal_id).first()
    if row is None:
        row = NotificationReadModel(user_id=current_user.id, signal_id=signal_id, read_at=datetime.utcnow())
        db.add(row)
        try:
            db.commit()
        except IntegrityError:          # the same user marked it read at the same moment elsewhere
            db.rollback()
            row = db.query(NotificationReadModel).filter(NotificationReadModel.user_id == current_user.id,
                                                         NotificationReadModel.signal_id == signal_id).first()
    return {"id": signal_id, "read": True, "read_at": _iso(row.read_at)}


@router.post("/{signal_id}/unread")
def mark_unread(signal_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _signal(db, current_user, signal_id)
    db.query(NotificationReadModel).filter(NotificationReadModel.user_id == current_user.id,
                                           NotificationReadModel.signal_id == signal_id).delete()
    db.commit()
    return {"id": signal_id, "read": False, "read_at": None}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    have = {sid for (sid,) in db.query(NotificationReadModel.signal_id)
            .filter(NotificationReadModel.user_id == current_user.id).all()}
    now = datetime.utcnow()
    added = 0
    for (sid,) in _scoped(db, current_user).with_entities(SignalModel.id).all():
        if sid not in have:
            db.add(NotificationReadModel(user_id=current_user.id, signal_id=sid, read_at=now))
            added += 1
    db.commit()
    return {"marked": added}
