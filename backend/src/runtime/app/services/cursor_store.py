from sqlalchemy import Column, String, Integer, Boolean, UniqueConstraint
from app.db.database import Base, SessionLocal
from datetime import datetime, timezone
from typing import Optional


class CursorModel(Base):
    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("connector_id", "resource_type", "clinic_id", name="uq_cursor_connector_resource_clinic"),
    )
    id = Column(Integer, primary_key=True, index=True)
    connector_id = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False)
    checkpoint = Column(String, nullable=False)
    clinic_id = Column(String, nullable=True, default="default")
    is_opaque = Column(Boolean, default=False, nullable=True)


def parse_fhir_instant(value: str) -> datetime:
    """
    Parses a FHIR instant/dateTime string into a timezone-aware UTC datetime.
    Correctly handles 'Z' and timezone offsets like +02:00, -05:00.
    Ensures that e.g. 10:00:00+02:00 is evaluated as 08:00:00Z.
    """
    if not value:
        raise ValueError("Empty timestamp string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_date(date_str: str) -> str:
    try:
        return parse_fhir_instant(date_str).isoformat()
    except Exception:
        return date_str


class CursorStore:
    """
    G5 Replay-Safe Cursor Store.
    Features:
    - Partitioned checkpoints by connector_id, resource_type, and clinic_id.
    - Preserves opaque tokens as opaque; never lexically sorts opaque tokens.
    - Preserves monotonicity for timestamp-based checkpoints.
    - Supports atomic rollback on crash/recovery.
    """

    def get(self, connector_id: str, resource_type: str, clinic_id: Optional[str] = None) -> Optional[str]:
        target_clinic = clinic_id or "default"
        with SessionLocal() as db:
            cursor = db.query(CursorModel).filter(
                CursorModel.connector_id == connector_id,
                CursorModel.resource_type == resource_type,
                (CursorModel.clinic_id == target_clinic) | (CursorModel.clinic_id.is_(None))
            ).first()
            return cursor.checkpoint if cursor else None

    def commit(
        self,
        connector_id: str,
        resource_type: str,
        checkpoint: str,
        clinic_id: Optional[str] = None,
        is_opaque: bool = False
    ) -> None:
        if not checkpoint:
            return

        target_clinic = clinic_id or "default"
        new_dt = None
        normalized = checkpoint

        if not is_opaque:
            try:
                new_dt = parse_fhir_instant(checkpoint)
                normalized = new_dt.isoformat()
            except Exception:
                normalized = checkpoint
                new_dt = None

        with SessionLocal() as db:
            cursor = db.query(CursorModel).filter(
                CursorModel.connector_id == connector_id,
                CursorModel.resource_type == resource_type,
                (CursorModel.clinic_id == target_clinic) | (CursorModel.clinic_id.is_(None))
            ).with_for_update().first()

            if cursor:
                if is_opaque:
                    # G5 Rule: Preserve an opaque cursor as opaque; never lexically sort tokens
                    cursor.checkpoint = checkpoint
                    cursor.is_opaque = True
                else:
                    should_update = False
                    if new_dt:
                        try:
                            cur_dt = parse_fhir_instant(cursor.checkpoint)
                            should_update = new_dt > cur_dt
                        except Exception:
                            should_update = normalized > cursor.checkpoint
                    else:
                        should_update = normalized > cursor.checkpoint

                    if should_update:
                        cursor.checkpoint = normalized
            else:
                cursor = CursorModel(
                    connector_id=connector_id,
                    resource_type=resource_type,
                    clinic_id=target_clinic,
                    checkpoint=normalized,
                    is_opaque=is_opaque
                )
                db.add(cursor)
            db.commit()

    def rollback(self, connector_id: str, resource_type: str, safe_checkpoint: str, clinic_id: Optional[str] = None) -> None:
        """Rolls back cursor to a known safe checkpoint upon crash or partial commit."""
        target_clinic = clinic_id or "default"
        with SessionLocal() as db:
            cursor = db.query(CursorModel).filter(
                CursorModel.connector_id == connector_id,
                CursorModel.resource_type == resource_type,
                (CursorModel.clinic_id == target_clinic) | (CursorModel.clinic_id.is_(None))
            ).first()
            if cursor:
                cursor.checkpoint = safe_checkpoint
                db.commit()


cursor_store = CursorStore()
