"""
The auditor's Severity Synthetic Retest 2 (retest.py, 607 reference checks) run against Sentinel's
integrated evaluators in app.services.severity_checks, plus the dashboard acceptance screens.

Reference times are whole minutes after 16:00 (the core is unit-agnostic); the acceptance tests use
local minutes after midnight, as the demo API does.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from fractions import Fraction
from itertools import combinations, product
from math import isclose, sqrt

import pytest

from app.services import severity_checks as sc
from app.services.severity_checks import (
    Fact as _Fact, Receipt as _Receipt, Route, classify_filing_risk, classify_review_capacity, staffed_slots,
)

pytestmark = pytest.mark.unit

COUNTS = defaultdict(int)
EXPECTED_COUNTS = {
    "queue examples": 13, "exhaustive roster masks": 256, "deadline minute sweep": 27,
    "evidence and alternatives": 16, "identity and receipts": 16, "exhaustive two-claim joins": 256,
    "duration sensitivity": 20, "quantum simulation sanity": 3,
}


def check(group, label, actual, expected):
    assert actual == expected, f"{group}: {label}: got {actual!r}, expected {expected!r}"
    COUNTS[group] += 1


KEY = ("Sharjah-demo", "Payer-A", "C-01", "original-filing", "demo-policy-v1")


def Fact(field, value, key=KEY, known_at=0, valid_until=90):
    return _Fact(field, value, key, known_at, valid_until)


def Receipt(kind="policy-required-receipt", key=KEY, event_at=60, known_at=60, verified=True):
    return _Receipt(kind, key, event_at, known_at, verified)


def queue(claims, roster, *, staffing_known=True, duration=15, now=30, end=60):
    return classify_review_capacity(claims, roster, now=now, end=end, staffing_known=staffing_known,
                                    duration=duration)


def external(facts, now, *, key=KEY, receipts=(), alert_lead=0):
    return classify_filing_risk(facts, now, key=key, receipts=receipts, alert_lead=alert_lead)


BASE = [Fact("deadline", 60), Fact("material", True), Fact("plan_exposed", True),
        Fact("inventory_complete", True), Fact("routes", (Route(),))]


def altered(facts, field, value):
    return [replace(f, value=value) if f.field == field else f for f in facts]


# --------------------------------------------------------------------------- reference groups

def test_queue_examples_and_roster_masks():
    one = [("A", 30, 60, True, True)]
    three = ["1", "2", "3"]
    for description, ids, extra, expected in [
        ("comfortable", three[:1], [], ("ON TRACK", 0)),
        ("zero buffer", three[:2], [], ("LOW", 0)),
        ("three reviews", three, [], ("MODERATE", 1)),
        ("duplicate claim", ["1", "2", "2"], [], ("LOW", 0)),
        ("double-booked worker ID", three, one, ("MODERATE", 1)),
        ("hypothetical cover", three, [("B", 45, 60, True, False)], ("MODERATE", 1)),
        ("unqualified cover", three, [("B", 45, 60, False, True)], ("MODERATE", 1)),
        ("confirmed cover 16:45", three, [("B", 45, 60, True, True)], ("LOW", 0)),
        ("confirmed cover 16:46", three, [("B", 46, 60, True, True)], ("MODERATE", 1)),
        ("fragmented cover", three, [("B", 30, 40, True, True), ("B", 50, 60, True, True)], ("MODERATE", 1)),
        ("no pending reviews", [], [], ("ON TRACK", 0)),
    ]:
        check("queue examples", description, queue(ids, one + extra), expected)
    check("queue examples", "stale roster", queue(three, one, staffing_known=False), ("UNASSESSED", None))
    check("queue examples", "no duration", queue(three, one, duration=None), ("UNASSESSED", None))

    # Independent set-packing oracle over every 8-minute availability pattern.
    for mask in range(256):
        available = {t for t in range(8) if mask & (1 << t)}
        rows = [("A", t, t + 1, True, True) for t in available]
        starts = [i for i in range(6) if set(range(i, i + 3)) <= available]
        possible = [n for n in range(3) for plan in combinations(starts, n)
                    if all(b - a >= 3 for a, b in zip(plan, plan[1:]))]
        check("exhaustive roster masks", str(mask), staffed_slots(rows, 0, 8, 3), max(possible))
    assert COUNTS["queue examples"] == 13 and COUNTS["exhaustive roster masks"] == 256


def test_deadline_sweep_evidence_and_alternatives():
    for t in range(35, 62):
        if t < 42:
            expect = "HIGH: monitor last start"
        elif t <= 48:
            expect = "CRITICAL: act now"
        elif t <= 60:
            expect = "CRITICAL: containment"
        else:
            expect = "VERIFY OUTCOME"
        check("deadline minute sweep", str(t), external(BASE, t), expect)
    cases = [
        ("unknown route inventory", altered(BASE, "inventory_complete", False), 49, "HIGH: verify recovery routes"),
        ("confirmed empty inventory", altered(BASE, "routes", ()), 41, "CRITICAL: containment"),
        ("bounds are estimates", altered(BASE, "routes", (Route(hard_bounds=False),)), 49, "HIGH: verify route bounds"),
        ("contradictory bounds", altered(BASE, "routes", (Route(20, 10),)), 49, "HIGH: verify route bounds"),
        ("alternative route", altered(BASE, "routes", (Route(), Route(3, 6))), 49, "HIGH: monitor last start"),
        ("alternate worker starts too late", altered(BASE, "routes", (Route(), Route(3, 6, 59))), 49,
         "CRITICAL: containment"),
        ("extension confirmed", altered(BASE, "deadline", 75), 49, "HIGH: monitor last start"),
        ("materiality unverified", [f for f in BASE if f.field != "material"], 49, "UNASSESSED: exposure"),
        ("no documented materiality trigger", altered(BASE, "material", False), 49, "NO MATERIAL FILING ALERT"),
        ("safe current plan", altered(BASE, "plan_exposed", False), 44, "NO MATERIAL FILING ALERT"),
        ("due time conflict", BASE + [Fact("deadline", 65)], 44, "UNASSESSED: deadline"),
        ("expired deadline evidence", [replace(f, valid_until=44) if f.field == "deadline" else f for f in BASE], 44,
         "UNASSESSED: deadline"),
        ("future extension hidden", BASE + [Fact("deadline", 75, known_at=46)], 44, "CRITICAL: act now"),
        ("replayed duplicate evidence", BASE + BASE, 44, "CRITICAL: act now"),
    ]
    for label, facts, now, expected in cases:
        check("evidence and alternatives", label, external(facts, now), expected)
    check("evidence and alternatives", "approved two-minute warning lead",
          external(BASE, 40, alert_lead=2), "CRITICAL: act now")
    missing = [f for f in BASE if f.field != "deadline"]
    check("evidence and alternatives", "internal finding survives unknown deadline",
          (queue(["1", "2", "3"], [("A", 30, 60, True, True)])[0], external(missing, 44)),
          ("MODERATE", "UNASSESSED: deadline"))
    assert COUNTS["deadline minute sweep"] == 27 and COUNTS["evidence and alternatives"] == 16


def test_identity_receipts_and_two_claim_joins():
    for pos, name in enumerate(["clinic", "payer", "claim", "obligation", "policy version"]):
        other = list(KEY)
        other[pos] = "other"
        mixed = [replace(f, key=tuple(other)) if f.field == "material" else f for f in BASE]
        check("identity and receipts", f"mixed {name} evidence", external(mixed, 44), "UNASSESSED: exposure")
        check("identity and receipts", f"mixed {name} receipt",
              external(BASE, 62, receipts=[Receipt(key=tuple(other))]), "VERIFY OUTCOME")
    for label, now, receipt, status in [
        ("exactly at cutoff", 60, Receipt(), "VERIFIED TIMELY"),
        ("unobserved timely receipt", 61, Receipt(known_at=62), "VERIFY OUTCOME"),
        ("late-arriving timely receipt", 62, Receipt(known_at=62), "VERIFIED TIMELY"),
        ("late event", 62, Receipt(event_at=61, known_at=62), "LATE RECEIPT: verify earlier filing"),
        ("local billing task", 62, Receipt(kind="staff-marked-billed"), "VERIFY OUTCOME"),
        ("unverified qualifying event", 62, Receipt(verified=False), "VERIFY OUTCOME"),
    ]:
        check("identity and receipts", label, external(BASE, now, receipts=[receipt]), status)
    # Every pair of four binary flags; a per-claim oracle requires all four on that claim.
    bits = list(product((False, True), repeat=4))
    mismatch = 0
    for a, b in product(bits, repeat=2):
        facts, keys = [], []
        for i, profile in enumerate((a, b)):
            key = (*KEY[:2], f"C-{i}", *KEY[3:])
            keys.append(key)
            d, m, e, u = profile
            if d:
                facts.append(Fact("deadline", 60, key))
            routes = (Route(),) if u else (Route(2, 3),)
            facts.extend([Fact("material", m, key), Fact("plan_exposed", e, key),
                          Fact("inventory_complete", True, key), Fact("routes", routes, key)])
        actual = sum(external(facts, 44, key=k).startswith("CRITICAL") for k in keys)
        expected = int(all(a)) + int(all(b))
        check("exhaustive two-claim joins", str((a, b)), actual, expected)
        pooled = all(x or y for x, y in zip(a, b))
        mismatch += int(pooled and expected == 0)
    assert mismatch == 50  # the unsafe pooled rule would raise a false Critical in 50/256 pairs
    assert COUNTS["identity and receipts"] == 16 and COUNTS["exhaustive two-claim joins"] == 256


def test_duration_sensitivity_and_quantum_sanity():
    durations = list(product([12, 18], repeat=3))
    for start, count in [(42, 6), (45, 3), (48, 3), (49, 0)]:
        flags = [a + b <= 30 and c <= 60 - start for a, b, c in durations]
        check("duration sensitivity", f"backup at 16:{start}", sum(flags), count)
    for label, scenarios, probability in [
        ("lower bound", [(18, 18, 12), (12, 12, 18)], Fraction(0)),
        ("upper bound", [(12, 12, 12), (18, 18, 18)], Fraction(1, 2)),
    ]:
        for i in range(3):
            check("duration sensitivity", f"{label}: marginal {i}", sum(s[i] == 12 for s in scenarios), 1)
        actual = Fraction(sum(a + b <= 30 and c <= 15 for a, b, c in scenarios), len(scenarios))
        check("duration sensitivity", label, actual, probability)
    for jobs in durations:
        primary1, primary2, backup = jobs
        success = primary1 + primary2 <= 30 and backup <= 15
        check("duration sensitivity", f"success implies short backup {jobs}", (not success) or backup == 12, True)
    good = [a + b <= 30 and c <= 15 for a, b, c in durations]
    uniform = [1 / sqrt(8)] * 8
    flipped = [-x if mark else x for x, mark in zip(uniform, good)]
    after = [2 * sum(flipped) / 8 - x for x in flipped]
    for name, amplitudes, target in [("initial", uniform, 3 / 8), ("amplified", after, 27 / 32)]:
        hits = sum(x * x for x, mark in zip(amplitudes, good) if mark)
        check("quantum simulation sanity", name, isclose(hits, target), True)
    check("quantum simulation sanity", "normalization", isclose(sum(x * x for x in after), 1), True)
    assert COUNTS["duration sensitivity"] == 20 and COUNTS["quantum simulation sanity"] == 3


def test_all_607_reference_checks_ran():
    # The groups above run first in file order; together they make the auditor's 607 checks.
    if sum(COUNTS.values()) == 0:
        pytest.skip("run with the whole module")
    assert dict(COUNTS) == EXPECTED_COUNTS
    assert sum(COUNTS.values()) == 607


# --------------------------------------------------------------------------- acceptance on claim data

# The auditor's example as a clinic's claim: payer deadline 5:00 PM UAE (13:00 UTC), material amount,
# one verified recovery route of 12-18 minutes, zero advance warning lead.
DEADLINE = datetime(2026, 11, 24, 13, 0)


def five_pm_claim(verified=True, deadline=DEADLINE):
    return {"claim_id": "CLM2026-DXB-0006", "payer": "Daman", "denial_code": "CO29", "denied_amount": "760.00",
            "currency": "AED", "resubmission_deadline": deadline.isoformat() + "Z",
            "required_evidence": ["Proof of original timely submission (claim submission receipt)"],
            "evidence_available": ["Proof of original timely submission (claim submission receipt)"],
            "recovery_routes": [{"name": "Electronic dispute pack submission", "shortest_minutes": 12,
                                 "longest_minutes": 18, "verified": verified}],
            "route_inventory_complete": True, "alert_lead_minutes": 0}


def at(hh, mm):
    return datetime(2026, 11, 24, hh - 4, mm)   # local UAE -> UTC


def _claim(inputs, now):
    from app.services.risk_level_evaluator import CLAIM_DENIAL, evaluate
    return evaluate(CLAIM_DENIAL, inputs, now)


def test_acceptance_screens_on_the_claim_data():
    r = _claim(five_pm_claim(), at(16, 41))
    assert r.label == "High" and "4:42 PM UAE allows the full 18 minutes" in r.reason
    for minute in range(42, 49):
        assert _claim(five_pm_claim(), at(16, minute)).label == "Critical — act now"
    assert "only the fastest verified completion" in _claim(five_pm_claim(), at(16, 48)).reason
    r = _claim(five_pm_claim(), at(16, 49))
    assert r.label == "Critical — escalate for containment"
    assert "needs 12 minutes" in r.reason and r.autopilot_eligible is False
    assert _claim(five_pm_claim(), at(17, 1)).label == "Verify filing outcome"


def test_changing_route_data_changes_the_conclusion():
    assert _claim(five_pm_claim(verified=False), at(16, 48)).label == "High — verify recovery options now"
    assert _claim(five_pm_claim(verified=False), at(16, 49)).label == "High — verify recovery options now"
    incomplete = dict(five_pm_claim(), route_inventory_complete=False)
    assert _claim(incomplete, at(16, 49)).label == "High — verify recovery options now"


def test_level_timeline_comes_from_the_claim_data():
    tl = {row["label"]: row for row in _claim(five_pm_claim(), at(16, 30)).to_dict()["timeline"]}
    assert tl["High"]["until"].endswith("4:41 PM UAE")
    assert tl["Critical — act now"]["from"].endswith("4:42 PM UAE") and tl["Critical — act now"]["until"].endswith("4:48 PM UAE")
    assert tl["Critical — escalate for containment"]["from"].endswith("4:49 PM UAE")
    assert tl["Verify filing outcome"]["from"].endswith("5:01 PM UAE")


def test_badge_result_explains_itself():
    r = _claim(five_pm_claim(), at(16, 48)).to_dict()
    for key in ("level", "label", "reason", "recommended_action", "rule_id", "rule_version", "policy_id",
                "policy_version", "evaluated_at", "inputs", "timeline"):
        assert r[key], key
    assert r["inputs"]["recovery_routes"][0]["shortest_minutes"] == 12


# --------------------------------------------------------------------------- review capacity on roster data

NOW_UTC = datetime(2026, 11, 24, 12, 30)   # 4:30 PM UAE


def roster(backup_assigned=False):
    return sc.roster_rows([
        {"id": 1, "reviewer": "Biller A", "role": "Claims reviewer", "shift_start": "16:30", "shift_end": "17:00",
         "qualified": True, "assigned": True},
        {"id": 2, "reviewer": "Biller B", "role": "Backup reviewer", "shift_start": "16:45", "shift_end": "17:00",
         "qualified": True, "assigned": backup_assigned},
    ])


def claims(n):
    return [{"signal_id": f"S{i}", "claim_id": f"C{i}", "evaluation": None} for i in range(n)]


def test_review_capacity_preview_never_changes_the_live_result():
    d = sc.live_checks(claims(3), roster(), preview_backup=True, now_utc=NOW_UTC)
    assert d["review_capacity"]["label"] == "Moderate — 1 review may miss the internal cutoff"
    assert d["review_capacity"]["assumption"] == "Assumes 15 minutes per review."
    assert d["review_capacity_preview"]["label"] == "Low — no spare time"
    assert "No spare time" in d["review_capacity_preview"]["reason"]
    assigned = sc.live_checks(claims(3), roster(backup_assigned=True), now_utc=NOW_UTC)
    assert assigned["review_capacity"]["label"] == "Low — no spare time"


def test_review_capacity_without_roster_is_unable_to_evaluate():
    d = sc.live_checks(claims(2), [], now_utc=NOW_UTC)
    assert d["review_capacity"]["label"] == "Unable to evaluate — current staffing missing"
    assert d["review_capacity"]["missing_input"] == "current staffing"
