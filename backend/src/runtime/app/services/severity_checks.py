"""
Severity checks for the billing dashboard (auditor's Severity Synthetic Retest 2).

Two separate decisions, evaluated at the same time:

* Review capacity: can the assigned team finish the ready claim reviews by the clinic's internal
  cutoff?  On track / Low / Moderate / Unable to evaluate. Ready claims come from the open queue and
  reviewers from the clinic's review roster (review_roster table).
* Payer filing risk: is this one claim at risk of missing its verified payer filing deadline? Each
  claim is evaluated on its own by risk_level_evaluator.evaluate_claim_denial with the core rule below
  (classify_filing_risk); the dashboard total is counted afterwards.

The core functions (staffed_slots, classify_review_capacity, read_fact, classify_filing_risk) follow the
auditor's reference (retest.py) and work on whole minutes. The results carry what the badge shows:
severity, reason, next action, assumption, rule ID/version, evidence references and evaluation time.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- policy


@dataclass(frozen=True)
class SeverityPolicy:
    """Versioned review policy. The values are synthetic until the clinic approves its own version."""
    policy_id: str = "POL-REVIEW-CAPACITY"
    version: str = "v1"
    is_synthetic: bool = True
    review_minutes: int = 15                   # fixed duration of one uninterrupted claim review
    internal_cutoff_minute: int = 17 * 60      # clinic's internal review cutoff (local minutes)
    utc_offset_minutes: int = 240              # UAE (UTC+4)
    timezone_label: str = "UAE"

    def summary(self) -> Dict[str, Any]:
        return {"policy_id": self.policy_id, "version": self.version, "review_minutes": self.review_minutes,
                "internal_cutoff": clock(self.internal_cutoff_minute), "timezone": self.timezone_label}


POLICY = SeverityPolicy()

REVIEW_RULE_ID, FILING_RULE_ID, RULE_VERSION = "RULE-REVIEW-CAPACITY", "RULE-FILING-RISK", "v1"


def clock(minute: Optional[float]) -> Optional[str]:
    """Local minutes after midnight as '4:42 PM'."""
    if minute is None:
        return None
    m = int(minute) % (24 * 60)
    h, mm = divmod(m, 60)
    return f"{(h % 12) or 12}:{mm:02d} {'AM' if h < 12 else 'PM'}"


# --------------------------------------------------------------------------- review capacity (core)

ON_TRACK, LOW, MODERATE, UNASSESSED = "ON TRACK", "LOW", "MODERATE", "UNASSESSED"


def staffed_slots(roster: Iterable[Sequence[Any]], now: int, end: int, duration: int) -> int:
    """Uninterrupted reviews that fit before `end`. Roster rows: (worker, start, stop, qualified, assigned).
    One qualified, assigned reviewer per review; a worker's overlapping or adjoining intervals merge."""
    windows: Dict[Any, List[Tuple[int, int]]] = defaultdict(list)
    for worker, start, stop, qualified, assigned in roster:
        if qualified and assigned and min(stop, end) > max(start, now):
            windows[worker].append((max(start, now), min(stop, end)))
    slots = 0
    for intervals in windows.values():
        merged: List[List[int]] = []
        for start, stop in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(stop, merged[-1][1])
            else:
                merged.append([start, stop])
        slots += sum((stop - start) // duration for start, stop in merged)
    return slots


def classify_review_capacity(claims: Iterable[str], roster, *, now: int, end: int, staffing_known: bool = True,
                             duration: Optional[int] = 15) -> Tuple[str, Optional[int]]:
    """(result, reviews projected to miss). Each ready claim counts once."""
    count = len(set(claims))
    if count == 0:
        return ON_TRACK, 0
    if not staffing_known or duration is None or duration <= 0:
        return UNASSESSED, None
    available = staffed_slots(roster, now, end, duration)
    if available < count:
        return MODERATE, count - available
    if staffed_slots(roster, now, end - 1, duration) < count:
        return LOW, 0
    return ON_TRACK, 0


# --------------------------------------------------------------------------- filing risk (core)


@dataclass(frozen=True)
class Fact:
    """One piece of evidence for a (clinic, payer, claim, obligation, policy version) key."""
    field: str
    value: Any
    key: tuple = ()
    known_at: Optional[int] = None             # when Sentinel learned it (None = already known)
    valid_until: Optional[int] = None          # when it stops being valid (None = still valid)
    ref: str = ""


@dataclass(frozen=True)
class Route:
    """A recovery route with validated end-to-end remaining times (including downstream stages)."""
    shortest: int = 12
    longest: int = 18
    earliest_start: Optional[int] = None       # None = can start now
    hard_bounds: bool = True                   # False = the times are estimates
    name: str = "Recovery route"
    ref: str = ""


@dataclass(frozen=True)
class Receipt:
    kind: str = "policy-required-receipt"
    key: tuple = ()
    event_at: int = 0                          # when the qualifying event happened
    known_at: int = 0                          # when Sentinel learned about it
    verified: bool = True
    ref: str = ""


def read_fact(facts: Iterable[Fact], key: tuple, name: str, now: int) -> Any:
    """The single value of a fact for this key known and valid at `now`; None if missing or conflicting."""
    values = {f.value for f in facts if f.key == key and f.field == name
              and (f.known_at is None or f.known_at <= now)
              and (f.valid_until is None or now < f.valid_until)}
    return next(iter(values)) if len(values) == 1 else None


def _start(route: Route, now: int) -> int:
    return now if route.earliest_start is None else max(now, route.earliest_start)


def classify_filing_risk(facts: Sequence[Fact], now: int, *, key: tuple, receipts: Iterable[Receipt] = (),
                         alert_lead: int = 0) -> str:
    """Evaluate one claim AND obligation at one as-of time under one policy version."""
    def fact(name):
        return read_fact(facts, key, name, now)

    deadline = fact("deadline")
    if deadline is None:
        return "UNASSESSED: deadline"
    matching = [r for r in receipts if r.key == key and r.verified
                and r.kind == "policy-required-receipt" and r.event_at <= r.known_at <= now]
    if any(r.event_at <= deadline for r in matching):
        return "VERIFIED TIMELY"
    if now > deadline:
        if matching:
            return "LATE RECEIPT: verify earlier filing"
        return "VERIFY OUTCOME"
    material, exposed = fact("material"), fact("plan_exposed")
    if material is False or exposed is False:
        return "NO MATERIAL FILING ALERT"
    if material is None or exposed is None:
        return "UNASSESSED: exposure"
    routes = fact("routes")
    if fact("inventory_complete") is not True or routes is None:
        return "HIGH: verify recovery routes"
    if any(not r.hard_bounds or not (0 < r.shortest <= r.longest) for r in routes):
        return "HIGH: verify route bounds"
    possible = [r for r in routes if _start(r, now) + r.shortest <= deadline]
    if not possible:
        return "CRITICAL: containment"
    for r in possible:
        safe_start = deadline - r.longest
        if safe_start > now + alert_lead and (r.earliest_start is None or r.earliest_start <= safe_start):
            return "HIGH: monitor last start"
    return "CRITICAL: act now"


# --------------------------------------------------------------------------- review capacity result

_TONES = {"Critical": "critical", "High": "high", "Moderate": "moderate", "Low": "low", "On track": "ok",
          "No active filing alert": "ok", "Unable to evaluate": "unknown", "Verify filing outcome": "verify"}


def _result(check: str, severity: str, label: str, code: str, *, reason: str, next_action: str, at_risk: str,
            assumption: Optional[str], rule_id: str, evidence_refs: List[str], as_of: int, as_of_utc: datetime,
            policy: SeverityPolicy, **extra: Any) -> Dict[str, Any]:
    return {
        "check": check, "severity": severity, "label": label, "code": code, "tone": _TONES.get(severity, "unknown"),
        "at_risk": at_risk, "reason": reason, "next_action": next_action, "assumption": assumption,
        "rule_id": rule_id, "rule_version": RULE_VERSION,
        "policy_id": policy.policy_id, "policy_version": policy.version, "is_synthetic": policy.is_synthetic,
        "evidence_refs": evidence_refs,
        "evaluated_at": as_of_utc.replace(tzinfo=timezone.utc).isoformat(),
        "as_of_local": f"{clock(as_of)} {policy.timezone_label}", **extra,
    }


def evaluate_review_capacity(claims: Sequence[str], roster: Sequence[Dict[str, Any]], *, as_of: int,
                             as_of_utc: datetime, policy: SeverityPolicy = POLICY,
                             staffing_known: bool = True, duration: Optional[int] = None,
                             work_order_refs: Sequence[str] = ()) -> Dict[str, Any]:
    """Roster rows: {worker, start, stop, qualified, assigned, ref}. Minutes are local minutes after midnight."""
    duration = policy.review_minutes if duration is None else duration
    cutoff = policy.internal_cutoff_minute
    rows = [(r["worker"], r["start"], r["stop"], r.get("qualified", False), r.get("assigned", False)) for r in roster]
    code, miss = classify_review_capacity(claims, rows, now=as_of, end=cutoff, staffing_known=staffing_known,
                                          duration=duration)
    count = len(set(claims))
    refs = list(work_order_refs) + [r["ref"] for r in roster if r.get("ref") and r.get("qualified") and r.get("assigned")]
    assumption = f"Assumes {duration} minutes per review." if duration else None
    at_risk = f"{count} ready claim review{'s' if count != 1 else ''} due by the {clock(cutoff)} internal cutoff."
    common = dict(at_risk=at_risk, assumption=assumption, rule_id=REVIEW_RULE_ID, evidence_refs=refs, as_of=as_of,
                  as_of_utc=as_of_utc, policy=policy, ready_reviews=count, projected_miss=miss,
                  internal_cutoff=clock(cutoff))
    if code == UNASSESSED:
        missing = "current staffing" if not staffing_known else "review duration"
        return _result("review_capacity", "Unable to evaluate", f"Unable to evaluate — {missing} missing", code,
                       reason=f"The {missing} is missing or stale, so the capacity before the cutoff cannot be "
                       "calculated.", next_action=f"Record the {missing} for today.", missing_input=missing, **common)
    slots = staffed_slots(rows, as_of, cutoff, duration)
    if code == MODERATE:
        latest_cover = cutoff - (miss or 0) * duration
        plural = "s" if miss != 1 else ""
        return _result(
            "review_capacity", "Moderate", f"Moderate — {miss} review{plural} may miss the internal cutoff", code,
            reason=(f"{count} ready reviews need {count} uninterrupted {duration}-minute slots before "
                    f"{clock(cutoff)}; the assigned qualified team has {slots}. {miss} review{plural} "
                    "projected to miss. The work order does not say which claim will miss."),
            next_action=(f"Confirm a qualified backup reviewer free from {clock(latest_cover)} to {clock(cutoff)}, "
                         "or agree a later internal cutoff."), **common)
    if code == LOW:
        return _result("review_capacity", "Low", "Low — no spare time", code,
                       reason=(f"All {count} ready reviews fit exactly before {clock(cutoff)}. No spare time: "
                               "any delay will miss the cutoff."),
                       next_action="Keep the assigned reviewers on these reviews until the cutoff.", **common)
    return _result("review_capacity", "On track", "On track", code,
                   reason=(f"All {count} ready reviews fit before {clock(cutoff)} with time to spare."
                           if count else "No ready reviews are waiting."),
                   next_action="No action needed.", **common)


# --------------------------------------------------------------------------- live clinic data

# Most urgent first: the order of the dashboard total and of the "most urgent claim".
FILING_LABEL_ORDER = (
    "Critical — act now", "Critical — escalate for containment", "High — verify recovery options now", "High",
    "Verify filing outcome", "Unable to evaluate — filing deadline unverified",
    "Unable to evaluate — exposure unverified", "No active filing alert", "Filed on time",
)


def label_tone(label: str) -> str:
    if label.startswith("Critical"):
        return "critical"
    if label.startswith("High"):
        return "high"
    if label.startswith("Verify"):
        return "verify"
    if label.startswith("Unable"):
        return "unknown"
    return "ok"


def local_minute(now_utc: Optional[datetime] = None, policy: SeverityPolicy = POLICY) -> Tuple[int, datetime]:
    """(local minutes after midnight, UTC instant) for now."""
    now_utc = (now_utc or datetime.utcnow()).replace(second=0, microsecond=0)
    local = now_utc + timedelta(minutes=policy.utc_offset_minutes)
    return local.hour * 60 + local.minute, now_utc


def hhmm(value: str) -> int:
    hh, mm = (int(x) for x in value.split(":"))
    return hh * 60 + mm


def roster_rows(entries: Sequence[Any]) -> List[Dict[str, Any]]:
    """Roster records (ReviewRosterModel or dicts) in the shape the capacity check reads."""
    rows = []
    for e in entries:
        get = (lambda k: e.get(k)) if isinstance(e, dict) else (lambda k: getattr(e, k))
        rows.append({"id": get("id"), "worker": get("reviewer"), "role": get("role"), "start": hhmm(get("shift_start")),
                     "stop": hhmm(get("shift_end")), "qualified": bool(get("qualified")),
                     "assigned": bool(get("assigned")), "ref": f"ROSTER-{get('id')}"})
    return rows


def _claim_result(claim: Dict[str, Any], as_of_local: str) -> Dict[str, Any]:
    """A claim's recorded filing-risk evaluation in the badge shape."""
    ev = claim["evaluation"]
    inputs = ev.get("inputs") or {}
    label = ev.get("label") or ev.get("level")
    refs = [r for r in (claim.get("signal_id"), inputs.get("claim_id"), inputs.get("filing_receipt")) if r]
    return {
        "check": "filing_risk", "severity": ev.get("level"), "label": label, "code": inputs.get("filing_code"),
        "tone": label_tone(label or ""), "at_risk": ev.get("problem"), "reason": ev.get("reason"),
        "next_action": ev.get("recommended_action"),
        "assumption": (f"Time left: {ev['time_left']}." if ev.get("time_left") else None),
        "rule_id": ev.get("rule_id"), "rule_version": ev.get("rule_version"), "policy_id": ev.get("policy_id"),
        "policy_version": ev.get("policy_version"), "evidence_refs": refs, "timeline": ev.get("timeline") or [],
        "evaluated_at": ev.get("evaluated_at"), "as_of_local": as_of_local,
        "subject": {"claim": inputs.get("claim_id"), "payer": inputs.get("payer")}, "signal_id": claim.get("signal_id"),
    }


def live_checks(claims: Sequence[Dict[str, Any]], roster: Sequence[Dict[str, Any]], *, preview_backup: bool = False,
                now_utc: Optional[datetime] = None, policy: SeverityPolicy = POLICY) -> Dict[str, Any]:
    """Both checks on the clinic's live data. `claims`: open claims awaiting a decision, each
    {signal_id, claim_id, evaluation} with its own filing-risk evaluation (never pooled).
    `roster`: roster_rows() of the clinic. A preview assigns the unassigned backups without saving."""
    minute, as_of_utc = local_minute(now_utc, policy)
    as_of_local = f"{clock(minute)} {policy.timezone_label}"
    ready = [c.get("claim_id") or c.get("signal_id") for c in claims]
    review = evaluate_review_capacity(ready, roster, as_of=minute, as_of_utc=as_of_utc, policy=policy,
                                      staffing_known=bool(roster), work_order_refs=["QUEUE-AWAITING-DECISION"])
    backups = [r for r in roster if r["qualified"] and not r["assigned"]]
    preview = None
    if preview_backup and backups:
        what_if = [dict(r, assigned=True) if r in backups else r for r in roster]
        preview = evaluate_review_capacity(ready, what_if, as_of=minute, as_of_utc=as_of_utc, policy=policy,
                                           staffing_known=True, work_order_refs=["QUEUE-AWAITING-DECISION"])
        preview["is_preview"] = True
    # The dashboard total is counted after each claim has been evaluated on its own.
    evaluated = [c for c in claims if c.get("evaluation")]
    counts: Dict[str, int] = defaultdict(int)
    for c in evaluated:
        counts[c["evaluation"].get("label") or c["evaluation"].get("level")] += 1
    rank = {label: i for i, label in enumerate(FILING_LABEL_ORDER)}
    totals = [{"label": label, "count": counts[label], "tone": label_tone(label)}
              for label in sorted(counts, key=lambda x: rank.get(x, len(rank)))]

    def urgency(c):
        ev = c["evaluation"]
        return (rank.get(ev.get("label") or "", len(rank)), ev.get("deadline") or "9999")
    urgent = min(evaluated, key=urgency) if evaluated else None
    return {"as_of_local": as_of_local, "evaluated_at": as_of_utc.replace(tzinfo=timezone.utc).isoformat(),
            "review_capacity": review, "review_capacity_preview": preview,
            "filing_risk": _claim_result(urgent, as_of_local) if urgent else None,
            "filing_totals": totals, "claims_evaluated": len(evaluated),
            "roster": [{k: r[k] for k in ("id", "worker", "role", "qualified", "assigned")}
                       | {"shift": f"{clock(r['start'])}–{clock(r['stop'])}"} for r in roster],
            "policy": policy.summary()}


__all__ = [
    "POLICY", "Fact", "Receipt", "Route", "SeverityPolicy", "classify_filing_risk", "classify_review_capacity",
    "clock", "evaluate_review_capacity", "live_checks", "read_fact", "roster_rows", "staffed_slots",
]
