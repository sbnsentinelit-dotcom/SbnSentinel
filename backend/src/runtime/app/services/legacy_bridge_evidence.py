"""
Legacy Claims Bridge: the evidence behind every figure, and how two assessments differ.

Every answer is rebuilt from the stored records exactly as the assessment saw them (its cutoff and the
records received by the time it was computed), so a figure on the screen can always be traced to the
records, the source role, the delivered page, who delivered it and the evidence custody row. Source
system names (connectors) are never returned.
"""
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.legacy_bridge.assessment import index_records, line_name, positions, trace_matches
from app.legacy_bridge.messages import message
from app.legacy_bridge.summary import BUCKETS
from app.models.legacy_bridge import LegacyAssessmentModel, LegacyPageModel, LegacySealModel, LegacyTransitionModel
from app.services.legacy_bridge_service import BridgeError, HistoryUnavailable, bindings, inputs_of, iso, to_db

FIGURE = re.compile(r"^(balance:(control|opening|closing):(%s)|cash:(advised|observed|confirmed|unmatched|unapplied|"
                    r"ledger_posted|provider_adjustments)|route:[a-z_]+|denial:[A-Za-z0-9._/-]+|source:[a-z]+|"
                    r"issue:[A-Z_]+:.+)$" % "|".join(BUCKETS))

RULES = {
    "balance": "Each position (claim line and bucket) counts once, from exactly one valid row dated at its snapshot. "
               "Duplicated, conflicting or invalid rows are listed as unverified and left out of the total.",
    "cash:advised": "Net amount of every cash payment advice from the payer.",
    "cash:observed": "Every bank deposit record, each counted once.",
    "cash:confirmed": "A deposit is confirmed only when exactly one advice, a nonzero cash advice, and exactly one deposit "
                      "share the payment trace and the amounts agree.",
    "cash:unmatched": "Deposits that are not confirmed: no advice, more than one advice or deposit on the trace, or a "
                      "different amount.",
    "cash:unapplied": "Deposits whose trace has no payment advice at all.",
    "cash:ledger_posted": "Every posting of a payment component in the clinic ledger.",
    "cash:provider_adjustments": "Provider-level recoveries inside payment advices, with their accepted finance owner.",
    "route": "A route is proven by a recent successful test that matches its source document, route and time window.",
    "denial": "A legacy denial with its appeal steps and the accepted owner of its claim line.",
    "source": "Every record this source delivered that the assessment counted, and its completeness statements.",
    "issue": "Records named by this hold reason.",
}


def _fields(data) -> dict:
    out = {}
    for k, v in data.model_dump(mode="json").items():
        if k == "kind" or v is None:
            continue
        if isinstance(v, dict) and {"book", "claim", "line"} <= set(v):
            v = "/".join((v["book"], v["claim"], v["line"]))
        out[k] = v
    return out


class _Evidence:
    """The records of one assessment, with where each came from."""

    def __init__(self, db: Session, t: LegacyTransitionModel, a: LegacyAssessmentModel):
        self.t, self.a = t, a
        ctx, batch, stored = inputs_of(db, t, a)     # the exact inputs from the assessment's manifest
        self.ctx = ctx
        self.data, _refs = index_records(ctx, batch.records, set())
        self.row_of = {id(rec): row for rec, row in zip(batch.records, stored)}
        self.role_of = {b.connector: b.role for b in bindings(db, t)}
        page_ids = {row.page_id for row in stored}
        self.pages = {p.id: p for p in db.query(LegacyPageModel).filter(LegacyPageModel.id.in_(page_ids)).all()} \
            if page_ids else {}
        self.db = db

    def view(self, rec, status: Optional[str] = None) -> dict:
        row = self.row_of.get(id(rec))
        page = self.pages.get(row.page_id) if row else None
        return {
            "key": rec.key, "kind": rec.data.kind, "source": self.role_of.get(rec.connector),
            "effective": iso(rec.effective), "seen": iso(rec.seen),
            "received_at": iso(row.received_at) if row else None,
            "received_by": page.received_by if page else None,
            "page_token": page.token if page else None,
            "custody_evidence_id": page.evidence_id if page else None,
            "evidence_ref": rec.evidence, "fields": _fields(rec.data), "status": status,
        }

    def all(self):
        for kind in self.data.values():
            yield from kind.values()


def _balance(ev: _Evidence, kind: str, bucket: str):
    at = ev.ctx.snapshot_at if kind == "closing" else ev.ctx.cutover
    valid, disputed = positions(ev.data[kind], at)
    out, total = [], 0
    for rec in sorted(ev.data[kind].values(), key=lambda r: r.key):
        if rec.data.bucket != bucket:
            continue
        pos = (rec.data.line, bucket)
        counted = pos in valid
        total += rec.data.minor if counted else 0
        out.append(ev.view(rec, "counted" if counted else "unverified"))
    return out, total


def _cash(ev: _Evidence, which: str):
    matches = trace_matches(ev.data)
    out, total = [], 0

    def trace_status(trace):
        d, a, ok = matches[trace]
        if ok:
            return "confirmed: one cash advice and one deposit agree"
        if not a:
            return "no payment advice on this trace"
        if len(a) > 1:
            return f"{len(a)} advices on this trace"
        if len(d) > 1:
            return f"{len(d)} deposits on this trace"
        if a[0].data.settlement != "cash":
            return "noncash advice"
        return "amount differs from the advice"

    if which in ("observed", "confirmed", "unmatched", "unapplied"):
        for trace, (d, a, ok) in sorted(matches.items()):
            if which == "confirmed" and not ok or which == "unmatched" and ok or which == "unapplied" and a:
                continue
            for rec in sorted(d, key=lambda r: r.key):
                total += rec.data.minor
                out.append(ev.view(rec, trace_status(trace)))
            if which in ("confirmed", "unmatched"):
                out.extend(ev.view(rec, "advice on the same trace") for rec in sorted(a, key=lambda r: r.key))
    elif which == "advised":
        for rec in sorted(ev.data["advice"].values(), key=lambda r: r.key):
            cash = rec.data.settlement == "cash"
            total += rec.data.net if cash else 0
            out.append(ev.view(rec, "cash advice" if cash else "noncash advice (not counted)"))
    elif which == "ledger_posted":
        for rec in sorted(ev.data["posting"].values(), key=lambda r: r.key):
            total += rec.data.component.minor
            out.append(ev.view(rec, "posted"))
    elif which == "provider_adjustments":
        refs = set()
        for rec in sorted(ev.data["advice"].values(), key=lambda r: r.key):
            parts = [p for p in rec.data.parts if p.type == "provider_adjustment"]
            if parts and rec.data.settlement == "cash":
                total += sum(p.minor for p in parts)
                refs.update(p.adjustment_ref for p in parts)
                out.append(ev.view(rec, "advice with a provider-level recovery"))
        out.extend(ev.view(rec, "accepted finance owner") for rec in ev.data["adjustment_owner"].values()
                   if rec.data.adjustment_ref in refs)
    return out, total


def _related(ev: _Evidence, item: str) -> List:
    """Records a hold reason or task names: by key, trace, advice (advice/part), claim line, adjustment
    reference, denial or source role."""
    head = item.split("/")[0]
    out = []
    for rec in ev.all():
        d = rec.data
        line = getattr(d, "line", None)
        names = {rec.key, getattr(d, "trace", None), getattr(d, "adjustment_ref", None), getattr(d, "advice", None),
                 getattr(d, "denial", None), ev.role_of.get(rec.connector), getattr(d, "stage", None)}
        if line is not None:
            names.add(line_name(line))
        if item in names or head in names or (line is not None and item.startswith(line_name(line) + "/")):
            out.append(rec)
    return sorted(out, key=lambda r: (r.data.kind, r.key))


def figure_evidence(db: Session, t: LegacyTransitionModel, a: LegacyAssessmentModel, figure: str) -> Dict:
    if not FIGURE.match(figure):
        raise BridgeError(422, "Unknown figure.")
    try:
        ev = _Evidence(db, t, a)
    except HistoryUnavailable as e:
        return {"figure": figure, "label": figure, "assessment_id": a.id, "as_of": iso(a.as_of), "total": None,
                "rule": None, "records": [], "unavailable": str(e)}
    head, _, rest = figure.partition(":")
    total, records, label, rule = None, [], figure, RULES.get(head)
    if head == "balance":
        kind, bucket = rest.split(":")
        records, total = _balance(ev, kind, bucket)
        label = f"{kind} {bucket}"
    elif head == "cash":
        records, total = _cash(ev, rest)
        rule = RULES.get(figure)
        label = rest
    elif head == "route":
        unproven = {i["item"] for i in json.loads(a.issues_json) if i["code"] == "UNPROVEN_ROUTE"}
        invalid = {i["item"] for i in json.loads(a.issues_json) if i["code"] == "INVALID_ROUTE_PROBE"}
        probes = [r for r in ev.data["probe"].values() if r.data.stage == rest]
        records = [ev.view(r, "invalid test" if r.key in invalid else "proven" if rest not in unproven else "not proven")
                   for r in sorted(probes, key=lambda r: r.key)]
        label = rest
    elif head == "denial":
        den = ev.data["denial"].get(rest)
        if den is None:
            raise BridgeError(404, "This denial is not in the assessment.")
        records = [ev.view(den, "denial")]
        records += [ev.view(r, f"appeal {r.data.stage}") for r in sorted(ev.data["appeal"].values(), key=lambda r: r.key)
                    if r.data.denial == rest]
        records += [ev.view(r, "line owner") for r in ev.data["assignment"].values() if r.data.line == den.data.line]
        label = line_name(den.data.line)
    elif head == "source":
        records = [ev.view(r) for r in sorted(ev.all(), key=lambda r: (r.data.kind, r.key))
                   if ev.role_of.get(r.connector) == rest]
        # The statements this assessment could see: those received by the time it was computed (07.T3).
        # A statement or correction that arrives later belongs to a later assessment.
        manifest = json.loads(a.manifest_json) if a.manifest_json else {}
        seen_by = (to_db(datetime.fromisoformat(manifest["inputs_until"].replace("Z", "+00:00")))
                   if manifest.get("inputs_until") else max(a.computed_at, a.as_of))
        seals = (db.query(LegacySealModel).filter(LegacySealModel.transition_id == t.id, LegacySealModel.role == rest,
                                                  LegacySealModel.received_at <= seen_by)
                 .order_by(LegacySealModel.through, LegacySealModel.id).all())
        visible = {s.id for s in seals}
        used = set(manifest.get("seals", []))
        label = rest
        return {"figure": figure, "label": label, "assessment_id": a.id, "as_of": iso(a.as_of), "total": None,
                "rule": rule, "records": records,
                "seals": [{"through": iso(s.through), "unique_rows": s.unique_rows, "received_at": iso(s.received_at),
                           "receipt": s.receipt, "applies": s.id in used, "superseded": s.superseded_by_id in visible}
                          for s in seals]}
    else:  # issue:<CODE>:<item>
        code, _, item = rest.partition(":")
        records = [ev.view(r) for r in _related(ev, item)]
        label, rule = message(code), f"{RULES['issue']} ({item})"
    return {"figure": figure, "label": label, "assessment_id": a.id, "as_of": iso(a.as_of), "total": total,
            "rule": rule, "records": records}


def work_item_evidence(db: Session, t: LegacyTransitionModel, a: Optional[LegacyAssessmentModel], w) -> List[dict]:
    """Records behind a task in the given (current) assessment: its linked hold reasons, or the item
    its follow-up names (a denial, a provider recovery reference, a reopened claim line)."""
    if a is None:
        return []
    try:
        ev = _Evidence(db, t, a)
    except HistoryUnavailable:
        return []
    names = [i["item"] for i in json.loads(w.issues_json)] if w.issues_json else [w.item]
    seen, out = set(), []
    for item in names:
        for rec in _related(ev, item):
            if id(rec) not in seen:
                seen.add(id(rec))
                out.append(ev.view(rec))
    return out


def _figures(summary: dict) -> Dict[str, Optional[int]]:
    out = {}
    for kind in ("control", "opening", "closing"):
        for bucket, v in (summary.get(kind) or {}).items():
            out[f"balance:{kind}:{bucket}"] = v
    for k, v in (summary.get("cash") or {}).items():
        if isinstance(v, (int, float)) or v is None:
            out[f"cash:{k}"] = v
    out["routes:proven"] = sum(1 for r in summary.get("routes", []) if r.get("proved"))
    out["denials"] = len(summary.get("denials", []))
    return out


def compare(a: LegacyAssessmentModel, b: LegacyAssessmentModel) -> dict:
    """What changed from assessment a to assessment b: status, hold reasons and every figure."""
    ia = {(i["code"], i["item"]) for i in json.loads(a.issues_json)}
    ib = {(i["code"], i["item"]) for i in json.loads(b.issues_json)}
    fa, fb = _figures(json.loads(a.summary_json)), _figures(json.loads(b.summary_json))
    changed = [{"figure": k, "from": fa.get(k), "to": fb.get(k)} for k in sorted(set(fa) | set(fb))
               if fa.get(k) != fb.get(k)]
    return {
        "from": {"assessment_id": a.id, "as_of": iso(a.as_of), "revision": a.revision, "status": a.status},
        "to": {"assessment_id": b.id, "as_of": iso(b.as_of), "revision": b.revision, "status": b.status},
        "issues_added": [{"code": c, "item": i, "message": message(c)} for c, i in sorted(ib - ia)],
        "issues_removed": [{"code": c, "item": i, "message": message(c)} for c, i in sorted(ia - ib)],
        "figures_changed": changed,
        "records": {"from": a.record_count, "to": b.record_count},
    }


def pulse(db: Session, t: LegacyTransitionModel) -> dict:
    """A cheap fingerprint of everything the screen shows; the screen re-reads when it changes."""
    from sqlalchemy import func
    from app.models.legacy_bridge import LegacyWorkItemEventModel
    last_page = db.query(func.max(LegacyPageModel.received_at)).filter(LegacyPageModel.transition_id == t.id).scalar()
    last_event = db.query(func.max(LegacyWorkItemEventModel.id)).filter(
        LegacyWorkItemEventModel.transition_id == t.id).scalar()
    return {"transition_id": t.id, "current_assessment_id": t.current_assessment_id,
            "last_received_at": iso(last_page), "last_work_event": last_event, "lock_version": t.lock_version}
