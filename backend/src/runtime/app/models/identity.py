"""MS-001 identity tables: server-side sessions and per-user notification read state."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.db.database import Base


class UserSessionModel(Base):
    """One signed-in session. Every access token carries its id ("sid"); a revoked or idle session
    no longer authenticates, whatever the token's own expiry says."""
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, index=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    # logout | idle_timeout | password_changed | password_reset | revoked | access_removed | org_suspended
    end_reason = Column(String(32), nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)


class NotificationReadModel(Base):
    """A user's read mark on one notification (a signal). Reading is per user: marking a shared
    alert read changes nothing for anyone else."""
    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("user_id", "signal_id", name="uq_notification_read"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, index=True, nullable=False)
    signal_id = Column(String, index=True, nullable=False)
    read_at = Column(DateTime, nullable=False, default=datetime.utcnow)
