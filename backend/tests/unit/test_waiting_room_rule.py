"""
The auditor's Waiting Room Stress Test 3 run against Sentinel's rule (app.services.waiting_room_rule):
the 43 original cases (cases.mjs), the 12 additional cases (edge-cases.mjs), the 10:45 reference case
and the seeded 500-cohort stress sweep (stress.mjs, seed 87923). Expected values are the auditor's.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.waiting_room_rule import revised_rule

pytestmark = pytest.mark.unit

ANCHOR = datetime(2026, 11, 21, 10, 0, tzinfo=timezone(timedelta(hours=4)))
SOURCE = {"arrive": "reception", "room": "rooms", "file": "records", "nurse": "nursing", "approval": "payer",
          "file_revoked": "records", "nurse_revoked": "nursing", "approval_revoked": "payer",
          "send": "nursing", "accept": "consult_team", "start": "clinician", "left": "reception",
          "no_show": "reception"}


def at(m):
    return (ANCHOR + timedelta(minutes=m)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ev(visit, kind, m, **more):
    return {"clinic": "SHJ", "visit": visit, "kind": kind, "source": SOURCE[kind], "id": f"{visit}/{kind}/{m}",
            "occurred": at(m), "known": at(m), "destination": "Consultation", "handoff": "H1", **more}


def visit(vid, **extra):
    return {"id": vid, "clinic": "SHJ", "payerRequired": True, **extra}


def basics(vid="A"):
    return [ev(vid, "arrive", 0), ev(vid, "file", 2), ev(vid, "approval", 12), ev(vid, "nurse", 15),
            ev(vid, "send", 15)]


CONFIG = {"clinic": "SHJ", "cutoff": at(45), "fresh": True}
MAIN_ROSTER = [visit(f"V{i + 1}") for i in range(10)]
MAIN_EVENTS = [e for i, r in enumerate(MAIN_ROSTER)
               for e in (basics(r["id"]) if i == 9 else [ev(r["id"], "arrive", 0), ev(r["id"], "start", 10)])]
BASE = basics()


def observe(result, expected):
    out = {}
    for key in expected:
        parts = key.split(".")
        if len(parts) == 1:
            out[key] = result[key]
        else:
            row = next((r for r in result["rows"] if r["id"] == parts[0]), {})
            out[key] = row.get(parts[1])
    return out


def run(expected, events=None, opts=None, roster=None):
    events = BASE if events is None else events
    roster = [visit("A")] if roster is None else roster
    result = revised_rule(roster, events, {**CONFIG, **(opts or {})})
    return observe(result, expected), result


ORIGINAL = [
    ("Nine seen and a tenth waiting; different denominators",
     {"seen": 9, "mean": 10, "denom": 9, "waiting": 1, "longest": 45, "V10.age": 45, "V10.readyAge": 30,
      "V10.owner": "Clinic coordinator"}, MAIN_EVENTS, {}, MAIN_ROSTER),
    ("Empty roster has unknown mean and no longest wait",
     {"seen": 0, "waiting": 0, "mean": None, "denom": 0, "longest": None}, [], {}, []),
    ("All open: no completed mean", {"seen": 0, "waiting": 1, "mean": None, "denom": 0, "longest": 45}, None, {}, None),
    ("Single real zero belongs in the mean", {"seen": 1, "mean": 0, "denom": 1},
     [ev("A", "arrive", 0), ev("A", "start", 0)], {}, None),
    ("Missing arrival does not become a zero wait", {"seen": 1, "denom": 0, "mean": None, "seenMissing": 1},
     [ev("A", "start", 10)], {}, None),
    ("Only valid durations enter the mean", {"seen": 2, "denom": 1, "seenMissing": 1, "mean": 10},
     [ev("A", "arrive", 0), ev("A", "start", 10), ev("B", "start", 10)], {}, [visit("A"), visit("B")]),
    ("Parallel prerequisites end at the last required event",
     {"A.age": 45, "A.readyAge": 30, "A.handoff": "Sent, acceptance pending"}, None, {}, None),
    ("Missing nursing readiness preserves total wait",
     {"A.age": 45, "A.readyAge": None, "A.owner": "Clinic coordinator"}, [e for e in BASE if e["kind"] != "nurse"], {}, None),
    ("A missing payer result blocks the readiness clock", {"A.readyAge": None, "A.age": 45},
     [e for e in BASE if e["kind"] != "approval"], {}, None),
    ("Payer evidence unnecessary for this path", {"A.readyAge": 30},
     [e for e in BASE if e["kind"] != "approval"], {}, [visit("A", payerRequired=False)]),
    ("Unknown pathway does not default to no authorization", {"A.readyAge": None}, BASE, {},
     [visit("A", payerRequired=None)]),
    ("An in-room record is not consultation start", {"waiting": 1, "seen": 0, "A.age": 45},
     BASE + [ev("A", "room", 20)], {}, None),
    ("Named recipient acceptance changes follow-up owner",
     {"A.handoff": "Accepted", "A.owner": "Consultation team", "waiting": 1}, BASE + [ev("A", "accept", 20)], {}, None),
    ("Wrong recipient cannot acquire ownership", {"A.owner": "Clinic coordinator"},
     BASE + [ev("A", "accept", 20, destination="Radiology")], {}, None),
    ("Response to an older transfer cannot acquire ownership", {"A.owner": "Clinic coordinator"},
     BASE + [ev("A", "accept", 20, handoff="OLD")], {}, None),
    ("Acceptance without a sent handoff does not transfer responsibility", {"A.owner": "Clinic coordinator"},
     [e for e in BASE if e["kind"] != "send"] + [ev("A", "accept", 20)], {}, None),
    ("Untrusted start marker cannot mark a patient seen", {"seen": 0, "waiting": 1},
     BASE + [ev("A", "start", 22, source="billing")], {}, None),
    ("Late consultation entry was unavailable at cutoff", {"seen": 0, "waiting": 1},
     BASE + [ev("A", "start", 30, known=at(50))], {}, None),
    ("Late consultation entry uses occurrence time after arrival", {"seen": 1, "waiting": 0, "mean": 30},
     BASE + [ev("A", "start", 30, known=at(50))], {"cutoff": at(55)}, None),
    ("Source outage prevents asserting an open wait", {"waiting": 0, "unknown": 1, "longest": None, "A.age": None},
     BASE, {"fresh": False}, None),
    ("Stale feed does not erase already documented start", {"seen": 1, "mean": 10},
     [ev("A", "arrive", 0), ev("A", "start", 10)], {"fresh": False}, None),
    ("Left before consultation stays separate", {"left": 1, "waiting": 0, "seen": 0, "denom": 0},
     BASE + [ev("A", "left", 25)], {}, None),
    ("Scheduled but no arrival is unconfirmed", {"unknown": 1, "waiting": 0, "seen": 0}, [], {}, None),
    ("Recorded no-show excluded from active and completed counts", {"noShow": 1, "waiting": 0, "seen": 0},
     [ev("A", "no_show", 10)], {}, None),
    ("A no-show conflicts with an actual arrival", {"review": 1, "waiting": 0}, BASE + [ev("A", "no_show", 20)], {}, None),
    ("Conflicting duplicate identity needs review", {"review": 1, "waiting": 0, "mean": None},
     BASE + [{**BASE[0], "occurred": at(1), "known": at(1)}], {}, None),
    ("Replayed event and roster entry count once", {"waiting": 1, "seen": 0, "A.readyAge": 30},
     BASE + BASE, {}, [visit("A"), visit("A")]),
    ("Encounter IDs are isolated by facility", {"seen": 0, "waiting": 1},
     BASE + [ev("A", "start", 8, clinic="OTHER")], {}, [visit("A"), visit("A", clinic="OTHER")]),
    ("Two visits of one person are counted separately", {"seen": 1, "waiting": 1, "mean": 10, "longest": 25},
     [ev("A", "arrive", 0), ev("A", "start", 10), ev("B", "arrive", 20)], {},
     [visit("A", patient="P"), visit("B", patient="P")]),
    ("Withdrawal suspends readiness and stale owner", {"A.readyAge": None, "A.owner": "Clinic coordinator"},
     BASE + [ev("A", "accept", 20), ev("A", "nurse_revoked", 30)], {}, None),
    ("Reconfirmation starts a new continuous readiness interval", {"A.readyAge": 5, "A.owner": "Clinic coordinator"},
     BASE + [ev("A", "accept", 20), ev("A", "nurse_revoked", 30), ev("A", "nurse", 40)], {}, None),
    ("Repeat positive evidence does not restart a valid clock", {"A.readyAge": 30}, BASE + [ev("A", "nurse", 35)], {}, None),
    ("Arrival and start in impossible order go to review", {"review": 1, "seen": 0, "mean": None},
     [ev("A", "arrive", 10), ev("A", "start", 5)], {}, None),
    ("Left before being seen conflicts with later consultation", {"review": 1, "seen": 0, "left": 0},
     BASE + [ev("A", "left", 25), ev("A", "start", 27)], {}, None),
    ("Event file order does not change ownership or clock", {"A.readyAge": 30, "A.owner": "Consultation team"},
     list(reversed(BASE + [ev("A", "accept", 20)])), {}, None),
    ("Ready alert at the illustrative ten-minute threshold", {"A.flag": True}, BASE, {"cutoff": at(25)}, None),
    ("One minute before threshold does not trigger alert", {"A.flag": False}, BASE, {"cutoff": at(24)}, None),
    ("Visit carried through UAE midnight uses elapsed minutes", {"seen": 1, "mean": 20},
     [ev("A", "arrive", 835), ev("A", "start", 855)], {"cutoff": at(870)}, None),
    ("Future-dated receipt does not count early", {"waiting": 1, "seen": 0}, BASE + [ev("A", "start", 55)], {}, None),
    ("Average responds to a different cohort", {"mean": 25, "seen": 1}, [ev("A", "arrive", 0), ev("A", "start", 25)], {}, None),
    ("Malformed timestamp is flagged, not converted to zero", {"review": 1, "waiting": 0},
     [ev("A", "arrive", 0, occurred="unknown")], {}, None),
    ("Resent handoff returns to pending until the new request is accepted",
     {"A.owner": "Clinic coordinator", "A.handoff": "Sent, acceptance pending"},
     BASE + [ev("A", "accept", 18), ev("A", "send", 25, handoff="H2")], {}, None),
    ("Arrival after previsit preparation starts the ready clock at arrival", {"A.readyAge": 45},
     [ev("A", "file", -12), ev("A", "nurse", -9), ev("A", "approval", -7), ev("A", "arrive", 0)], {}, None),
]

ADDITIONAL = [
    ("Payer feed stale while movement is fresh: preserve total, suppress readiness",
     {"A.age": 45, "A.readyAge": None, "A.flag": False, "waiting": 1}, BASE, {"coverage": {"payer": False}}, None),
    ("Self-pay visit ignores a stale payer feed", {"A.age": 45, "A.readyAge": 30}, BASE,
     {"coverage": {"payer": False}}, [visit("A", payerRequired=False)]),
    ("Missing clinician feed makes still-waiting status unknown", {"unknown": 1, "waiting": 0, "longest": None},
     BASE, {"coverage": {"clinician": False}}, None),
    ("A stale file feed cannot certify all preparation complete", {"A.age": 45, "A.readyAge": None, "waiting": 1},
     BASE, {"coverage": {"records": False}}, None),
    ("A stale handoff feed leaves receipt uncertain, even after previous acceptance",
     {"A.age": 45, "A.readyAge": 30, "A.handoff": "Handoff evidence unavailable",
      "A.owner": "Clinic coordinator to verify"}, BASE + [ev("A", "accept", 18)], {"coverage": {"handoff": False}}, None),
    ("Explicitly confirmed consultation survives a later movement feed outage", {"seen": 1, "mean": 30, "waiting": 0},
     BASE + [ev("A", "start", 30)], {"coverage": {"clinician": False}}, None),
    ("A repeated receipt with a new arrival timestamp is idempotent", {"waiting": 1, "A.owner": "Consultation team"},
     BASE + [ev("A", "accept", 18), ev("A", "accept", 18, known=at(21))], {}, None),
    ("One source ID reused across distinct visits quarantines both", {"review": 2, "waiting": 0},
     [ev("A", "arrive", 0), ev("B", "arrive", 12, id="A/arrive/0")], {}, [visit("A"), visit("B")]),
    ("Simultaneously revoked and confirmed prerequisite requires review", {"review": 1, "waiting": 0},
     BASE + [ev("A", "nurse_revoked", 15)], {}, None),
    ("Future knowledge of a conflict does not rewrite the past", {"waiting": 1, "A.readyAge": 30},
     BASE + [ev("A", "nurse", 14, id="A/nurse/15", known=at(48))], {}, None),
    ("Front desk cannot anchor an unverified future clinical marker", {"waiting": 1, "seen": 0},
     BASE + [ev("A", "start", 15, source="reception")], {}, None),
    ("UTC and UAE offsets identify the same moment", {"seen": 1, "mean": 15},
     [ev("A", "arrive", 0, occurred="2026-11-21T10:00:00+04:00"), ev("A", "start", 15)], {}, None),
]


@pytest.mark.parametrize("name,expected,events,opts,roster", ORIGINAL, ids=[c[0] for c in ORIGINAL])
def test_original_cases(name, expected, events, opts, roster):
    observed, _ = run(expected, events, opts, roster)
    assert observed == expected, name


@pytest.mark.parametrize("name,expected,events,opts,roster", ADDITIONAL, ids=[c[0] for c in ADDITIONAL])
def test_additional_cases(name, expected, events, opts, roster):
    observed, _ = run(expected, events, opts, roster)
    assert observed == expected, name


def test_case_counts_match_the_auditor_package():
    assert len(ORIGINAL) == 43 and len(ADDITIONAL) == 12


def test_reference_case_at_10_45():
    result = revised_rule(MAIN_ROSTER, MAIN_EVENTS, CONFIG)
    waiting = next(r for r in result["rows"] if r["status"] == "waiting")
    assert (result["seen"], result["denom"], result["mean"], result["waiting"]) == (9, 9, 10, 1)
    assert (waiting["id"], waiting["age"], waiting["readyAge"]) == ("V10", 45, 30)
    assert waiting["handoff"] == "Sent, acceptance pending" and waiting["owner"] == "Clinic coordinator"
    # Remove the nursing timestamp: the total wait stays, readiness becomes unconfirmed.
    no_nurse = [e for e in MAIN_EVENTS if not (e["visit"] == "V10" and e["kind"] == "nurse")]
    waiting = next(r for r in revised_rule(MAIN_ROSTER, no_nurse, CONFIG)["rows"] if r["status"] == "waiting")
    assert waiting["age"] == 45 and waiting["readyAge"] is None


def test_cutoff_needs_an_explicit_time_zone():
    with pytest.raises(ValueError):
        revised_rule([], [], {"clinic": "SHJ", "cutoff": "2026-11-21T10:45:00"})


# --------------------------------------------------------------------------- seeded stress sweep (stress.mjs)

def stress(trials=500):
    seed = [87923]

    def random(n):
        seed[0] = (seed[0] * 1664525 + 1013904223) & 0xFFFFFFFF
        return (n * seed[0]) // 4294967296

    generated = passed = 0
    for _ in range(trials):
        cohort, events, oracle = [], [], {}
        total = basis = 0
        longest = None
        counter = {"seen": 0, "waiting": 0, "left": 0, "unknown": 0, "noShow": 0, "review": 0}
        n, now = 5 + random(46), 150
        for i in range(n):
            vid, start_at, duration, payer_required = f"S{i}", random(65), random(35), bool(random(2))
            cohort.append(visit(vid, payerRequired=payer_required))
            source_kind = random(7)

            def generate(kind, t, **more):
                events.append(ev(vid, kind, t, **more))
            age = ready_age = None
            if source_kind in (0, 1):
                if random(4):
                    generate("arrive", start_at)
                    age = duration
                    total += age
                    basis += 1
                generate("start", start_at + duration)
                status = "seen"
            elif source_kind in (2, 3):
                status = "waiting"
                age = now - start_at
                longest = age if longest is None else max(longest, age)
                generate("arrive", start_at)
                readiness = [random(25), random(25), random(25)]
                generate("file", start_at + readiness[0])
                nursing_known = bool(random(3))
                if nursing_known:
                    generate("nurse", start_at + readiness[1])
                if payer_required:
                    generate("approval", start_at + readiness[2])
                if nursing_known:
                    ready_age = age - max(readiness[:3 if payer_required else 2])
            elif source_kind == 4:
                generate("arrive", start_at)
                generate("left", start_at + duration)
                status = "left"
            elif source_kind == 5:
                generate("no_show", start_at)
                status = "noShow"
            else:
                status = "unknown"
            counter[status] += 1
            oracle[vid] = {"status": status, "age": age, "readyAge": ready_age}
            if random(4) == 0:
                generate("start", 10, clinic="OTHER")
        generated += n
        clean = len(events)
        for j in range(clean):
            if random(4) == 0:
                events.append(events[j])
        for j in range(len(events) - 1, 0, -1):
            k = random(j + 1)
            events[j], events[k] = events[k], events[j]
        s = revised_rule(cohort, events, {"clinic": "SHJ", "cutoff": at(now), "fresh": True})
        assert len(s["rows"]) == n
        for key, value in counter.items():
            assert s[key] == value
        assert s["denom"] == basis
        assert s["mean"] == (total / basis if basis else None)
        assert s["longest"] == longest
        for row in s["rows"]:
            assert {"status": row["status"], "age": row["age"], "readyAge": row["readyAge"]} == oracle[row["id"]]
        passed += 1
    return {"seed": 87923, "cohorts": trials, "passed": passed, "syntheticVisits": generated}


def test_seeded_stress_sweep_matches_the_auditor_summary():
    assert stress() == {"seed": 87923, "cohorts": 500, "passed": 500, "syntheticVisits": 13886}
