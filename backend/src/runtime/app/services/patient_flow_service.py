"""
Live Patient Flow: every number, status and suggestion on the screen is calculated here from the
clinic's visit events (models/patient_flow.py). The source of the events (hospital feed, a person
on the screen, or the synthetic visit generator) does not change the calculation.

Definitions (policy values in services/patient_flow_policy.py):
- A visit is open from its arrival until it is checked out or cancelled.
- Wait = consultation start - arrival. While a patient is still waiting, the current wait is
  cutoff - arrival.
- Delayed = still waiting and the current wait is above the delay threshold.
- Today / Yesterday show the clinic at a cutoff (now, or the same clock time yesterday): who is
  waiting and who is with a provider at that moment; Last 7 Days shows totals over the period.
- Trends compare with the equivalent earlier cutoff (yesterday at the same time, the day before,
  or the previous 7 days). With no events in that earlier period the trend is unavailable.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.encounter import EncounterModel
from app.models.patient_flow import (
    ARRIVED, BILLING, CANCELLED, CHECKED_OUT, CONSULT_END, CONSULT_START, LAB_END, LAB_START, READY,
    ROOM_STATUS, SOURCE_MANUAL, TERMINAL_EVENTS, ClinicRoomModel, PatientFlowActionModel, VisitEventModel,
)
from app.services import workbook_dataset
from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY, PatientFlowPolicy, local_midnight_utc

PERIODS = ("Today", "Yesterday", "Last 7 Days")
STAGES = ["Check In", "Waiting", "Consultation", "Lab", "Billing", "Completed"]
_STAGE_OF_EVENT = {ARRIVED: "Check In", READY: "Waiting", CONSULT_START: "Consultation",
                   LAB_START: "Lab", BILLING: "Billing", CHECKED_OUT: "Completed"}
DEFAULT_ROOMS = (("Room 1", "consult"), ("Room 2", "consult"), ("Room 3", "consult"), ("Lab", "lab"))
ROOM_STATUSES = ("Available", "Occupied", "Cleaning")

MESSAGING_CONNECTOR = "MOCK_PATIENT_MESSAGING_CONNECTOR"
TELEPHONY_CONNECTOR = "MOCK_TELEPHONY_CONNECTOR"


class PatientFlowError(Exception):
    """A request the Patient Flow rules refuse (returned to the client as 409 with this message)."""


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


# ---------------------------------------------------------------- visit state from events
@dataclass
class VisitState:
    visit_id: str
    patient_ref: Optional[str] = None
    patient_name: Optional[str] = None
    provider: Optional[str] = None
    department: Optional[str] = None
    priority: Optional[str] = None
    times: Dict[str, datetime] = field(default_factory=dict)       # first time of each event type
    sources: Dict[str, str] = field(default_factory=dict)
    room: Optional[str] = None
    cutoff: Optional[datetime] = None

    def t(self, event_type: str) -> Optional[datetime]:
        return self.times.get(event_type)

    @property
    def arrived_at(self) -> Optional[datetime]:
        return self.t(ARRIVED)

    @property
    def consult_start(self) -> Optional[datetime]:
        return self.t(CONSULT_START)

    @property
    def is_open(self) -> bool:
        return bool(self.arrived_at) and not any(self.t(e) for e in TERMINAL_EVENTS)

    @property
    def stage(self) -> str:
        if self.t(CANCELLED):
            return "Cancelled"
        if self.t(CHECKED_OUT):
            return "Completed"
        if self.t(BILLING):
            return "Billing"
        if self.t(LAB_START) and not self.t(LAB_END):
            return "Lab"
        if self.consult_start:
            return "Consultation"
        if self.t(READY):
            return "Waiting"
        return "Check In"

    @property
    def waiting(self) -> bool:
        return self.is_open and not self.consult_start

    def wait_minutes(self) -> Optional[int]:
        """Measured wait (arrival -> consultation start); while waiting, the wait so far."""
        if not self.arrived_at:
            return None
        if self.consult_start:
            end = self.consult_start
        elif self.t(CANCELLED):
            end = self.t(CANCELLED)
        elif self.is_open and self.cutoff:
            end = self.cutoff
        else:
            return None
        return max(0, int((end - self.arrived_at).total_seconds() // 60))

    def delayed(self, policy: PatientFlowPolicy) -> bool:
        w = self.wait_minutes()
        return self.waiting and w is not None and w > policy.delay_threshold_minutes

    def was_delayed(self, policy: PatientFlowPolicy) -> bool:
        w = self.wait_minutes()
        return w is not None and w > policy.delay_threshold_minutes

    def status(self, policy: PatientFlowPolicy) -> str:
        stage = self.stage
        if stage == "Cancelled":
            return "Cancelled"
        if stage == "Completed":
            return "Completed"
        if self.consult_start:
            return "In Room"
        return "Delayed" if self.delayed(policy) else "Waiting"

    def stage_timestamps(self) -> Dict[str, str]:
        out = {}
        for event_type, stage in _STAGE_OF_EVENT.items():
            if self.t(event_type):
                out[stage] = iso(self.t(event_type))
        return out


def build_states(events: Iterable[VisitEventModel], cutoff: datetime) -> Dict[str, VisitState]:
    """Visit states at `cutoff` from the events recorded up to then. The first event of each type
    counts; nothing after a terminal event (checked out / cancelled) changes the visit."""
    states: Dict[str, VisitState] = {}
    ordered = sorted((e for e in events if e.visit_id and e.event_time <= cutoff),
                     key=lambda e: (e.event_time, e.id or 0))
    for ev in ordered:
        st = states.get(ev.visit_id)
        if st is None:
            st = states[ev.visit_id] = VisitState(visit_id=ev.visit_id, cutoff=cutoff)
        if any(st.t(x) for x in TERMINAL_EVENTS):
            continue
        for attr in ("patient_ref", "patient_name", "provider", "department", "priority"):
            if getattr(st, attr) is None and getattr(ev, attr):
                setattr(st, attr, getattr(ev, attr))
        if ev.event_type == CONSULT_START and ev.room and st.room is None:
            # The room of the consultation (a later room assignment fills it if the start had none).
            st.room = ev.room
        if ev.event_type not in st.times:
            st.times[ev.event_type] = ev.event_time
            st.sources[ev.event_type] = ev.source
    return {k: v for k, v in states.items() if v.arrived_at}


# ---------------------------------------------------------------- rooms
def ensure_rooms(db: Session, clinic_id: str) -> List[ClinicRoomModel]:
    rooms = (db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic_id)
             .order_by(ClinicRoomModel.sort_order, ClinicRoomModel.id).all())
    if rooms:
        return rooms
    for i, (name, kind) in enumerate(DEFAULT_ROOMS):
        db.add(ClinicRoomModel(clinic_id=clinic_id, name=name, kind=kind, sort_order=i))
    db.flush()
    return (db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic_id)
            .order_by(ClinicRoomModel.sort_order, ClinicRoomModel.id).all())


def clinic_room_names(db: Session, clinic_id: str, kind: Optional[str] = None) -> List[str]:
    q = db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic_id)
    if kind:
        q = q.filter(ClinicRoomModel.kind == kind)
    return [r.name for r in q.order_by(ClinicRoomModel.sort_order, ClinicRoomModel.id).all()]


def room_states(rooms: List[ClinicRoomModel], states: Dict[str, VisitState], room_events: List[VisitEventModel],
                now: datetime, policy: PatientFlowPolicy) -> List[Dict[str, Any]]:
    out = []
    for room in rooms:
        info: Dict[str, Any] = {"id": room.id, "name": room.name, "kind": room.kind, "doctor": room.doctor,
                                "status": "Available", "visit_id": None, "patient_name": None,
                                "provider": None, "department": None, "wait_minutes": None,
                                "available_in_minutes": None, "patients_in_lab": 0, "since": None}
        last_change: Optional[datetime] = None
        if room.kind == "lab":
            in_lab = [s for s in states.values() if s.is_open and s.stage == "Lab"]
            info["patients_in_lab"] = len(in_lab)
            if in_lab:
                first = min(in_lab, key=lambda s: s.t(LAB_START))
                info.update(status="Occupied", visit_id=first.visit_id, patient_name=first.patient_name,
                            provider=first.provider, department=first.department)
            for s in states.values():
                for x in (LAB_START, LAB_END):
                    if s.t(x) and (last_change is None or s.t(x) > last_change):
                        last_change = s.t(x)
        else:
            occupant = next((s for s in states.values()
                             if s.room == room.name and s.consult_start and not s.t(CONSULT_END)
                             and not any(s.t(x) for x in TERMINAL_EVENTS)), None)
            ended = [s.t(CONSULT_END) for s in states.values() if s.room == room.name and s.t(CONSULT_END)]
            started = [s.consult_start for s in states.values() if s.room == room.name and s.consult_start]
            if occupant:
                info.update(status="Occupied", visit_id=occupant.visit_id, patient_name=occupant.patient_name,
                            provider=occupant.provider, department=occupant.department,
                            wait_minutes=occupant.wait_minutes(), since=iso(occupant.consult_start))
            elif ended:
                last_end = max(ended)
                cleaning_until = last_end + timedelta(minutes=policy.cleaning_minutes)
                if now < cleaning_until:
                    left = int((cleaning_until - now).total_seconds() // 60) + 1
                    info.update(status="Cleaning", available_in_minutes=left, since=iso(last_end))
            candidates = [x for x in started + ended if x]
            last_change = max(candidates) if candidates else None
        manual = [e for e in room_events if e.room == room.name and e.event_time <= now]
        if manual:
            latest = max(manual, key=lambda e: (e.event_time, e.id or 0))
            if last_change is None or latest.event_time > last_change:
                if latest.detail in ROOM_STATUSES and not (latest.detail != "Occupied" and info["visit_id"]):
                    info.update(status=latest.detail, since=iso(latest.event_time), available_in_minutes=None)
        out.append(info)
    return out


# ---------------------------------------------------------------- metrics
def _period_windows(period: str, now: datetime, policy: PatientFlowPolicy):
    """(mode, window_start, cutoff, prev_window_start, prev_cutoff, day_starts)"""
    today = policy.local_date(now)
    if period == "Yesterday":
        cutoff = now - timedelta(days=1)
        start = local_midnight_utc(today - timedelta(days=1), policy)
        return "occupancy", start, cutoff, start - timedelta(days=1), cutoff - timedelta(days=1)
    if period == "Last 7 Days":
        start = local_midnight_utc(today - timedelta(days=6), policy)
        return "period", start, now, start - timedelta(days=7), now - timedelta(days=7)
    start = local_midnight_utc(today, policy)
    return "occupancy", start, now, start - timedelta(days=1), now - timedelta(days=1)


def _metrics(states: Dict[str, VisitState], mode: str, start: datetime, cutoff: datetime,
             policy: PatientFlowPolicy) -> Dict[str, Any]:
    if mode == "occupancy":
        waiting = [s for s in states.values() if s.waiting]
        delayed = [s for s in waiting if s.delayed(policy)]
        checked_in = len(waiting) - len(delayed)
        in_consult = sum(1 for s in states.values() if s.is_open and s.stage == "Consultation")
        delayed_count = len(delayed)
    else:
        arrivals = [s for s in states.values() if start <= s.arrived_at <= cutoff]
        checked_in = len(arrivals)
        in_consult = sum(1 for s in states.values() if s.consult_start and start <= s.consult_start <= cutoff)
        delayed_count = sum(1 for s in arrivals if s.was_delayed(policy))
    waits = [s.wait_minutes() for s in states.values()
             if s.consult_start and start <= s.consult_start <= cutoff]
    waits = [w for w in waits if w is not None]
    return {"checked_in": checked_in, "in_consultation": in_consult, "delayed": delayed_count,
            "avg_wait": round(mean(waits)) if waits else None, "avg_wait_sample": len(waits)}


def _trend(current: Optional[int], previous: Optional[int], unit: str = "") -> Optional[str]:
    if current is None or previous is None:
        return None
    diff = current - previous
    if unit == "m":
        return f"+{diff}m" if diff >= 0 else f"{diff}m"
    if previous == 0:
        return f"+{diff}" if diff >= 0 else str(diff)
    pct = round(diff / previous * 100)
    if abs(pct) > 999:
        return ">+999%" if pct > 0 else "<-999%"
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _trend_good(key: str, trend: Optional[str]) -> Optional[bool]:
    if trend is None:
        return None
    rising = trend.startswith("+") or trend.startswith(">")
    flat = trend in ("+0", "+0%", "+0m")
    if flat:
        return None
    # More patients seen is good; longer waits and more delayed patients are bad.
    return rising if key in ("checked_in", "in_consultation") else not rising


def _spark(events: List[VisitEventModel], mode: str, start: datetime, cutoff: datetime,
           policy: PatientFlowPolicy, keep) -> Dict[str, List[Optional[float]]]:
    points: List[Tuple[datetime, datetime]] = []
    if mode == "occupancy":
        t = start + timedelta(hours=policy.open_hour)
        while t < cutoff:
            points.append((max(start, t - timedelta(hours=1)), t))
            t += timedelta(hours=1)
        points.append((max(start, cutoff - timedelta(hours=1)), cutoff))
    else:
        d = start
        while d < cutoff:
            points.append((d, min(cutoff, d + timedelta(days=1))))
            d += timedelta(days=1)
    series: Dict[str, List[Optional[float]]] = {"checked_in": [], "in_consultation": [], "avg_wait": [], "delayed": []}
    for p_start, p_end in points:
        st = {k: v for k, v in build_states(events, p_end).items() if keep(v)}
        if mode == "occupancy":
            m = _metrics(st, "occupancy", start, p_end, policy)
            hour = _metrics(st, "period", p_start, p_end, policy)
            m["avg_wait"] = hour["avg_wait"]
        else:
            m = _metrics(st, "period", p_start, p_end, policy)
        for k in series:
            series[k].append(m[k])
    return series


# ---------------------------------------------------------------- suggestions
def compute_suggestions(states: Dict[str, VisitState], now: datetime, policy: PatientFlowPolicy,
                        handled: set) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    waiting = sorted((s for s in states.values() if s.waiting), key=lambda s: s.arrived_at)
    for s in states.values():
        if not (s.is_open and s.stage == "Consultation" and s.consult_start and not s.t(CONSULT_END)):
            continue
        running = int((now - s.consult_start).total_seconds() // 60)
        late = running - policy.consult_slot_minutes
        if late <= 0:
            continue
        nxt = next((w for w in waiting if w.provider == s.provider), None)
        if not nxt:
            continue
        sid = f"late:{s.visit_id}:{nxt.visit_id}"
        if sid in handled:
            continue
        out.append({
            "id": sid, "type": "PROVIDER_RUNNING_LATE", "action": "NOTIFY_PATIENT_DELAY",
            "visit_id": nxt.visit_id, "patient_name": nxt.patient_name, "provider": s.provider,
            "minutes_late": late,
            "text": f"{s.provider}'s consultation is running {late} mins late. "
                    f"Notify next patient {nxt.patient_name} of delay?",
            "basis": {"consultation_visit_id": s.visit_id, "consultation_started_at": iso(s.consult_start),
                      "slot_minutes": policy.consult_slot_minutes, "next_visit_id": nxt.visit_id,
                      "next_arrived_at": iso(nxt.arrived_at)},
        })
    for w in sorted(waiting, key=lambda s: -(s.wait_minutes() or 0)):
        minutes = w.wait_minutes() or 0
        if minutes <= policy.delay_threshold_minutes:
            continue
        sid = f"wait:{w.visit_id}"
        if sid in handled or any(x["visit_id"] == w.visit_id for x in out):
            continue
        out.append({
            "id": sid, "type": "LONG_WAIT", "action": "NOTIFY_PATIENT_DELAY", "visit_id": w.visit_id,
            "patient_name": w.patient_name, "provider": w.provider, "minutes_waiting": minutes,
            "text": f"{w.patient_name} has waited {minutes} mins for {w.provider} "
                    f"(target {policy.delay_threshold_minutes} mins). Notify the patient of the expected delay?",
            "basis": {"visit_id": w.visit_id, "arrived_at": iso(w.arrived_at),
                      "delay_threshold_minutes": policy.delay_threshold_minutes},
        })
    return out


# ---------------------------------------------------------------- snapshot
def _load_events(db: Session, clinic_id: str, since: datetime) -> List[VisitEventModel]:
    return (db.query(VisitEventModel)
            .filter(VisitEventModel.clinic_id == clinic_id, VisitEventModel.event_time >= since)
            .all())


def _row(s: VisitState, enc: Optional[EncounterModel], policy: PatientFlowPolicy) -> Dict[str, Any]:
    wait = s.wait_minutes()
    return {
        "id": s.visit_id, "visit_id": s.visit_id,
        "patient_name": s.patient_name, "patient_ref": s.patient_ref,
        "mrn": getattr(enc, "mrn", None), "payer_network": getattr(enc, "payer_network", None),
        "provider_name": s.provider, "department": s.department, "priority": s.priority,
        "status": s.status(policy), "journey_stage": s.stage, "room": s.room,
        "wait_minutes": wait, "wait_measured": bool(s.consult_start),
        "wait_time": f"{wait} mins" if wait is not None else None,
        "arrived_at": iso(s.arrived_at), "stage_timestamps": s.stage_timestamps(),
        "date": policy.local_date(s.arrived_at).isoformat(),
    }


def snapshot(db: Session, clinic_id: str, period: str = "Today", department: Optional[str] = None,
             priority: Optional[str] = None, now: Optional[datetime] = None,
             policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> Dict[str, Any]:
    """One clinic-scoped, read-only answer for the whole Patient Flow screen."""
    now = now or datetime.utcnow()
    period = period if period in PERIODS else "Today"
    mode, start, cutoff, prev_start, prev_cutoff = _period_windows(period, now, policy)
    events = _load_events(db, clinic_id, prev_start - timedelta(days=1))
    visit_events = [e for e in events if e.visit_id]
    room_events = [e for e in events if e.event_type == ROOM_STATUS]

    want_priority = "Urgent" if priority in ("High", "Urgent") else priority

    def keep(s: VisitState) -> bool:
        return ((not department or s.department == department)
                and (not want_priority or s.priority == want_priority))

    states = {k: v for k, v in build_states(visit_events, cutoff).items() if keep(v)}
    prev_states = {k: v for k, v in build_states(visit_events, prev_cutoff).items() if keep(v)}
    current = _metrics(states, mode, start, cutoff, policy)
    # The comparison is only made when events cover the earlier period from its first day.
    first_prev_day_end = prev_start + timedelta(days=1)
    has_history = any(prev_start <= e.event_time < min(first_prev_day_end, prev_cutoff) for e in visit_events)
    previous = _metrics(prev_states, mode, prev_start, prev_cutoff, policy) if has_history else None
    spark = _spark(visit_events, mode, start, cutoff, policy, keep)

    cards = {}
    for key, unit in (("checked_in", ""), ("in_consultation", ""), ("avg_wait", "m"), ("delayed", "")):
        value = current[key]
        prev_value = previous[key] if previous else None
        trend = _trend(value, prev_value, unit)
        if key == "avg_wait":
            display = f"{value}m" if value is not None else "No verified waits"
        else:
            display = str(value)
        cards[key] = {"value": value, "display": display, "trend": trend, "trend_good": _trend_good(key, trend),
                      "previous": prev_value, "spark": spark[key]}
    cards["avg_wait"]["sample"] = current["avg_wait_sample"]

    if mode == "occupancy":
        visible = [s for s in states.values() if s.is_open]
        visible.sort(key=lambda s: (0 if s.priority == "Urgent" else 1, s.arrived_at, s.visit_id))
    else:
        visible = [s for s in states.values() if start <= s.arrived_at <= cutoff]
        visible.sort(key=lambda s: (s.arrived_at, s.visit_id), reverse=True)
    encs = {e.id: e for e in db.query(EncounterModel).filter(
        EncounterModel.id.in_([s.visit_id for s in visible] or [""])).all()}
    queue = [_row(s, encs.get(s.visit_id), policy) for s in visible]

    # Rooms and suggestions describe the clinic now, whatever period is selected.
    now_states = build_states(visit_events, now)
    stored_rooms = (db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic_id)
                    .order_by(ClinicRoomModel.sort_order, ClinicRoomModel.id).all())
    rooms = room_states(stored_rooms, now_states, room_events, now, policy)
    handled = {a.suggestion_id for a in db.query(PatientFlowActionModel.suggestion_id)
               .filter(PatientFlowActionModel.clinic_id == clinic_id,
                       PatientFlowActionModel.suggestion_id.isnot(None)).all()}
    suggestions = compute_suggestions(now_states, now, policy, handled)

    busy = {s.provider for s in now_states.values() if s.is_open and s.stage == "Consultation"}
    specialty_of = workbook_dataset.provider_specialty()
    doctors = [{"name": p, "specialty": specialty_of.get(p), "available": p not in busy}
               for p in workbook_dataset.providers()]
    last_event = max((e.event_time for e in events if e.event_time <= now), default=None)
    synced = bool(last_event and now - last_event <= timedelta(minutes=policy.feed_fresh_minutes))

    return {
        "clinic_id": clinic_id, "as_of": iso(now), "period": period, "mode": mode,
        "cutoff": iso(cutoff), "timezone": policy.timezone_label,
        "policy": {"id": policy.policy_id, "version": policy.version,
                   "delay_threshold_minutes": policy.delay_threshold_minutes,
                   "cleaning_minutes": policy.cleaning_minutes,
                   "consult_slot_minutes": policy.consult_slot_minutes},
        "feed": {"last_event_at": iso(last_event), "synced": synced},
        "cards": cards, "queue": queue, "total": len(queue),
        "rooms": rooms, "suggestions": suggestions, "doctors": doctors,
        "departments": workbook_dataset.specialties(),
    }


# ---------------------------------------------------------------- journey and encounter cache
def visit_state(db: Session, clinic_id: Optional[str], visit_id: str, now: Optional[datetime] = None
                ) -> Optional[VisitState]:
    now = now or datetime.utcnow()
    q = db.query(VisitEventModel).filter(VisitEventModel.visit_id == visit_id)
    if clinic_id:
        q = q.filter(VisitEventModel.clinic_id == clinic_id)
    return build_states(q.all(), now).get(visit_id)


def journey_for_visit(db: Session, visit_id: str, now: Optional[datetime] = None,
                      policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> Optional[Dict[str, Any]]:
    """Stages and their times as recorded by the visit's events (None when the visit has none)."""
    now = now or datetime.utcnow()
    st = visit_state(db, None, visit_id, now)
    if not st:
        return None
    stage_sources = {_STAGE_OF_EVENT[k]: v for k, v in st.sources.items() if k in _STAGE_OF_EVENT}
    return {"current_stage": st.stage, "stage_timestamps": st.stage_timestamps(), "stage_sources": stage_sources,
            "status": st.status(policy), "wait_minutes": st.wait_minutes(),
            "wait_time": f"{st.wait_minutes()} mins" if st.wait_minutes() is not None else None,
            "room": st.room}


def refresh_encounter_cache(db: Session, clinic_id: str, visit_ids: Iterable[str], now: datetime,
                            policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> None:
    """Keeps the encounter record of each visit in step with its events (other screens read it)."""
    ids = list(visit_ids)
    if not ids:
        return
    events = db.query(VisitEventModel).filter(VisitEventModel.clinic_id == clinic_id,
                                              VisitEventModel.visit_id.in_(ids)).all()
    states = build_states(events, now)
    for enc in db.query(EncounterModel).filter(EncounterModel.id.in_(ids)).all():
        st = states.get(enc.id)
        if not st:
            continue
        enc.status = st.status(policy)
        enc.journey_stage = st.stage
        enc.stage_timestamps = st.stage_timestamps()
        enc.wait_time = f"{st.wait_minutes()} mins" if st.consult_start else None


def has_visit_events(db: Session, visit_id: str) -> bool:
    return db.query(VisitEventModel.id).filter(VisitEventModel.visit_id == visit_id).first() is not None


# ---------------------------------------------------------------- staff actions
def _manual_event(db: Session, clinic_id: str, st: VisitState, event_type: str, at: datetime, actor: str,
                  room: Optional[str] = None, detail: Optional[str] = None) -> VisitEventModel:
    ev = VisitEventModel(
        event_key=f"manual:{uuid.uuid4().hex}", clinic_id=clinic_id, visit_id=st.visit_id, event_type=event_type,
        event_time=at, patient_ref=st.patient_ref, patient_name=st.patient_name, provider=st.provider,
        department=st.department, priority=st.priority, room=room, detail=detail, source=SOURCE_MANUAL,
        recorded_by=actor,
    )
    db.add(ev)
    return ev


def _free_consult_room(db: Session, clinic_id: str, now: datetime, policy: PatientFlowPolicy) -> Optional[str]:
    events = _load_events(db, clinic_id, now - timedelta(days=2))
    rooms = room_states(ensure_rooms(db, clinic_id), build_states([e for e in events if e.visit_id], now),
                        [e for e in events if e.event_type == ROOM_STATUS], now, policy)
    return next((r["name"] for r in rooms if r["kind"] == "consult" and r["status"] == "Available"), None)


def report_status(db: Session, clinic_id: str, visit_id: str, status: str, actor: str,
                  now: Optional[datetime] = None, policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> Dict[str, Any]:
    """A person reports a visit's status from the queue. It is stored as a new event (with the
    actor), never as an overwrite. Delayed is calculated from the wait and cannot be set."""
    now = now or datetime.utcnow()
    st = visit_state(db, clinic_id, visit_id, now)
    if not st:
        raise LookupError("Visit not found in this clinic")
    if not st.is_open:
        raise PatientFlowError("This visit is already closed.")
    if status == "Delayed":
        raise PatientFlowError(f"Delayed is calculated: a patient waiting more than "
                               f"{policy.delay_threshold_minutes} minutes since arrival is shown as Delayed.")
    if status == "Waiting":
        if st.consult_start:
            raise PatientFlowError("The consultation has already started; a visit cannot go back to Waiting.")
        return {"visit_id": visit_id, "status": st.status(policy), "recorded": False}
    if status == "In Room":
        if st.consult_start:
            return {"visit_id": visit_id, "status": st.status(policy), "recorded": False}
        room = _free_consult_room(db, clinic_id, now, policy)
        _manual_event(db, clinic_id, st, CONSULT_START, now, actor, room=room)
    elif status == "Completed":
        if st.consult_start and not st.t(CONSULT_END):
            _manual_event(db, clinic_id, st, CONSULT_END, now, actor, room=st.room)
        _manual_event(db, clinic_id, st, CHECKED_OUT, now, actor)
    else:
        raise PatientFlowError(f"Unknown status: {status}")
    db.flush()
    refresh_encounter_cache(db, clinic_id, [visit_id], now, policy)
    db.commit()
    new = visit_state(db, clinic_id, visit_id, now)
    return {"visit_id": visit_id, "status": new.status(policy) if new else status, "recorded": True,
            "room": new.room if new else None}


def assign_room(db: Session, clinic_id: str, visit_id: str, room_name: str, actor: str,
                now: Optional[datetime] = None, policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> Dict[str, Any]:
    """Staff start the consultation of a waiting patient in a free room, or give a room to a
    consultation that started without one (recorded with the actor)."""
    now = now or datetime.utcnow()
    st = visit_state(db, clinic_id, visit_id, now)
    if not st:
        raise LookupError("Visit not found in this clinic")
    roomless_consult = st.is_open and st.stage == "Consultation" and not st.room
    if not (st.waiting or roomless_consult):
        raise PatientFlowError("Only a waiting patient, or a consultation without a room, can be assigned a room.")
    events = _load_events(db, clinic_id, now - timedelta(days=2))
    rooms = room_states(ensure_rooms(db, clinic_id), build_states([e for e in events if e.visit_id], now),
                        [e for e in events if e.event_type == ROOM_STATUS], now, policy)
    target = next((r for r in rooms if r["name"] == room_name), None)
    if not target or target["kind"] != "consult":
        raise PatientFlowError(f"{room_name} is not a consultation room of this clinic.")
    if target["status"] != "Available":
        raise PatientFlowError(f"{room_name} is {target['status'].lower()}.")
    _manual_event(db, clinic_id, st, CONSULT_START, now, actor, room=room_name)
    db.flush()
    refresh_encounter_cache(db, clinic_id, [visit_id], now, policy)
    db.commit()
    return {"visit_id": visit_id, "room": room_name, "status": "In Room"}


def set_room_status(db: Session, clinic_id: str, room_id: int, status: str, actor: str,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    if status not in ROOM_STATUSES:
        raise PatientFlowError(f"Room status must be one of {', '.join(ROOM_STATUSES)}.")
    room = db.query(ClinicRoomModel).filter(ClinicRoomModel.id == room_id, ClinicRoomModel.clinic_id == clinic_id).first()
    if not room:
        raise LookupError("Room not found in this clinic")
    db.add(VisitEventModel(event_key=f"manual:{uuid.uuid4().hex}", clinic_id=clinic_id, visit_id=None,
                           event_type=ROOM_STATUS, event_time=now, room=room.name, detail=status,
                           source=SOURCE_MANUAL, recorded_by=actor))
    db.commit()
    return {"room": room.name, "status": status}


def _patient_contact(patient_ref: Optional[str]) -> Optional[str]:
    if not patient_ref:
        return None
    return (workbook_dataset.patients_by_ref().get(patient_ref) or {}).get("mobile")


def _receipt(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:10].upper()}"


def _existing_action(db: Session, key: str) -> Optional[PatientFlowActionModel]:
    return db.query(PatientFlowActionModel).filter(PatientFlowActionModel.idempotency_key == key).first()


def _action_dict(a: PatientFlowActionModel) -> Dict[str, Any]:
    return {"action_id": a.action_id, "action_type": a.action_type, "visit_id": a.visit_id,
            "suggestion_id": a.suggestion_id, "recipient": a.recipient, "message": a.message,
            "status": a.status, "connector": a.connector, "receipt_ref": a.receipt_ref, "detail": a.detail,
            "actor": a.actor, "created_at": iso(a.created_at)}


def _store_action(db: Session, **fields) -> PatientFlowActionModel:
    action = PatientFlowActionModel(action_id=f"PFA-{uuid.uuid4().hex[:10].upper()}", **fields)
    db.add(action)
    db.commit()
    return action


def act_on_suggestion(db: Session, clinic_id: str, suggestion_id: str, decision: str, actor: str,
                      idempotency_key: Optional[str] = None, now: Optional[datetime] = None,
                      policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> Dict[str, Any]:
    """Approve (send the notification through the messaging connector) or dismiss a suggestion.
    The suggestion is recalculated first; one action per suggestion."""
    now = now or datetime.utcnow()
    key = idempotency_key or f"suggestion:{suggestion_id}"
    prior = _existing_action(db, key) or db.query(PatientFlowActionModel).filter(
        PatientFlowActionModel.clinic_id == clinic_id, PatientFlowActionModel.suggestion_id == suggestion_id).first()
    if prior:
        return _action_dict(prior)
    events = _load_events(db, clinic_id, now - timedelta(days=2))
    states = build_states([e for e in events if e.visit_id], now)
    current = {s["id"]: s for s in compute_suggestions(states, now, policy, set())}
    sug = current.get(suggestion_id)
    if not sug:
        raise PatientFlowError("This suggestion no longer applies to the current queue.")
    if decision == "dismiss":
        return _action_dict(_store_action(
            db, idempotency_key=key, clinic_id=clinic_id, action_type="DISMISS_SUGGESTION",
            visit_id=sug["visit_id"], suggestion_id=suggestion_id, basis={**sug["basis"], "as_of": iso(now)},
            status="RECORDED", actor=actor))
    st = states.get(sug["visit_id"])
    recipient = _patient_contact(st.patient_ref if st else None)
    if sug["type"] == "PROVIDER_RUNNING_LATE":
        message = (f"Dear {sug['patient_name']}, {sug['provider']} is running about {sug['minutes_late']} "
                   f"minutes late. Thank you for your patience.")
    else:
        message = (f"Dear {sug['patient_name']}, thank you for waiting. {sug['provider']} will see you "
                   f"as soon as possible.")
    if recipient:
        status, receipt, detail = "SENT", _receipt("MSG", suggestion_id, recipient), None
    else:
        status, receipt, detail = "FAILED", None, "No contact number on file for this patient."
    return _action_dict(_store_action(
        db, idempotency_key=key, clinic_id=clinic_id, action_type="NOTIFY_PATIENT_DELAY",
        visit_id=sug["visit_id"], suggestion_id=suggestion_id, recipient=recipient, message=message,
        basis={**sug["basis"], "as_of": iso(now), "suggestion": sug["text"]}, status=status,
        connector=MESSAGING_CONNECTOR, receipt_ref=receipt, detail=detail, actor=actor))


def call_patient(db: Session, clinic_id: str, visit_id: str, actor: str, idempotency_key: Optional[str] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Places a call to the patient's number on file through the telephony connector and records the
    attempt and its result."""
    now = now or datetime.utcnow()
    key = idempotency_key or f"call:{visit_id}:{now.strftime('%Y%m%d%H%M')}"
    prior = _existing_action(db, key)
    if prior:
        return _action_dict(prior)
    st = visit_state(db, clinic_id, visit_id, now)
    if not st:
        enc = db.query(EncounterModel).filter(EncounterModel.id == visit_id,
                                              EncounterModel.clinic_id == clinic_id).first()
        if not enc:
            raise LookupError("Visit not found in this clinic")
        patient_ref, name = enc.patient_ref, enc.patient_name
    else:
        patient_ref, name = st.patient_ref, st.patient_name
    recipient = _patient_contact(patient_ref)
    if not recipient:
        status, receipt, detail = "FAILED", None, "No contact number on file for this patient."
    else:
        answered = int(hashlib.sha256(f"{visit_id}|{key}".encode()).hexdigest(), 16) % 10 < 7
        status = "COMPLETED" if answered else "NO_ANSWER"
        receipt = _receipt("CALL", visit_id, key)
        detail = None if answered else "The patient did not answer."
    return _action_dict(_store_action(
        db, idempotency_key=key, clinic_id=clinic_id, action_type="CALL_PATIENT", visit_id=visit_id,
        recipient=recipient, message=f"Call to {name}", basis={"as_of": iso(now)}, status=status,
        connector=TELEPHONY_CONNECTOR, receipt_ref=receipt, detail=detail, actor=actor))


def visit_actions(db: Session, clinic_id: str, visit_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(PatientFlowActionModel)
            .filter(PatientFlowActionModel.clinic_id == clinic_id, PatientFlowActionModel.visit_id == visit_id)
            .order_by(PatientFlowActionModel.created_at.desc()).all())
    return [_action_dict(a) for a in rows]


def local_day(now: datetime, policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> date:
    return policy.local_date(now)
