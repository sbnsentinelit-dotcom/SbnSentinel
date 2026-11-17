"""H08 bounded projection worker using the existing pipeline_events queue."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_, and_
from app.models.event import OperationalEventModel
from app.models.evidence import EvidenceModel, IngestionLedgerModel
from app.services.financial_projection_service import apply_committed


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class HospitalWorker:
    def __init__(self, session_factory, authorize, *, worker_id, lease_seconds=30, max_attempts=5):
        if not worker_id or lease_seconds < 1 or max_attempts < 1:
            raise ValueError("Invalid worker configuration")
        self.sessions = session_factory
        self.authorize = authorize
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def claim(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            event = db.scalars(select(OperationalEventModel).where(
                OperationalEventModel.workflow_key == "hospital-financial",
                or_(OperationalEventModel.state.in_(["Queued", "Retrying", "Deferred"]),
                    and_(OperationalEventModel.state == "Running", OperationalEventModel.lease_until < now)),
                or_(OperationalEventModel.next_attempt_at.is_(None), OperationalEventModel.next_attempt_at <= now),
            ).order_by(OperationalEventModel.received_at).limit(1).with_for_update(skip_locked=True)).first()
            if event is None:
                return None
            event.state = "Running"
            event.lease_owner = self.worker_id
            event.lease_until = now + timedelta(seconds=self.lease_seconds)
            event.lease_generation = (event.lease_generation or 0) + 1
            event.row_version = (event.row_version or 0) + 1
            claim = (event.id, event.lease_generation, event.row_version)
            db.commit()
            return claim

    def process(self, claim):
        event_id, generation, revision = claim
        with self.sessions() as db:
            event = db.scalars(select(OperationalEventModel).where(
                OperationalEventModel.id == event_id).with_for_update()).one()
            now = datetime.now(timezone.utc)
            if (event.state != "Running" or event.lease_owner != self.worker_id or
                event.lease_generation != generation or event.row_version != revision or
                event.lease_until is None or utc(event.lease_until) <= now):
                return "STALE_LEASE"
            self.authorize(event.source, event.clinic_id)
            ledger = db.get(IngestionLedgerModel, event.ledger_id)
            evidence = db.get(EvidenceModel, ledger.evidence_id) if ledger else None
            if evidence is None:
                raise ValueError("Durable event evidence is missing")
            import json
            basis = json.loads(evidence.fact_value_str)
            self.authorize(event.source, event.clinic_id, evidence.resource_kind, basis.get("profile_revision"))
            result = apply_committed(db, evidence, consumer_version=event.consumer_version)
            self.authorize(event.source, event.clinic_id)
            if utc(event.lease_until) <= datetime.now(timezone.utc):
                db.rollback()
                return "STALE_LEASE"
            if result.status in {"APPLIED", "DUPLICATE", "HISTORICAL"}:
                event.state = "Completed"
                event.completed_at = datetime.now(timezone.utc)
                event.last_error = None
            else:
                event.retry_count = (event.retry_count or 0) + 1
                event.state = "Failed" if event.retry_count >= self.max_attempts else "Deferred"
                event.last_error = result.reason
                event.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** event.retry_count))
            event.lease_owner = None
            event.lease_until = None
            event.row_version += 1
            db.commit()
            return result.status

    def run_once(self):
        claim = self.claim()
        if claim is None:
            return "IDLE"
        try:
            return self.process(claim)
        except Exception as error:
            event_id, generation, revision = claim
            outcome = "STALE_LEASE"
            with self.sessions() as db:
                event = db.scalars(select(OperationalEventModel).where(
                    OperationalEventModel.id == event_id).with_for_update()).one()
                if (event.state == "Running" and event.lease_owner == self.worker_id
                    and event.lease_generation == generation and event.row_version == revision):
                    event.retry_count = (event.retry_count or 0) + 1
                    event.state = ("Blocked" if isinstance(error, PermissionError) else
                        "Failed" if event.retry_count >= self.max_attempts else "Retrying")
                    event.last_error = "SOURCE_AUTHORITY_BLOCKED" if isinstance(error, PermissionError) else "PROJECTION_FAILED"
                    event.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** event.retry_count))
                    event.lease_owner = None
                    event.lease_until = None
                    event.row_version += 1
                    outcome = "BLOCKED" if event.state == "Blocked" else "FAILED" if event.state == "Failed" else "RETRY_SCHEDULED"
                    db.commit()
            return outcome
