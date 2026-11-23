"""
Evidence retrieval for a denied claim.

Before a claim is resubmitted (by a person after review, or by Autopilot), Sentinel collects the
evidence the payer asks for from the connected systems: first the EHR, then the other sources.
Every document found is recorded with its source and reference; anything that cannot be found is
listed, and a notification is stored so a person can request it. The claim is never resubmitted
with missing evidence (the payer connector refuses).

In the demo the connected systems are synthetic: the EHR holds what the claim data says it holds,
and the other sources answer deterministically per claim and document (about one in two found).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.signal import SignalModel

EHR_SOURCE = "EHR document store (synthetic)"
SECONDARY_SOURCES = (
    "Lab information system (synthetic)",
    "Provider document request (synthetic)",
    "DHPO claim history (synthetic)",
)
NOTIFICATION_SOURCE = "Sentinel Evidence Retrieval"


def _digest(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest(), 16)


def _search_other_sources(claim_id: str, document: str) -> Optional[str]:
    """Synthetic search of the non-EHR systems. Returns the source that holds it, or None."""
    h = _digest(claim_id, document)
    if h % 2 == 0:
        return None
    return SECONDARY_SOURCES[h % len(SECONDARY_SOURCES)]


def retrieve_claim_evidence(db: Session, signal: SignalModel, requested_by: str,
                            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Collects the required evidence for the signal's denied claim and stores the result on the
    signal. Documents found are added to the evidence Sentinel holds for the claim."""
    now = now or datetime.utcnow()
    meta = dict(signal.metadata_data or {})
    inputs = dict(meta.get("risk_inputs") or {})
    claim_id = inputs.get("claim_id")
    required: List[str] = list(inputs.get("required_evidence") or [])
    if not claim_id or not required:
        return {"status": "NOT_APPLICABLE", "message": "This signal has no claim evidence to collect.",
                "documents": [], "not_found": [], "complete": False}

    held = set(inputs.get("evidence_available") or [])
    documents = []
    for name in required:
        if name in held:
            source = EHR_SOURCE
        else:
            source = _search_other_sources(claim_id, name)
        found = source is not None
        if found:
            held.add(name)
        documents.append({
            "name": name,
            "status": "FOUND" if found else "NOT_FOUND",
            "source": source,
            "document_ref": f"DOC-{_digest(claim_id, name) % 16 ** 8:08X}" if found else None,
        })
    not_found = [d["name"] for d in documents if d["status"] == "NOT_FOUND"]
    retrieval = {
        "status": "COMPLETE" if not not_found else "INCOMPLETE",
        "complete": not not_found,
        "claim_id": claim_id,
        "searched_at": now.replace(tzinfo=timezone.utc).isoformat(),
        "requested_by": requested_by,
        "documents": documents,
        "not_found": not_found,
    }
    inputs["evidence_available"] = [name for name in required if name in held]
    meta["risk_inputs"] = inputs
    meta["evidence_retrieval"] = retrieval

    if not_found and not meta.get("evidence_notification_id"):
        meta["evidence_notification_id"] = _store_notification(db, signal, claim_id, not_found, inputs, now)
    signal.metadata_data = meta  # reassign so the JSON change is persisted
    db.commit()
    return retrieval


def _store_notification(db: Session, signal: SignalModel, claim_id: str, missing: List[str],
                        inputs: Dict[str, Any], now: datetime) -> str:
    """A bell notification for a person: the evidence could not be found automatically."""
    from app.services.risk_level_evaluator import DEFAULT_POLICY, _utc, local_time
    deadline = _utc(inputs.get("resubmission_deadline"))
    by = f" before {local_time(deadline, DEFAULT_POLICY)}" if deadline else ""
    payer = inputs.get("payer") or "the payer"
    notification_id = f"NTF-{uuid.uuid4().hex[:10].upper()}"
    db.add(SignalModel(
        id=notification_id,
        source=NOTIFICATION_SOURCE,
        type="Notification",
        message=(f"Evidence not found for claim {claim_id}: {', '.join(missing)}. Request it from the provider "
                 f"and resubmit to {payer}{by}."),
        timestamp=now,
        org_id=signal.org_id,
        clinic_id=signal.clinic_id,
        risk_level="Information",
        metadata_data={"notification": True, "about_signal": signal.id, "claim_id": claim_id,
                       "missing_evidence": missing, "patient_name": (signal.metadata_data or {}).get("patient_name")},
    ))
    return notification_id
