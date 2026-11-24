"""
Waiting-room rule (auditor's Waiting Room Stress Test 3, revised-rule.mjs), in Python.

For a clinic's review roster at one cutoff, from its source events, it answers:
* how long patients waited before their consultation started (only visits with a recorded arrival and
  consultation start count; the count is returned, a missing average is never zero);
* who is still waiting, how long since arrival and how long since continuously ready (the last
  required preparation step: file, nursing and, when the service needs it, payer approval; they can run
  in parallel and are not added up); missing or stale evidence leaves readiness unconfirmed;
* the handoff state and who follows up (the clinic coordinator until the receiving team accepts that
  handoff).

Events are dicts: {clinic, visit, kind, source, id, occurred, known, destination, handoff}; times are
ISO strings with an explicit time zone. A kind counts only from its authorized source; an event known
after the cutoff does not count; conflicting duplicates of one source id send the visit to review.
Roster rows: {id, clinic, payerRequired (True / False / None = unknown pathway)}.
Options: {clinic, cutoff, fresh (movement feed fresh), coverage: {source: False when that feed is down}}.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

AUTHORIZED = {
    "arrive": "reception", "no_show": "reception", "left": "reception", "start": "clinician", "room": "rooms",
    "file": "records", "file_revoked": "records", "approval": "payer", "approval_revoked": "payer",
    "nurse": "nursing", "nurse_revoked": "nursing", "send": "nursing", "accept": "consult_team",
}
READY_ALERT_MINUTES = 10           # illustrative ready alert; replace with a clinic-approved rule
COORDINATOR = "Clinic coordinator"

_TZ = re.compile(r"(Z|[+-]\d\d:\d\d)$")


def instant(value: Any) -> Optional[float]:
    """Milliseconds since the epoch for an ISO time with an explicit zone; None otherwise."""
    if not isinstance(value, str) or not _TZ.search(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _minutes(a: float, b: float) -> float:
    return (b - a) / 60000


def _event_index(events: Iterable[Dict[str, Any]], clinic: str, cutoff: float):
    by_visit: Dict[str, List[Dict[str, Any]]] = {}
    bad = set()
    identities: Dict[str, Dict[str, Any]] = {}
    for e in events:
        kind = e.get("kind")
        if e.get("clinic") != clinic or kind not in AUTHORIZED or e.get("source") != AUTHORIZED[kind]:
            continue
        known, when = instant(e.get("known")), instant(e.get("occurred"))
        if known is not None and known > cutoff:
            continue
        if known is None or when is None or known < when or not e.get("id"):
            bad.add(e.get("visit"))
            continue
        if when > cutoff:
            continue
        key = repr((e.get("source"), e.get("id")))
        signature = repr((e.get("visit"), kind, when, e.get("destination"), e.get("handoff")))
        earlier = identities.get(key)
        if earlier:
            if earlier["signature"] != signature:
                bad.add(earlier["visit"])
                bad.add(e.get("visit"))
            continue
        identities[key] = {"signature": signature, "visit": e.get("visit")}
        by_visit.setdefault(e.get("visit"), []).append({**e, "time": when})
    for items in by_visit.values():
        items.sort(key=lambda x: (x["time"], x["id"]))
    return by_visit, bad


def _blank(visit_id: str, status: str) -> Dict[str, Any]:
    return {"id": visit_id, "status": status, "age": None, "readyAge": None, "flag": False,
            "handoff": "Unconfirmed", "owner": None, "evidence": []}


def _summarize_visit(roster: Dict[str, Any], events: List[Dict[str, Any]], bad: bool, now: float, fresh: bool,
                     coverage: Dict[str, Any]) -> Dict[str, Any]:
    def review():
        return {**_blank(roster["id"], "review"), "owner": COORDINATOR, "evidence": [e["id"] for e in events]}
    if bad:
        return review()

    def first(kind):
        return next((e for e in events if e["kind"] == kind), None)
    arrival, start, left, absent = first("arrive"), first("start"), first("left"), first("no_show")
    if ((left and start) or (absent and (arrival or start or left))
            or (arrival and any(e and e["time"] < arrival["time"] for e in (start, left)))):
        return review()
    evidence = [e["id"] for e in events]
    if start:
        return {**_blank(roster["id"], "seen"),
                "age": _minutes(arrival["time"], start["time"]) if arrival else None, "evidence": evidence}
    if left or absent:
        return {**_blank(roster["id"], "left" if left else "noShow"), "evidence": evidence}
    if not arrival or not fresh or coverage.get("reception") is False or coverage.get("clinician") is False:
        return {**_blank(roster["id"], "unknown"), "owner": COORDINATOR, "evidence": evidence}
    row = {**_blank(roster["id"], "waiting"), "age": _minutes(arrival["time"], now), "owner": COORDINATOR,
           "evidence": evidence}
    payer_required = roster.get("payerRequired")
    if not isinstance(payer_required, bool):
        return row
    required = ["file", "nurse", "approval"] if payer_required else ["file", "nurse"]
    if (coverage.get("records") is False or coverage.get("nursing") is False
            or (payer_required and coverage.get("payer") is False)):
        return row
    qualified = set()
    since = sent = accepted = None
    groups: Dict[float, List[Dict[str, Any]]] = {}
    for e in events:
        groups.setdefault(e["time"], []).append(e)
    for time, group in groups.items():
        for k in required:
            yes = any(e["kind"] == k for e in group)
            no = any(e["kind"] == k + "_revoked" for e in group)
            if yes and no:
                return review()
            if no:
                qualified.discard(k)
            elif yes:
                qualified.add(k)
        if time < arrival["time"] or not all(k in qualified for k in required):
            since = sent = accepted = None
            continue
        if since is None:
            since = time
        sends = [e for e in group if e["kind"] == "send" and e.get("destination") == "Consultation" and e.get("handoff")]
        if len({e["handoff"] for e in sends}) > 1:
            return review()
        if sends and (sent or {}).get("handoff") != sends[0]["handoff"]:
            sent, accepted = sends[0], None
        if sent:
            ack = next((e for e in group if e["kind"] == "accept" and e.get("destination") == sent.get("destination")
                        and e.get("handoff") == sent.get("handoff")), None)
            if ack:
                accepted = ack
    if since is not None:
        row["readyAge"] = _minutes(since, now)
        row["flag"] = row["readyAge"] >= READY_ALERT_MINUTES
    row["handoff"] = "Accepted" if accepted else "Sent, acceptance pending" if sent else "Unconfirmed"
    if accepted:
        row["owner"] = "Consultation team"
    if coverage.get("handoff") is False:
        row["handoff"] = "Handoff evidence unavailable"
        row["owner"] = "Clinic coordinator to verify"
    return row


def revised_rule(roster: Iterable[Dict[str, Any]], events: Iterable[Dict[str, Any]], opts: Dict[str, Any]) -> Dict[str, Any]:
    now = instant(opts.get("cutoff"))
    if now is None:
        raise ValueError("Review cutoff must have an explicit time zone")
    by_visit, bad = _event_index(events, opts.get("clinic"), now)
    unique: Dict[str, Dict[str, Any]] = {}
    for v in roster:
        if v.get("clinic") != opts.get("clinic"):
            continue
        if v["id"] in unique and unique[v["id"]].get("payerRequired") != v.get("payerRequired"):
            bad.add(v["id"])
        unique[v["id"]] = v
    coverage = opts.get("coverage") or {}
    rows = [_summarize_visit(v, by_visit.get(v["id"], []), v["id"] in bad, now, bool(opts.get("fresh")), coverage)
            for v in unique.values()]
    counts = {"seen": 0, "waiting": 0, "unknown": 0, "left": 0, "noShow": 0, "review": 0}
    for r in rows:
        counts[r["status"]] += 1
    done = [r for r in rows if r["status"] == "seen" and r["age"] is not None]
    ongoing = [r for r in rows if r["status"] == "waiting"]
    return {**counts, "rows": rows, "denom": len(done), "seenMissing": counts["seen"] - len(done),
            "mean": sum(r["age"] for r in done) / len(done) if done else None,
            "longest": max([0] + [r["age"] for r in ongoing]) if ongoing else None}
