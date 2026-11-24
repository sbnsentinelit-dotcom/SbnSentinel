"""
Risk level evaluator: decides whether a signal is Critical, High or Moderate.

The level is calculated from the source data the connected systems send (payer / clearinghouse
remittance for claim denials, the EHR for no-shows and waiting times), not taken from a fixed
label per denial code. In the demo the same data comes from the synthetic feed; the logic is the
same either way.

Principles (from the auditor's severity reference):
1. Level = consequence x urgency: how much is at risk, and how much time is left compared with the
   time the fix needs.
2. Critical only on complete, verified evidence. If a required input is missing, the level is capped
   at High (or Moderate when the consequence itself is unknown) and the reason names the input.
3. Each signal is evaluated on its own data; evidence from other claims is never pooled.
4. Every result carries the level, reason, rule and policy version, the inputs used, the missing
   inputs and the evaluation time. It is re-evaluated at read time, so a deadline that comes
   closer raises the level by itself.

Claim denials use the payer filing-risk rule (auditor's Severity Synthetic Retest 2, see severity_checks):
per claim, a verified deadline, materiality, an exposed current plan and verified correction-route bounds give
High, "Critical — act now" or "Critical — escalate for containment"; a missing deadline is "Unable to
evaluate"; after the deadline without a receipt, "Verify filing outcome". The badge text is `label`.

Levels: Critical, High, Moderate. There is no Low.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

CRITICAL = "Critical"
HIGH = "High"
MODERATE = "Moderate"
LEVELS = (CRITICAL, HIGH, MODERATE)

CLAIM_DENIAL = "claim_denial"
NO_SHOW = "no_show"
WAIT_TIME = "wait_time"

RULE_IDS = {
    CLAIM_DENIAL: "RISK-CLAIM-DENIAL",
    NO_SHOW: "RISK-NO-SHOW",
    WAIT_TIME: "RISK-WAIT-TIME",
}
RULE_VERSION = "v1"


@dataclass(frozen=True)
class RiskPolicy:
    """Versioned thresholds. Synthetic demo values; a clinic can approve its own version."""
    policy_id: str = "POL-RISK-LEVEL"
    version: str = "v1"
    is_synthetic: bool = True
    currency: str = "AED"
    materiality_amount: float = 500.0          # amount at or above which a loss is material
    warning_window_days: float = 7.0           # deadline this close or closer -> High
    critical_buffer_days: float = 1.0          # days left <= correction time + buffer -> Critical
    correction_days: Dict[str, float] = field(default_factory=lambda: {
        "CO4": 1.0,    # add the missing modifier
        "CO50": 2.0,   # correct the diagnosis coding
        "CO29": 2.0,   # timely-filing appeal pack
        "CO16": 3.0,   # obtain and attach clinical documents
        "CO15": 5.0,   # retrospective prior authorization
    })
    # Correction-and-resubmission route per denial code: (shortest, longest) days. Synthetic demo bounds,
    # treated as verified; the longest equals correction_days. PR204 has no route (inventory incomplete).
    route_days: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "CO4": (0.5, 1.0), "CO50": (1.0, 2.0), "CO29": (1.0, 2.0), "CO16": (2.0, 3.0), "CO15": (3.0, 5.0),
    })
    route_bounds_verified: bool = True
    no_show_repeat_count: int = 2              # no-shows in the last 90 days -> High
    wait_moderate_minutes: int = 30
    wait_high_minutes: int = 45
    wait_critical_minutes: int = 60
    urgent_wait_critical_minutes: int = 30
    utc_offset_minutes: int = 240              # clinic local time for display (UAE = UTC+4)
    timezone_label: str = "UAE"

    def summary(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id, "version": self.version, "is_synthetic": self.is_synthetic,
            "currency": self.currency, "materiality_amount": self.materiality_amount,
            "warning_window_days": self.warning_window_days, "critical_buffer_days": self.critical_buffer_days,
            "correction_days": dict(self.correction_days), "no_show_repeat_count": self.no_show_repeat_count,
            "wait_minutes": {"moderate": self.wait_moderate_minutes, "high": self.wait_high_minutes,
                             "critical": self.wait_critical_minutes,
                             "urgent_critical": self.urgent_wait_critical_minutes},
        }


DEFAULT_POLICY = RiskPolicy()


@dataclass
class RiskEvaluation:
    level: str
    reason: str
    kind: str
    rule_id: str
    rule_version: str
    policy_id: str
    policy_version: str
    evaluated_at: datetime
    inputs: Dict[str, Any]
    missing_inputs: List[str]
    problem: str = ""
    recommended_action: str = ""
    expected_outcome: str = ""
    deadline: Optional[datetime] = None
    time_left: Optional[str] = None
    autopilot_eligible: bool = False
    autopilot_action: Optional[str] = None     # what Autopilot does: resubmit_claim | permitted_action
    label: Optional[str] = None                # badge text, e.g. "Critical — act now"
    timeline: List[Dict[str, str]] = field(default_factory=list)   # when the level changes (claims)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level, "label": self.label or self.level, "timeline": list(self.timeline),
            "reason": self.reason, "kind": self.kind,
            "problem": self.problem, "recommended_action": self.recommended_action,
            "expected_outcome": self.expected_outcome,
            "deadline": self.deadline.replace(tzinfo=timezone.utc).isoformat() if self.deadline else None,
            "time_left": self.time_left, "autopilot_eligible": self.autopilot_eligible,
            "autopilot_action": self.autopilot_action,
            "rule_id": self.rule_id, "rule_version": self.rule_version,
            "policy_id": self.policy_id, "policy_version": self.policy_version,
            "evaluated_at": self.evaluated_at.replace(tzinfo=timezone.utc).isoformat(),
            "inputs": self.inputs, "missing_inputs": list(self.missing_inputs),
        }


# --------------------------------------------------------------------------- helpers

def _utc(value: Any) -> Optional[datetime]:
    """Naive UTC datetime from an ISO string or datetime; None when missing or unreadable."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        t = value
    else:
        try:
            t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t


def _minutes(t: datetime) -> int:
    """Whole minutes since the epoch (the filing-risk core works on minutes)."""
    return int((t - datetime(1970, 1, 1)).total_seconds() // 60)


def _number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _days(n: float) -> str:
    n = round(n, 1)
    return f"{n:g} day" if n == 1 else f"{n:g} days"


def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def local_time(t: datetime, policy: RiskPolicy) -> str:
    """Clinic local date and time, e.g. '24 Nov 2026, 2:00 PM UAE'."""
    local = t + timedelta(minutes=policy.utc_offset_minutes)
    return f"{local.day} {local.strftime('%b %Y')}, {local.strftime('%I:%M %p').lstrip('0')} {policy.timezone_label}"


def time_left_text(deadline: datetime, now: datetime) -> str:
    """'2 days 4 hours left', '5 hours 10 minutes left', or 'deadline passed 1 day 2 hours ago'."""
    seconds = (deadline - now).total_seconds()
    passed = seconds < 0
    minutes = int(abs(seconds) // 60)
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} day" + ("" if days == 1 else "s"))
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if not days and (mins or not parts):
        parts.append(f"{mins} minute" + ("" if mins == 1 else "s"))
    span = " ".join(parts[:2])
    return f"deadline passed {span} ago" if passed else f"{span} left"


def _result(kind: str, level: str, reason: str, now: datetime, policy: RiskPolicy,
            inputs: Dict[str, Any], missing: List[str], **extra: Any) -> RiskEvaluation:
    if missing:
        reason += " Missing input: " + ", ".join(missing) + "."
    return RiskEvaluation(level=level, reason=reason, kind=kind, rule_id=RULE_IDS[kind], rule_version=RULE_VERSION,
                          policy_id=policy.policy_id, policy_version=policy.version, evaluated_at=now,
                          inputs=inputs, missing_inputs=missing, **extra)


# --------------------------------------------------------------------------- claim denial

def _list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return []


def evaluate_claim_denial(inputs: Dict[str, Any], now: datetime, policy: RiskPolicy = DEFAULT_POLICY) -> RiskEvaluation:
    """Source: payer / clearinghouse remittance and the EHR. Inputs: claim_id, payer, denial_code,
    denial_reason, denied_amount, currency, remittance_date, resubmission_deadline, required_evidence
    (what the payer asks for) and evidence_available (what the EHR already holds)."""
    code = (inputs.get("denial_code") or "").upper() or None
    claim_id = inputs.get("claim_id") or "this claim"
    payer = inputs.get("payer") or "the payer"
    denial_reason = (inputs.get("denial_reason") or "").strip().rstrip(".")
    amount = _number(inputs.get("denied_amount"))
    currency = (inputs.get("currency") or "").upper() or None
    deadline = _utc(inputs.get("resubmission_deadline"))
    required = _list(inputs.get("required_evidence"))
    available = set(_list(inputs.get("evidence_available")))
    in_ehr = [e for e in required if e in available]
    not_in_ehr = [e for e in required if e not in available]
    correction = policy.correction_days.get(code) if code else None
    used = {"claim_id": inputs.get("claim_id"), "payer": inputs.get("payer"), "denial_code": code,
            "denial_reason": denial_reason or None, "denied_amount": amount, "currency": currency,
            "remittance_date": inputs.get("remittance_date"),
            "resubmission_deadline": deadline.isoformat() + "Z" if deadline else None,
            "required_evidence": required, "evidence_available": sorted(available),
            "correction_days": correction, "materiality_amount": policy.materiality_amount,
            "warning_window_days": policy.warning_window_days}
    missing: List[str] = []

    # What went wrong, from the payer's data.
    problem = f"Claim {claim_id} was denied by {payer}"
    problem += f" with {code}" if code else ""
    problem += f": {denial_reason}." if denial_reason else "."
    if required:
        problem += f" Evidence the payer needs: {', '.join(required)}."

    # What to do, from the payer and EHR data.
    by = f" before {local_time(deadline, policy)}" if deadline else ""
    if deadline and now > deadline:
        action = (f"Check {payer}'s portal for a resubmission receipt for claim {claim_id}; if there is none, "
                  "file an appeal with the proof of the original timely submission.")
    elif required and not not_in_ehr:
        action = f"Attach {', '.join(required)} from the EHR and resubmit claim {claim_id} to {payer}{by}."
    elif required:
        action = (f"Obtain {', '.join(not_in_ehr)} (not yet in the EHR)"
                  + (f", attach {', '.join(in_ehr)}" if in_ehr else "")
                  + f" and resubmit claim {claim_id} to {payer}{by}.")
    else:
        action = f"Correct claim {claim_id} for {code or 'the denial'} and resubmit it to {payer}{by}."
    if deadline is None:
        action = f"Confirm the resubmission deadline with {payer}. " + action
    shown = _money(amount, currency or policy.currency) if amount is not None else "the denied amount"
    outcome = (f"Claim {claim_id} resubmitted with the required evidence before the deadline; "
               f"{shown} recoverable if {payer} accepts it.")

    time_left = time_left_text(deadline, now) if deadline else None
    when = f" ({time_left}; deadline {local_time(deadline, policy)})" if deadline else ""
    extra = dict(problem=problem, recommended_action=action, expected_outcome=outcome, deadline=deadline,
                 time_left=time_left)

    def done(level: str, reason: str, label: Optional[str] = None, action: Optional[str] = None,
             timeline: Optional[List[Dict[str, str]]] = None) -> RiskEvaluation:
        # Autopilot acts only on "Critical — act now" (a verified deadline that the fastest route can still
        # meet). It first collects the evidence; the payer connector refuses a resubmission with anything
        # missing. "Escalate for containment" cannot be fixed in time, so it goes to a person.
        eligible = label == "Critical — act now" and deadline is not None and now <= deadline
        fields = dict(extra, recommended_action=action or extra["recommended_action"])
        return _result(CLAIM_DENIAL, level, reason, now, policy, used, missing, autopilot_eligible=eligible,
                       autopilot_action="resubmit_claim" if eligible else None, label=label or level,
                       timeline=timeline or [], **fields)

    # Payer filing risk (auditor's Severity Synthetic Retest 2): this one claim and its resubmission
    # obligation, from the facts known now; never pooled with other claims.
    from app.services.severity_checks import Fact, Receipt, classify_filing_risk
    key = ("claim", claim_id)
    now_m = _minutes(now)
    facts = [Fact("deadline", _minutes(deadline), key)] if deadline is not None else []
    if amount is None:
        missing.append("denied amount")
    elif currency and currency != policy.currency:
        missing.append(f"amount in {policy.currency}")
    else:
        facts.append(Fact("material", amount >= policy.materiality_amount, key))
    days_left = (deadline - now).total_seconds() / 86400 if deadline is not None else None
    # The current plan (the normal billing workflow) threatens timely filing inside the warning window.
    facts.append(Fact("plan_exposed", days_left is not None and days_left <= policy.warning_window_days, key))
    routes, complete, lead = claim_routes(inputs, code, policy)
    facts.append(Fact("inventory_complete", complete, key))
    if routes is not None:
        facts.append(Fact("routes", routes, key))
    receipts = []
    receipt = inputs.get("filing_receipt") or {}
    event_at, known_at = _utc(receipt.get("event_at")), _utc(receipt.get("known_at"))
    if event_at and known_at:
        receipts.append(Receipt(kind=receipt.get("kind") or "policy-required-receipt", key=key,
                                event_at=_minutes(event_at), known_at=_minutes(known_at),
                                verified=bool(receipt.get("verified")), ref=receipt.get("ref") or ""))
    filing = classify_filing_risk(facts, now_m, key=key, receipts=receipts, alert_lead=lead)
    used.update({"filing_code": filing, "filing_receipt": receipt.get("ref"), "alert_lead_minutes": lead,
                 "recovery_routes": [{"name": r.name, "shortest_minutes": r.shortest, "longest_minutes": r.longest,
                                      "verified": r.hard_bounds} for r in (routes or ())],
                 "route_inventory_complete": complete})

    if filing == "UNASSESSED: deadline":
        missing.append("payer resubmission deadline")
        return done(MODERATE, "The payer resubmission deadline has not been received or verified, so the filing "
                    "risk cannot be evaluated. Verify the deadline.", "Unable to evaluate — filing deadline unverified")
    if filing == "VERIFIED TIMELY":
        ref = receipts[0].ref
        return done(MODERATE, f"Receipt {ref} shows the claim was resubmitted on or before the payer deadline "
                    f"({local_time(deadline, policy)}).", "Filed on time",
                    action=f"No action needed: claim {claim_id} was resubmitted to {payer} in time.")
    if filing == "LATE RECEIPT: verify earlier filing":
        return done(HIGH, f"The only resubmission receipt for claim {claim_id} is after the payer deadline "
                    f"({local_time(deadline, policy)}).", "Verify filing outcome",
                    action=f"Verify with {payer} whether an earlier qualifying submission of claim {claim_id} exists.")
    if filing == "VERIFY OUTCOME":
        return done(HIGH, f"The payer resubmission deadline has passed ({time_left}; deadline "
                    f"{local_time(deadline, policy)}). A missing resubmission receipt does not prove the claim was "
                    "not resubmitted; verify the outcome.", "Verify filing outcome")
    if filing == "UNASSESSED: exposure":
        return done(MODERATE, f"The denied amount was not received in {policy.currency}, so materiality cannot "
                    f"be assessed and the level cannot be raised above Moderate{when}.",
                    "Unable to evaluate — exposure unverified")
    if filing == "NO MATERIAL FILING ALERT":
        if amount < policy.materiality_amount:
            return done(MODERATE, f"Denied amount {shown} is below the materiality threshold of "
                        f"{_money(policy.materiality_amount, policy.currency)}{when}.", "No active filing alert")
        return done(MODERATE, f"Denied amount {shown} is material, but the payer resubmission deadline is beyond the "
                    f"{_days(policy.warning_window_days)} warning window{when}.", "No active filing alert")
    if filing in ("HIGH: verify recovery routes", "HIGH: verify route bounds"):
        if not complete or routes is None:
            missing.append(f"recovery route for claim {claim_id}" if inputs.get("recovery_routes") is not None
                           else f"correction route for denial code {code or 'unknown'}")
            why = "the list of recovery routes is incomplete"
        else:
            why = "the recovery route times are estimates, not verified bounds"
        return done(HIGH, f"Denied amount {shown} is material and the deadline is inside the "
                    f"{_days(policy.warning_window_days)} warning window{when}, but {why}, so Critical cannot be "
                    "established.", "High — verify recovery options now")
    timeline = filing_timeline(deadline, routes, lead, policy)
    if filing == "HIGH: monitor last start":
        d_m = _minutes(deadline)
        best = next(r for r in routes if _route_start(r, now_m) + r.shortest <= d_m and d_m - r.longest > now_m + lead
                    and (r.earliest_start is None or r.earliest_start <= d_m - r.longest))
        safe = deadline - timedelta(minutes=best.longest)
        return done(HIGH, f"Denied amount {shown} is material and the payer resubmission deadline is inside the "
                    f"{_days(policy.warning_window_days)} warning window{when}. Starting the {best.name.lower()} by "
                    f"{local_time(safe, policy)} allows the full {_duration(best.longest)}.", "High",
                    timeline=timeline)
    if filing == "CRITICAL: containment":
        fastest = min(routes, key=lambda r: r.shortest) if routes else None
        why = (f"even the fastest {fastest.name.lower()} needs {_duration(fastest.shortest)}" if fastest
               else "no recovery route remains")
        return done(CRITICAL, f"Denied amount {shown} is material. Only {time_left} before the payer resubmission "
                    f"deadline ({local_time(deadline, policy)}) and {why}, so it cannot finish in time.",
                    "Critical — escalate for containment",
                    action=(f"Escalate now: ask {payer} about an extension or other relief for claim {claim_id}. "
                            "Do not record a write-off; verify the outcome after the deadline."), timeline=timeline)
    fastest = min((r for r in routes if _route_start(r, now_m) + r.shortest <= _minutes(deadline)),
                  key=lambda r: r.shortest)
    gather = f" ({', '.join(not_in_ehr)} still to be obtained)" if not_in_ehr else ""
    only = (", so only the fastest verified completion can still meet it"
            if _minutes(deadline) - now_m < fastest.longest else "")
    urgency = "it must start today" if fastest.longest >= 1440 else "it must start now"
    return done(CRITICAL, f"Denied amount {shown} is material. Only {time_left} before the payer resubmission "
                f"deadline ({local_time(deadline, policy)}) and the {fastest.name.lower()} needs "
                f"{_duration(fastest.shortest)} to {_duration(fastest.longest)}{gather}{only}; {urgency}.",
                "Critical — act now", timeline=timeline)


def _route_start(route, now_m: int) -> int:
    return now_m if route.earliest_start is None else max(now_m, route.earliest_start)


def _duration(minutes: int) -> str:
    if minutes % 1440 == 0:
        return _days(minutes // 1440)
    if minutes >= 60 and minutes % 60 == 0:
        h = minutes // 60
        return f"{h} hour" + ("" if h == 1 else "s")
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def claim_routes(inputs: Dict[str, Any], code: Optional[str], policy: RiskPolicy):
    """(routes, inventory complete, immediate-action lead in minutes) for one claim. A claim's own
    recovery routes (from the source system) come first; otherwise the policy's route for its denial code."""
    from app.services.severity_checks import Route
    data = inputs.get("recovery_routes")
    if data is not None:
        routes = []
        for r in data:
            try:
                routes.append(Route(int(r["shortest_minutes"]), int(r["longest_minutes"]),
                                    hard_bounds=bool(r.get("verified")), name=r.get("name") or "Recovery route",
                                    ref=r.get("ref") or ""))
            except (KeyError, TypeError, ValueError):
                return None, False, int(inputs.get("alert_lead_minutes") or 0)
        return tuple(routes), inputs.get("route_inventory_complete") is True, int(inputs.get("alert_lead_minutes") or 0)
    bounds = policy.route_days.get(code) if code else None
    lead = int(policy.critical_buffer_days * 1440)
    if not bounds:
        return None, False, lead
    route = Route(int(bounds[0] * 1440), int(bounds[1] * 1440), hard_bounds=policy.route_bounds_verified,
                  name=f"{code} correction and resubmission")
    return (route,), True, lead


def filing_timeline(deadline: Optional[datetime], routes, lead: int, policy: RiskPolicy) -> List[Dict[str, str]]:
    """When this claim's level changes, from its own deadline and verified routes (no clock is faked)."""
    if deadline is None or not routes:
        return []
    d = _minutes(deadline)
    usable = [r for r in routes if r.earliest_start is None or r.earliest_start <= d - r.longest]
    if not usable:
        return []
    safe = max(d - r.longest for r in usable)
    last = max(d - r.shortest for r in routes)
    window = d - int(policy.warning_window_days * 1440)

    def t(minute: int) -> str:
        return local_time(datetime(1970, 1, 1) + timedelta(minutes=minute), policy)
    return [{"label": "No active filing alert", "until": t(window)},
            {"label": "High", "from": t(window), "until": t(safe - lead - 1)},
            {"label": "Critical — act now", "from": t(safe - lead), "until": t(last)},
            {"label": "Critical — escalate for containment", "from": t(last + 1), "until": t(d)},
            {"label": "Verify filing outcome", "from": t(d + 1)}]


# --------------------------------------------------------------------------- no-show

def evaluate_no_show(inputs: Dict[str, Any], now: datetime, policy: RiskPolicy = DEFAULT_POLICY) -> RiskEvaluation:
    """Source: EHR appointment. Inputs: visit_priority, prior_no_shows_90d, visit_value, currency."""
    priority = (inputs.get("visit_priority") or "").strip() or None
    prior = inputs.get("prior_no_shows_90d")
    prior = int(prior) if isinstance(prior, (int, float)) or (isinstance(prior, str) and prior.isdigit()) else None
    value = _number(inputs.get("visit_value"))
    currency = (inputs.get("currency") or policy.currency).upper()
    used = {"visit_priority": priority, "prior_no_shows_90d": prior, "visit_value": value, "currency": currency,
            "appointment_at": inputs.get("appointment_at"), "no_show_repeat_count": policy.no_show_repeat_count,
            "materiality_amount": policy.materiality_amount}
    missing = [name for name, v in (("visit priority", priority), ("no-show history", prior),
                                    ("visit value", value)) if v is None]
    appt = _utc(inputs.get("appointment_at"))
    kind_text = (priority or "unclassified").lower()
    article = "an" if kind_text[:1] in "aeiou" else "a"
    problem = (f"The patient missed {article} {kind_text} visit"
               + (f" at {local_time(appt, policy)}" if appt else "")
               + (f"; {prior} no-show{'s' if prior != 1 else ''} in the last 90 days" if prior is not None else "")
               + (f"; visit value {_money(value, currency)}" if value is not None else "") + ".")
    outcome = "Patient contacted and the visit rebooked; the freed slot is offered to another patient."

    def done(level: str, reason: str, action: str) -> RiskEvaluation:
        return _result(NO_SHOW, level, reason, now, policy, used, missing, problem=problem,
                       recommended_action=action, expected_outcome=outcome, autopilot_eligible=level == CRITICAL,
                       autopilot_action="permitted_action" if level == CRITICAL else None)

    if priority and priority.lower() == "urgent":
        return done(CRITICAL, "The missed visit was marked Urgent; the patient must be contacted the same day for "
                    "safety.", "Call the patient today and rebook within 24 hours; tell the treating provider if the "
                    "patient cannot be reached.")
    if prior is not None and prior >= policy.no_show_repeat_count:
        return done(HIGH, f"The patient has {prior} no-shows in the last 90 days (threshold "
                    f"{policy.no_show_repeat_count}); a repeated pattern needs follow-up.",
                    "Call the patient, rebook the visit and apply the clinic's repeat no-show policy.")
    if value is not None and currency == policy.currency and value >= policy.materiality_amount:
        return done(HIGH, f"The missed visit was worth {_money(value, currency)}, at or above the materiality "
                    "threshold.", "Call the patient to rebook and offer the freed slot to the waiting list today.")
    return done(MODERATE, "A routine visit was missed with no repeated pattern and no material value at risk.",
                "Send the patient a reschedule message with the next free slots.")


# --------------------------------------------------------------------------- waiting time

def evaluate_wait_time(inputs: Dict[str, Any], now: datetime, policy: RiskPolicy = DEFAULT_POLICY) -> RiskEvaluation:
    """Source: EHR / front desk. Inputs: wait_minutes, patient_priority, room_available."""
    wait = _number(inputs.get("wait_minutes"))
    priority = (inputs.get("patient_priority") or "").strip() or None
    room = inputs.get("room_available")
    room = room if isinstance(room, bool) else None
    used = {"wait_minutes": wait, "patient_priority": priority, "room_available": room,
            "thresholds": policy.summary()["wait_minutes"]}
    missing = [name for name, v in (("patient priority", priority), ("room availability", room)) if v is None]
    room_text = {True: "a room is free", False: "no room is free", None: "room availability unknown"}[room]
    problem = ((f"The patient has waited {int(wait)} minutes" if wait is not None else "The waiting time is unknown")
               + f" ({(priority or 'unknown').lower()} priority; {room_text}).")
    outcome = "Waiting time reduced and the patient informed."

    def done(level: str, reason: str, action: str, miss: Optional[List[str]] = None) -> RiskEvaluation:
        return _result(WAIT_TIME, level, reason, now, policy, used, miss if miss is not None else missing,
                       problem=problem, recommended_action=action, expected_outcome=outcome,
                       autopilot_eligible=level == CRITICAL,
                       autopilot_action="permitted_action" if level == CRITICAL else None)

    if wait is None:
        return done(MODERATE, "The waiting time was not received; the level cannot be raised above Moderate.",
                    "Check the patient's waiting time at the front desk.", ["waiting time"] + missing)
    w = int(wait)
    critical_action = "Move the patient to the next free room or provider now and inform the clinic administrator."
    if w >= policy.wait_critical_minutes:
        return done(CRITICAL, f"The patient has waited {w} minutes, at or above {policy.wait_critical_minutes} minutes.",
                    critical_action)
    if priority and priority.lower() == "urgent" and w >= policy.urgent_wait_critical_minutes:
        return done(CRITICAL, f"An Urgent patient has waited {w} minutes, at or above "
                    f"{policy.urgent_wait_critical_minutes} minutes.", critical_action)
    if w >= policy.wait_high_minutes:
        return done(HIGH, f"The patient has waited {w} minutes ({policy.wait_high_minutes}-"
                    f"{policy.wait_critical_minutes - 1} minute band).",
                    "Give the patient the next free room and update them on the wait.")
    if w >= policy.wait_moderate_minutes and room is False:
        return done(HIGH, f"The patient has waited {w} minutes and no room is available.",
                    "Free or reassign a room for this patient and update them on the wait.")
    return done(MODERATE, f"The patient has waited {w} minutes (below {policy.wait_high_minutes} minutes) and a room "
                "is available or availability is unknown.", "Update the patient on the expected wait.")


# --------------------------------------------------------------------------- dispatch

EVALUATORS = {CLAIM_DENIAL: evaluate_claim_denial, NO_SHOW: evaluate_no_show, WAIT_TIME: evaluate_wait_time}


def kind_for_rule(rule_id: Optional[str]) -> Optional[str]:
    """Which risk rule applies to a governance rule: UAE claim denials, no-show, waiting time."""
    if not rule_id:
        return None
    if rule_id.startswith("RULE-UAE-"):
        return CLAIM_DENIAL
    if rule_id == "RULE-SCH-001":
        return NO_SHOW
    if rule_id == "RULE-SCH-002":
        return WAIT_TIME
    return None


def evaluate(kind: str, inputs: Optional[Dict[str, Any]], now: Optional[datetime] = None,
             policy: RiskPolicy = DEFAULT_POLICY) -> RiskEvaluation:
    now = (now or datetime.utcnow()).replace(second=0, microsecond=0)
    return EVALUATORS[kind](inputs or {}, now, policy)


def normalize_level(level: Optional[str]) -> Optional[str]:
    """Older rows used 'Medium' for the same level as 'Moderate'."""
    return MODERATE if level == "Medium" else level


def current_level(stored_level: Optional[str], metadata: Optional[Dict[str, Any]],
                  now: Optional[datetime] = None) -> Optional[str]:
    """The level shown now: re-evaluated from the stored source inputs when the signal has them."""
    meta = metadata or {}
    kind = meta.get("risk_kind")
    if kind in EVALUATORS and "risk_inputs" in meta:
        return evaluate(kind, meta.get("risk_inputs"), now).level
    return normalize_level(stored_level)


def current_evaluation(metadata: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    meta = metadata or {}
    kind = meta.get("risk_kind")
    if kind in EVALUATORS and "risk_inputs" in meta:
        return evaluate(kind, meta.get("risk_inputs"), now).to_dict()
    return None
