"""MS-001 server-side sessions: start at login, touch on use, end on logout, idle time, password
change or reset, removed access and organization suspension."""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.models.identity import UserSessionModel
from app.models.user import User

ACTIVITY_EVERY = timedelta(seconds=60)   # last_seen_at = last recorded user activity, at most once a minute


def idle_limit() -> timedelta:
    return timedelta(minutes=max(1, int(getattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30))))


def start(db: Session, user: User, ip: Optional[str] = None, user_agent: Optional[str] = None):
    """A new session and its access token (the token names the session in its "sid" claim)."""
    now = datetime.utcnow()
    lifetime = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    row = UserSessionModel(user_id=user.id, created_at=now, last_seen_at=now, expires_at=now + lifetime,
                           ip_address=(ip or "")[:64] or None, user_agent=(user_agent or "")[:255] or None)
    db.add(row)
    db.flush()
    token = create_access_token(data={"sub": str(user.id), "role": user.role, "sid": row.id}, expires_delta=lifetime)
    return row, token


def end(db: Session, row: UserSessionModel, reason: str, at: Optional[datetime] = None):
    if row.ended_at is None:
        row.ended_at = at or datetime.utcnow()
        row.end_reason = reason


def end_all(db: Session, user_id: int, reason: str, keep: Optional[str] = None) -> int:
    rows = db.query(UserSessionModel).filter(UserSessionModel.user_id == user_id,
                                             UserSessionModel.ended_at.is_(None)).all()
    n = 0
    for r in rows:
        if r.id != keep:
            end(db, r, reason)
            n += 1
    return n


def check(db: Session, sid: str, user: User):
    """The live session for this token, or the reason it no longer authenticates. Reading never moves
    last activity: polling, push delivery and ordinary page loads leave an unattended session to
    expire. Idle sessions are ended here (and audited) the first time they are used after the limit."""
    row = db.query(UserSessionModel).filter(UserSessionModel.id == sid).first()
    if row is None or row.user_id != user.id:
        return None, "unknown_session"
    now = datetime.utcnow()
    if row.ended_at is not None:
        return None, row.end_reason or "ended"
    if row.expires_at and now >= row.expires_at:
        end(db, row, "expired", at=row.expires_at)
        db.commit()
        return None, "expired"
    if now - row.last_seen_at > idle_limit():
        end(db, row, "idle_timeout", at=row.last_seen_at + idle_limit())
        from app.core.access import record
        record(db, user, "SECURITY:SESSION_IDLE_TIMEOUT", "Authentication", target_type="session", target_id=row.id,
               detail=f"no activity for {int(idle_limit().total_seconds() // 60)} minutes")
        db.commit()
        return None, "idle_timeout"
    return row, None


def record_activity(db: Session, row: UserSessionModel) -> bool:
    """A person used the screen (typing, clicking): move last activity, at most once a minute."""
    now = datetime.utcnow()
    if now - row.last_seen_at >= ACTIVITY_EVERY:
        row.last_seen_at = now
        db.commit()
        return True
    return False


class SessionEnded(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate(db: Session, token: str):
    """(user, session) for a token, or SessionEnded. Used by HTTP requests and both WebSockets.
    A token without a server session (issued before migration r1) must sign in again."""
    try:
        from jose import JWTError, jwt
    except ImportError:  # pragma: no cover
        import jwt
        from jwt.exceptions import PyJWTError as JWTError
    try:
        payload = jwt.decode(token or "", settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise SessionEnded(401, "Could not validate credentials")
    user_id, sid = payload.get("sub"), payload.get("sid")
    if not user_id:
        raise SessionEnded(401, "Could not validate credentials")
    if not sid:
        raise SessionEnded(401, "Session ended (reauthenticate)")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise SessionEnded(401, "Could not validate credentials")
    if not user.is_active:
        raise SessionEnded(401, "Session ended (access_removed)")
    from app.models.user import canonical_role
    if canonical_role(user.role) != user.role:
        user.role = canonical_role(user.role)
    row, reason = check(db, sid, user)
    if row is None:
        raise SessionEnded(401, f"Session ended ({reason})")
    from app.core.access import org_is_suspended, is_platform
    if not is_platform(user) and org_is_suspended(db, user.org_id):
        raise SessionEnded(403, "Your organization is suspended")
    user._session_id = sid
    return user, row


def authenticate(db: Session, token: str) -> User:
    from fastapi import HTTPException
    try:
        user, _ = validate(db, token)
    except SessionEnded as e:
        headers = {"WWW-Authenticate": "Bearer"} if e.status_code == 401 else None
        raise HTTPException(status_code=e.status_code, detail=e.detail, headers=headers)
    return user


def view(row: UserSessionModel, current_sid: Optional[str] = None) -> dict:
    def iso(d):
        return d.isoformat() + "Z" if d else None
    active = row.ended_at is None and (row.expires_at is None or row.expires_at > datetime.utcnow())
    return {"id": row.id, "created_at": iso(row.created_at), "last_seen_at": iso(row.last_seen_at),
            "expires_at": iso(row.expires_at), "ended_at": iso(row.ended_at), "end_reason": row.end_reason,
            "ip_address": row.ip_address, "user_agent": row.user_agent, "active": active,
            "current": row.id == current_sid}
