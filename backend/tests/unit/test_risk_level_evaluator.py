"""
Risk level evaluator: Critical / High / Moderate from source data (payer remittance, EHR).
There is no Low level, and Critical is only possible on complete, verified evidence.
"""
from datetime import datetime, timedelta

import pytest

from app.services.risk_level_evaluator import (
    CLAIM_DENIAL, CRITICAL, DEFAULT_POLICY, HIGH, LEVELS, MODERATE, NO_SHOW, WAIT_TIME,
    current_evaluation, current_level, evaluate, kind_for_rule, local_time, time_left_text,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 22, 10, 0)


def claim(code="CO4", amount="640.00", currency="AED", days=None):
    deadline = (NOW + timedelta(days=days)).isoformat() + "Z" if days is not None else None
    return {"denial_code": code, "denied_amount": amount, "currency": currency,
            "remittance_date": (NOW - timedelta(days=3)).isoformat() + "Z", "resubmission_deadline": deadline}


def level(kind, inputs, now=NOW):
    return evaluate(kind, inputs, now).level


@pytest.mark.parametrize("inputs,expected", [
    # consequence unknown or not material -> Moderate
    (dict(claim(days=1), denied_amount=None), MODERATE),
    (claim(currency="USD", days=1), MODERATE),
    (claim(amount="499.99", days=0.5), MODERATE),
    # material, evidence incomplete or deadline passed -> High (never Critical)
    (claim(days=None), HIGH),
    (claim(days=-1), HIGH),
    # material, verified deadline, days left <= correction time + 1 day -> Critical
    (claim(code="CO4", days=2), CRITICAL),
    (claim(code="CO4", days=1.5), CRITICAL),
    (claim(code="CO15", days=6), CRITICAL),
    (claim(code="CO16", days=4), CRITICAL),
    (claim(code="CO29", days=3), CRITICAL),
    (claim(code="CO50", days=3), CRITICAL),
    # material, inside the 7-day warning window -> High
    (claim(code="CO4", days=2.1), HIGH),
    (claim(code="CO29", days=4), HIGH),
    (claim(code="CO4", days=7), HIGH),
    # material, beyond the warning window -> Moderate
    (claim(code="CO4", days=7.1), MODERATE),
    (claim(code="CO50", days=10), MODERATE),
    # unknown denial code: correction time unknown -> Critical impossible
    (claim(code="CO97", days=0.5), HIGH),
    (claim(code="CO97", days=10), MODERATE),
])
def test_claim_denial_rules(inputs, expected):
    assert level(CLAIM_DENIAL, inputs) == expected


def test_claim_result_explains_itself():
    r = evaluate(CLAIM_DENIAL, claim(code="CO4", days=1.5), NOW).to_dict()
    assert r["level"] == CRITICAL
    assert "AED 640.00" in r["reason"] and "1 day 12 hours" in r["reason"] and "must start today" in r["reason"]
    assert r["rule_id"] == "RISK-CLAIM-DENIAL" and r["rule_version"] == "v1"
    assert r["policy_id"] == "POL-RISK-LEVEL" and r["policy_version"] == "v1"
    assert r["inputs"]["denial_code"] == "CO4" and r["inputs"]["correction_days"] == 1.0
    assert r["missing_inputs"] == [] and r["evaluated_at"].startswith("2026-11-22T10:00")


def test_missing_deadline_is_named_in_the_reason():
    r = evaluate(CLAIM_DENIAL, claim(days=None), NOW)
    assert r.level == HIGH and r.missing_inputs == ["payer resubmission deadline"]
    assert "Missing input: payer resubmission deadline." in r.reason


def test_level_rises_as_the_deadline_comes_closer():
    inputs = claim(code="CO4", days=9)
    assert [level(CLAIM_DENIAL, inputs, NOW + timedelta(days=d)) for d in (0, 3, 7.5, 9.5)] == \
        [MODERATE, HIGH, CRITICAL, HIGH]  # last: deadline passed -> verify outcome


@pytest.mark.parametrize("inputs,expected", [
    ({"visit_priority": "Urgent", "prior_no_shows_90d": 0, "visit_value": "100"}, CRITICAL),
    ({"visit_priority": "Routine", "prior_no_shows_90d": 2, "visit_value": "100"}, HIGH),
    ({"visit_priority": "Routine", "prior_no_shows_90d": 0, "visit_value": "500", "currency": "AED"}, HIGH),
    ({"visit_priority": "Routine", "prior_no_shows_90d": 1, "visit_value": "450"}, MODERATE),
    ({}, MODERATE),
])
def test_no_show_rules(inputs, expected):
    assert level(NO_SHOW, inputs) == expected


@pytest.mark.parametrize("inputs,expected", [
    ({"wait_minutes": 64, "patient_priority": "Normal", "room_available": True}, CRITICAL),
    ({"wait_minutes": 30, "patient_priority": "Urgent", "room_available": True}, CRITICAL),
    ({"wait_minutes": 45, "patient_priority": "Normal", "room_available": True}, HIGH),
    ({"wait_minutes": 59, "patient_priority": "Normal", "room_available": True}, HIGH),
    ({"wait_minutes": 35, "patient_priority": "Normal", "room_available": False}, HIGH),
    ({"wait_minutes": 35, "patient_priority": "Normal", "room_available": True}, MODERATE),
    ({"wait_minutes": 44}, MODERATE),
    ({}, MODERATE),
])
def test_wait_time_rules(inputs, expected):
    assert level(WAIT_TIME, inputs) == expected


def test_only_three_levels_exist():
    assert LEVELS == (CRITICAL, HIGH, MODERATE)
    seen = {level(CLAIM_DENIAL, claim(code=c, amount=a, days=d))
            for c in ("CO4", "CO15", "CO16", "CO29", "CO50", "CO97")
            for a in ("100", "640") for d in (None, -1, 0.5, 2, 4, 6, 8, 30)}
    assert seen <= set(LEVELS)


def test_rule_mapping():
    assert kind_for_rule("RULE-UAE-CO4") == CLAIM_DENIAL
    assert kind_for_rule("RULE-SCH-001") == NO_SHOW
    assert kind_for_rule("RULE-SCH-002") == WAIT_TIME
    assert kind_for_rule("RULE-SYS-BLOCKED") is None


def test_current_level_reevaluates_stored_inputs_and_normalizes_old_rows():
    meta = {"risk_kind": CLAIM_DENIAL, "risk_inputs": claim(code="CO4", days=1)}
    assert current_level("Moderate", meta, NOW) == CRITICAL
    assert current_evaluation(meta, NOW)["level"] == CRITICAL
    assert current_level("Medium", {}, NOW) == MODERATE
    assert current_level("Information", None, NOW) == "Information"
    assert current_evaluation({}, NOW) is None


# --------------------------------------------------------------------------- time, problem, action, autopilot

def full_claim(days=2.5, available=("Signed operative note", "Pathology report")):
    return dict(claim(code="CO16", days=days), claim_id="CLM-1", payer="Daman",
                denial_reason="Operative note and pathology report missing",
                required_evidence=["Signed operative note", "Pathology report"], evidence_available=list(available))


def test_time_left_counts_down_day_by_day():
    inputs = full_claim(days=3)
    texts = [evaluate(CLAIM_DENIAL, inputs, NOW + timedelta(days=d)).time_left for d in (0, 1, 2)]
    assert texts == ["3 days left", "2 days left", "1 day left"]
    assert time_left_text(NOW + timedelta(hours=5, minutes=10), NOW) == "5 hours 10 minutes left"
    assert time_left_text(NOW - timedelta(days=1, hours=2), NOW) == "deadline passed 1 day 2 hours ago"
    assert local_time(datetime(2026, 11, 24, 10, 0), DEFAULT_POLICY) == "24 Nov 2026, 2:00 PM UAE"


def test_problem_reason_and_action_come_from_the_payer_and_ehr_data():
    r = evaluate(CLAIM_DENIAL, full_claim(days=2.5), NOW)
    assert r.level == CRITICAL
    assert r.problem == ("Claim CLM-1 was denied by Daman with CO16: Operative note and pathology report missing. "
                         "Evidence the payer needs: Signed operative note, Pathology report.")
    assert "2 days 12 hours left before the payer resubmission deadline (25 Nov 2026, 2:00 AM UAE)" in r.reason
    assert r.recommended_action == ("Attach Signed operative note, Pathology report from the EHR and resubmit claim "
                                    "CLM-1 to Daman before 25 Nov 2026, 2:00 AM UAE.")
    assert "AED 640.00 recoverable" in r.expected_outcome
    assert r.time_left == "2 days 12 hours left" and r.autopilot_eligible is True


def test_autopilot_acts_on_every_critical_and_collects_missing_evidence_first():
    missing = evaluate(CLAIM_DENIAL, full_claim(days=2.5, available=("Signed operative note",)), NOW)
    assert missing.level == CRITICAL and missing.autopilot_eligible is True
    assert missing.autopilot_action == "resubmit_claim"
    assert missing.recommended_action.startswith("Obtain Pathology report (not yet in the EHR)")
    assert "Pathology report still to be obtained" in missing.reason
    high = evaluate(CLAIM_DENIAL, full_claim(days=6), NOW)
    assert high.level == HIGH and high.autopilot_eligible is False
    urgent = evaluate(NO_SHOW, {"visit_priority": "Urgent"}, NOW)
    assert urgent.autopilot_eligible is True and urgent.autopilot_action == "permitted_action"
    assert evaluate(WAIT_TIME, {"wait_minutes": 64}, NOW).autopilot_eligible is True
    assert evaluate(WAIT_TIME, {"wait_minutes": 50}, NOW).autopilot_eligible is False
    passed = evaluate(CLAIM_DENIAL, full_claim(days=-1), NOW)
    assert passed.autopilot_eligible is False


def test_passed_and_unknown_deadlines_change_the_action():
    passed = evaluate(CLAIM_DENIAL, full_claim(days=-1), NOW)
    assert passed.level == HIGH and passed.recommended_action.startswith("Check Daman's portal")
    unknown = evaluate(CLAIM_DENIAL, full_claim(days=None), NOW)
    assert unknown.recommended_action.startswith("Confirm the resubmission deadline with Daman.")


def test_no_show_and_wait_texts():
    ns = evaluate(NO_SHOW, {"visit_priority": "Urgent", "prior_no_shows_90d": 1, "visit_value": "300"}, NOW)
    assert ns.problem.startswith("The patient missed an urgent visit") and ns.recommended_action.startswith("Call the patient today")
    wt = evaluate(WAIT_TIME, {"wait_minutes": 52, "patient_priority": "Normal", "room_available": True}, NOW)
    assert wt.problem == "The patient has waited 52 minutes (normal priority; a room is free)."
    assert wt.recommended_action == "Give the patient the next free room and update them on the wait."
