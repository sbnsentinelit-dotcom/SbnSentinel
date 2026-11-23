"""
Patient Flow calculation (services/patient_flow_service.py) with a fixed clock and versioned events.
The cases follow the auditor's acceptance checks (Production Check 05): measured waits, an open
visit across midnight, replays and duplicates, trends and suggestions.
"""
from datetime import date, datetime, timedelta
from itertools import count

import pytest

from app.models.patient_flow import (
    ARRIVED, BILLING, CANCELLED, CHECKED_OUT, CONSULT_END, CONSULT_START, READY, ROOM_STATUS,
    ClinicRoomModel, VisitEventModel,
)
from app.services import patient_flow_service as flow
from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY as POLICY, local_midnight_utc

pytestmark = pytest.mark.unit

DAY = date(2026, 11, 23)
_ids = count(1)


def at(hhmm: str, day: date = DAY) -> datetime:
    """Clinic local time (UAE) -> stored UTC instant."""
    h, m = map(int, hhmm.split(":"))
    return local_midnight_utc(day, POLICY) + timedelta(hours=h, minutes=m)


def ev(visit, event_type, hhmm, day=DAY, provider="Dr. Omar Ali", room=None, source="test", **kw):
    return VisitEventModel(id=next(_ids), event_key=f"{visit}:{event_type}:{next(_ids)}", clinic_id="C1",
                           visit_id=visit, event_type=event_type, event_time=at(hhmm, day),
                           patient_name=kw.get("name", f"Patient {visit}"), patient_ref=kw.get("ref"),
                           provider=provider, department=kw.get("dept", "ENT"),
                           priority=kw.get("priority", "Normal"), room=room, source=source)


def test_open_visit_wait_is_measured_from_arrival_and_turns_delayed():
    events = [ev("A", ARRIVED, "10:00"), ev("A", READY, "10:10")]
    st = flow.build_states(events, at("10:45"))["A"]
    assert st.wait_minutes() == 45
    assert st.stage == "Waiting" and st.status(POLICY) == "Delayed"
    m = flow._metrics({"A": st}, "occupancy", at("00:00"), at("10:45"), POLICY)
    assert m == {"checked_in": 0, "in_consultation": 0, "delayed": 1, "avg_wait": None, "avg_wait_sample": 0}
    early = flow.build_states(events, at("10:20"))["A"]
    assert early.status(POLICY) == "Waiting" and early.wait_minutes() == 20


def test_average_wait_uses_measured_starts_and_exit_leaves_occupancy():
    events = []
    for i in range(9):
        v = f"E{i}"
        events += [ev(v, ARRIVED, f"0{8 + i // 6}:{(i % 6) * 10:02d}"),
                   ev(v, CONSULT_START, f"0{8 + i // 6}:{(i % 6) * 10 + 10:02d}", room="Room 1"),
                   ev(v, CONSULT_END, f"0{8 + i // 6}:{(i % 6) * 10 + 15:02d}", room="Room 1"),
                   ev(v, CHECKED_OUT, f"0{8 + i // 6}:{(i % 6) * 10 + 20:02d}")]
    events += [ev("N", ARRIVED, "10:00"), ev("N", CONSULT_START, "10:50", room="Room 2")]
    states = flow.build_states(events, at("11:00"))
    m = flow._metrics(states, "occupancy", at("00:00"), at("11:00"), POLICY)
    assert m["avg_wait"] == 14 and m["avg_wait_sample"] == 10
    assert m["in_consultation"] == 1
    events.append(ev("N", CHECKED_OUT, "11:10"))
    after = flow.build_states(events, at("11:15"))
    assert not after["N"].is_open and after["N"].status(POLICY) == "Completed"
    assert flow._metrics(after, "occupancy", at("00:00"), at("11:15"), POLICY)["in_consultation"] == 0


def test_no_verified_waits_is_not_zero():
    states = flow.build_states([ev("A", ARRIVED, "09:00")], at("09:05"))
    m = flow._metrics(states, "occupancy", at("00:00"), at("09:05"), POLICY)
    assert m["avg_wait"] is None and m["avg_wait_sample"] == 0


def test_open_visit_stays_visible_across_midnight():
    yesterday = DAY - timedelta(days=1)
    events = [ev("M", ARRIVED, "23:55", day=yesterday)]
    states = flow.build_states(events, at("00:10"))
    assert states["M"].is_open and states["M"].wait_minutes() == 15
    m = flow._metrics(states, "occupancy", at("00:00"), at("00:10"), POLICY)
    assert m["checked_in"] == 1


def test_duplicates_and_out_of_order_delivery_rebuild_one_visit():
    ordered = [ev("R", ARRIVED, "09:00"), ev("R", CONSULT_START, "09:20", room="Room 1"),
               ev("R", CONSULT_END, "09:40", room="Room 1")]
    shuffled = [ordered[2], ordered[0], ordered[1], ev("R", CONSULT_START, "09:30", room="Room 3")]
    a = flow.build_states(ordered, at("10:00"))["R"]
    b = flow.build_states(shuffled, at("10:00"))["R"]
    assert a.wait_minutes() == b.wait_minutes() == 20
    assert b.room == "Room 1" and b.stage == "Consultation"


def test_nothing_changes_a_visit_after_it_is_closed():
    events = [ev("X", ARRIVED, "09:00"), ev("X", CANCELLED, "09:40"), ev("X", CONSULT_START, "09:50")]
    st = flow.build_states(events, at("10:00"))["X"]
    assert st.status(POLICY) == "Cancelled" and st.consult_start is None
    assert st.wait_minutes() == 40


def test_stage_timestamps_come_only_from_recorded_events():
    events = [ev("S", ARRIVED, "09:00"), ev("S", CONSULT_START, "09:20", room="Room 1")]
    stamps = flow.build_states(events, at("09:30"))["S"].stage_timestamps()
    assert set(stamps) == {"Check In", "Consultation"}  # no Waiting stage was recorded
    assert stamps["Check In"] == "2026-11-23T05:00:00Z"


def test_trend_text_and_meaning():
    assert flow._trend(3, 2) == "+50%"
    assert flow._trend(2, 4) == "-50%"
    assert flow._trend(5, 0) == "+5"
    assert flow._trend(2000, 1) == ">+999%"
    assert flow._trend(18, 12, "m") == "+6m"
    assert flow._trend(3, None) is None
    assert flow._trend_good("in_consultation", "+50%") is True
    assert flow._trend_good("avg_wait", "+6m") is False
    assert flow._trend_good("delayed", "-50%") is True
    assert flow._trend_good("delayed", "+0") is None


def test_suggestions_are_calculated_from_the_queue():
    events = [ev("C", ARRIVED, "09:00", provider="Dr. Reem Malik"),
              ev("C", CONSULT_START, "09:10", provider="Dr. Reem Malik", room="Room 1"),
              ev("W", ARRIVED, "09:05", provider="Dr. Reem Malik", name="Aisha Verma"),
              ev("L", ARRIVED, "09:00", provider="Dr. Sara Khan", name="Omar Khan")]
    now = at("09:45")
    sugg = flow.compute_suggestions(flow.build_states(events, now), now, POLICY, set())
    late = next(s for s in sugg if s["type"] == "PROVIDER_RUNNING_LATE")
    assert late["text"] == ("Dr. Reem Malik's consultation is running 15 mins late. "
                            "Notify next patient Aisha Verma of delay?")
    long_wait = next(s for s in sugg if s["type"] == "LONG_WAIT")
    assert long_wait["visit_id"] == "L" and long_wait["minutes_waiting"] == 45
    assert all(s["visit_id"] != "W" or s["type"] == "PROVIDER_RUNNING_LATE" for s in sugg)
    handled = {late["id"], long_wait["id"]}
    assert [s["id"] for s in flow.compute_suggestions(flow.build_states(events, now), now, POLICY, handled)] \
        == ["wait:W"]


def test_room_state_follows_consultations_cleaning_and_staff():
    rooms = [ClinicRoomModel(id=1, clinic_id="C1", name="Room 1", kind="consult"),
             ClinicRoomModel(id=2, clinic_id="C1", name="Room 2", kind="consult"),
             ClinicRoomModel(id=3, clinic_id="C1", name="Room 3", kind="consult")]
    events = [ev("A", ARRIVED, "09:00"), ev("A", CONSULT_START, "09:10", room="Room 1"),
              ev("B", ARRIVED, "09:00"), ev("B", CONSULT_START, "09:05", room="Room 2"),
              ev("B", CONSULT_END, "09:25", room="Room 2"), ev("B", BILLING, "09:28")]
    staff = [VisitEventModel(id=999, event_key="r", clinic_id="C1", visit_id=None, event_type=ROOM_STATUS,
                             event_time=at("09:20"), room="Room 3", detail="Cleaning", source="manual")]
    now = at("09:30")
    out = {r["name"]: r for r in flow.room_states(rooms, flow.build_states(events, now), staff, now, POLICY)}
    assert out["Room 1"]["status"] == "Occupied" and out["Room 1"]["patient_name"] == "Patient A"
    assert out["Room 2"]["status"] == "Cleaning" and out["Room 2"]["available_in_minutes"] == 6
    assert out["Room 3"]["status"] == "Cleaning"
    later = at("09:40")
    out = {r["name"]: r for r in flow.room_states(rooms, flow.build_states(events, later), staff, later, POLICY)}
    assert out["Room 2"]["status"] == "Available"


def test_last_seven_days_counts_totals_in_the_period():
    events = [ev("P1", ARRIVED, "09:00", day=DAY - timedelta(days=2)),
              ev("P1", CONSULT_START, "09:40", day=DAY - timedelta(days=2)),
              ev("P1", CHECKED_OUT, "10:10", day=DAY - timedelta(days=2)),
              ev("P2", ARRIVED, "09:00"), ev("P2", CONSULT_START, "09:10")]
    start = local_midnight_utc(DAY - timedelta(days=6), POLICY)
    states = flow.build_states(events, at("12:00"))
    m = flow._metrics(states, "period", start, at("12:00"), POLICY)
    assert m["checked_in"] == 2 and m["in_consultation"] == 2 and m["delayed"] == 1 and m["avg_wait"] == 25
