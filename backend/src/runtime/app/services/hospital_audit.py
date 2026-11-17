"""Use the existing append-only audit model in the caller's transaction."""
import json
from app.models.audit import AuditLogModel


def record_hospital_audit(db, scope, actor_id, action, entity_ref, *, reason, old=None, new=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Audit reason required")
    db.add(AuditLogModel(user_system=str(actor_id), action=action, module="Hospital",
        org_id=scope.org_id, clinic_id=scope.clinic_id, entity_ref=entity_ref,
        correlation_id=entity_ref, reason=reason,
        details_json=json.dumps({"old": old, "new": new, "grant_revision": scope.grant_revision}, sort_keys=True)))
    db.flush()
