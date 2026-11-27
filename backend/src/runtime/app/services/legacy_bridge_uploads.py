"""
Legacy Claims Bridge: clinic staff deliver a source's CSV export from the screen.

Preview reads the file, checks every row exactly as the intake would (binding, role, scope, times) and
says what would be stored, without storing anything. Upload stages the same records as one page that
follows the source's current stream head. When the user states that the file completes the source's
export through an instant, the server records that completeness statement (a seal over the source's
whole population at that instant: what it delivered before plus this file), attributed to the user;
the statement is the user's own declaration and is kept with the page's custody row.
"""
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.legacy_bridge import csv_intake
from app.legacy_bridge.assessment import digest_rows
from app.legacy_bridge.contracts import Record
from app.legacy_bridge.identity import digest
from app.legacy_bridge.intake import IncomingSeal, PageIn
from app.models.legacy_bridge import (
    LegacyRecordModel, LegacySealModel, LegacySourceBindingModel, LegacyTransitionModel,
)
from app.services.legacy_bridge_service import (
    BridgeError, _check_page, _read_head, _utcnow, bindings, iso, scope_of, stage_page, to_db,
)


def _binding(db: Session, t: LegacyTransitionModel, binding_id: int) -> LegacySourceBindingModel:
    b = db.query(LegacySourceBindingModel).filter(LegacySourceBindingModel.id == binding_id,
                                                  LegacySourceBindingModel.transition_id == t.id).first()
    if not b:
        raise BridgeError(403, "Unknown source for this transition.")
    return b


def _parse(t: LegacyTransitionModel, b: LegacySourceBindingModel, content: str):
    from app.services.revenue_overview import clinic_offset
    if len(content) > 5_000_000:
        raise BridgeError(413, "The file is larger than 5 MB; split the export.")
    return csv_intake.parse(b.role, content, clinic_offset())


def _population(db: Session, t: LegacyTransitionModel, b: LegacySourceBindingModel, new: List[Record],
                through: datetime) -> List[Record]:
    """The source's records as an assessment at `through` would count them: everything it delivered
    (before and in this file) observed by then, one copy per business record; for the control source
    its controls plus the closing snapshot of that cutoff."""
    stored = [Record.model_validate_json(r) for (r,) in db.query(LegacyRecordModel.record_json).filter(
        LegacyRecordModel.transition_id == t.id, LegacyRecordModel.connector == b.connector).all()]
    seen_at = through.replace(tzinfo=timezone.utc)
    rows = [r for r in stored + new if r.seen <= seen_at and r.revision == 1]
    if b.role == "control":
        closings = [r.effective for r in rows if r.data.kind == "closing" and r.effective <= seen_at]
        snapshot = max(closings) if closings else None
        rows = [r for r in rows if r.data.kind != "closing" or r.effective == snapshot]
    groups: Dict[tuple, Dict[str, Record]] = {}
    for r in rows:
        content = digest(dict(revision=r.revision, effective=r.effective.isoformat(), data=r.data.model_dump(mode="json")))
        groups.setdefault((r.data.kind, r.key), {})[content] = r
    # A business record delivered twice with different content is held by the rules; it is not counted.
    return [next(iter(g.values())) for g in groups.values() if len(g) == 1]


def preview(db: Session, t: LegacyTransitionModel, binding_id: int, content: str,
            now: Optional[datetime] = None) -> dict:
    now = now or _utcnow()
    b = _binding(db, t, binding_id)
    records, problems = _parse(t, b, content)
    checked: List[Record] = []
    if records and not problems:
        try:
            checked = _check_page(t, b, PageIn.model_validate_json(json.dumps(
                {"binding_id": b.id, "records": records})), now)
        except BridgeError as e:
            detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            problems += [{"row": None, "reason": f"{p['key']}: {p['reason']}"} for p in detail.get("records", [])] \
                or [{"row": None, "reason": detail.get("message")}]
        except Exception as e:   # contract validation of a record
            problems.append({"row": None, "reason": str(e).splitlines()[-1][:300]})
    have = {d for (d,) in db.query(LegacyRecordModel.content_digest).filter(
        LegacyRecordModel.transition_id == t.id, LegacyRecordModel.connector == b.connector).all()}
    fresh = [r for r in checked if digest(r.model_dump()) not in have]
    by_kind: Dict[str, int] = {}
    for r in records:
        by_kind[r["data"]["kind"]] = by_kind.get(r["data"]["kind"], 0) + 1
    seals = {}
    for s in db.query(LegacySealModel).filter(LegacySealModel.transition_id == t.id).all():
        if s.role not in seals or s.through > seals[s.role]:
            seals[s.role] = s.through
    head = _read_head(db, t, b.connector, "export")
    latest = max(seals.values()) if seals else None
    return {
        "role": b.role, "rows": len(records), "by_kind": by_kind, "problems": problems,
        "new_records": len(fresh), "already_received": len(checked) - len(fresh),
        "latest_event": iso(max((r.effective for r in checked), default=None)),
        "this_source_complete_through": iso(seals.get(b.role)),
        "sources_complete_through": {role: iso(v) for role, v in sorted(seals.items())},
        "latest_statement": iso(latest),
        "stream_head": head.head_token if head else None,
        "sample": records[:10],
        "can_upload": bool(records) and not problems,
    }


def upload(db: Session, t: LegacyTransitionModel, binding_id: int, content: str, filename: str, actor: str,
           complete_through: Optional[datetime] = None, now: Optional[datetime] = None) -> dict:
    now = now or _utcnow()
    b = _binding(db, t, binding_id)
    records, problems = _parse(t, b, content)
    if problems or not records:
        raise BridgeError(422, {"message": "The file was refused; nothing was stored.",
                                "records": [{"key": f"row {p['row']}" if p['row'] else "file", "reason": p["reason"]}
                                            for p in problems] or [{"key": "file", "reason": "no records"}]})
    seal = None
    if complete_through is not None:
        through = to_db(complete_through)
        if through > now:
            raise BridgeError(422, "The completeness date cannot be in the future.")
        scope = scope_of(t)
        new = [Record.model_validate_json(json.dumps({**r, "scope": scope, "connector": b.connector})) for r in records]
        population = _population(db, t, b, new, through)
        seal = IncomingSeal(through=through.replace(tzinfo=timezone.utc), unique_rows=len(population),
                            digest=digest_rows(population),
                            # The statement's identity is its source, instant and population; who uploaded which
                            # file is kept separately with the page's custody row (07.T4).
                            receipt=f"attested-{digest([b.connector, iso(through), digest_rows(population)])[:24]}")
    head = _read_head(db, t, b.connector, "export")
    page = PageIn.model_validate_json(json.dumps({
        "binding_id": b.id, "resource": "export", "previous": head.head_token if head else None,
        "records": records, "seal": seal.model_dump(mode="json") if seal else None}))
    attestation = {"file": filename[:200], "uploaded_by": actor,
                   "complete_through": iso(to_db(complete_through)) if complete_through else None}
    result = stage_page(db, t, page, actor, now, attestation=attestation)
    return {**result, "complete_through": attestation["complete_through"],
            "sealed_rows": seal.unique_rows if seal else None}


def source_roles(db: Session, t: LegacyTransitionModel) -> List[dict]:
    """Every evidence role with its bound sources (no source system names)."""
    from app.legacy_bridge.scenario import ROLES
    bound = {}
    for b in bindings(db, t):
        bound.setdefault(b.role, []).append(
            {"binding_id": b.id, "valid_from": iso(b.valid_from), "valid_until": iso(b.valid_until)})
    return [{"role": r, "sources": bound.get(r, []), "csv_columns": csv_intake.columns(r)} for r in ROLES]
