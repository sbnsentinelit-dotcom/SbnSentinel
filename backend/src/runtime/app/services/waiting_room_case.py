"""
Records a waiting-room case as source events in a clinic (as a queue feed would deliver them), and
applies corrections to it. It proves the waiting-room rule on the live Patient Flow screen with the
auditor's example; the screen calculates everything from these stored events.

The case runs like a real clinic session in the clinic's Day Clinic (its own three rooms):
* one patient per doctor at a time (each consultation gets a provider who has no other consultation then,
  including the generator's visits), one consultation per room, rooms cleaned between patients;
* nine patients arrive at different times; each waits 10 minutes, is roomed one minute before the
  consultation starts, is seen for 12 minutes, then billing and checkout;
* a tenth patient arrives at the anchor time T for a fourth doctor who is not free yet: file ready
  T+2, payer approval T+12, nursing done T+15, handoff to Consultation sent T+15, not yet accepted.
At T+45: 9 consultations started, average wait 10 minutes for those 9, 6 completed, 3 in consultation
(three doctors, three rooms), 1 still waiting 45 minutes since arrival and 30 minutes since ready.
Events after T+45 (later ends, checkouts, the tenth patient's consultation) are delivered with their
own times and count only once their time has come.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.encounter import EncounterModel
from app.models.patient_flow import (
    ARRIVED, BILLING, CANCELLED, CHECKED_OUT, CONSULT_END, CONSULT_START, FILE_READY, HANDOFF_ACCEPTED,
    HANDOFF_SENT, NURSING_DONE, PAYER_APPROVED, ROOMED, SOURCE_ROLE_OF_EVENT, ClinicRoomModel, VisitEventModel,
)
from app.services import workbook_dataset

CASE_DEPARTMENT = "Day Clinic"
CASE_ROOM_KIND = "day_clinic"
CASE_ROOMS = ("Day Clinic Room 1", "Day Clinic Room 2", "Day Clinic Room 3")
CASE_SOURCE = "queue_feed"
CASE_VISITS = 10
CONSULT_MINUTES = 12
WAIT_MINUTES = 10
# Consultation starts (minutes from T) per doctor lane: a room is cleaned for 10 minutes after each
# consultation and the next patient is roomed one minute before the start.
LANE_STARTS = ((-10, 13, 36), (-5, 18, 41), (0, 23, 44))
WAITING_ROOM_FREE_AT = 59        # the tenth patient's consultation (Day Clinic Room 1 is free again)


def case_visit_ids(arrival_utc: datetime) -> List[str]:
    stamp = (arrival_utc + timedelta(hours=4)).strftime("%Y%m%d-%H%M")
    return [f"WRC-{stamp}-{i:02d}" for i in range(1, CASE_VISITS + 1)]


def ensure_case_rooms(db: Session, clinic_id: str) -> None:
    have = {r.name for r in db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic_id).all()}
    for i, name in enumerate(CASE_ROOMS):
        if name not in have:
            db.add(ClinicRoomModel(clinic_id=clinic_id, name=name, kind=CASE_ROOM_KIND, sort_order=10 + i))
    db.flush()


def _busy_intervals(db: Session, clinic_id: str, day_start: datetime, day_end: datetime,
                    exclude: List[str]) -> Dict[str, list]:
    """Consultations of each provider in the clinic that day: stored events and the generator's plan."""
    from app.models.patient_flow import SOURCE_GENERATOR
    from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY
    from app.services.visit_generator import plan_day
    busy: Dict[str, list] = {}
    day = DEFAULT_FLOW_POLICY.local_date(day_start + timedelta(hours=12))
    generator_active = (db.query(VisitEventModel.id)
                        .filter(VisitEventModel.clinic_id == clinic_id, VisitEventModel.source == SOURCE_GENERATOR)
                        .first())
    for d in ((day, day + timedelta(days=1), day + timedelta(days=2)) if generator_active else ()):
        for v in plan_day(clinic_id, d):
            times = {t: at for t, at, _ in v.events}
            if CONSULT_START in times:
                busy.setdefault(v.provider, []).append((times[CONSULT_START], times.get(CONSULT_END, day_end)))
    rows = (db.query(VisitEventModel)
            .filter(VisitEventModel.clinic_id == clinic_id, VisitEventModel.event_type.in_([CONSULT_START, CONSULT_END]),
                    VisitEventModel.event_time >= day_start, VisitEventModel.event_time < day_end,
                    VisitEventModel.superseded_at.is_(None), VisitEventModel.visit_id.notin_(exclude)).all())
    starts = {(e.visit_id): e for e in rows if e.event_type == CONSULT_START}
    ends = {(e.visit_id): e.event_time for e in rows if e.event_type == CONSULT_END}
    for vid, e in starts.items():
        if e.provider:
            busy.setdefault(e.provider, []).append((e.event_time, ends.get(vid, day_end)))
    return busy


def _assign_providers(db: Session, clinic_id: str, t0: datetime, schedule, exclude: List[str]) -> Dict[int, str]:
    """A provider for each visit who has no other consultation (stored, planned or in this case) while
    that visit is roomed and seen: one patient per doctor at a time."""
    local_day = (t0 + timedelta(hours=4)).date()
    day_start = datetime.combine(local_day, datetime.min.time()) - timedelta(hours=4)
    busy = _busy_intervals(db, clinic_id, day_start - timedelta(days=1), day_start + timedelta(days=2), exclude)
    providers = workbook_dataset.providers()
    chosen: Dict[int, str] = {}
    for lane, events, n in schedule:
        minutes = {t: m for t, m, _ in events}
        a = t0 + timedelta(minutes=minutes[ROOMED])
        b = t0 + timedelta(minutes=minutes[CONSULT_END])
        order = providers[lane % len(providers):] + providers[:lane % len(providers)]
        pick = next((p for p in order if not any(x < b and y > a for x, y in busy.get(p, []))), None)
        if pick is None:
            raise ValueError("No provider is free for every Day Clinic consultation at that time; "
                             "choose another arrival time (--arrival HH:MM).")
        chosen[n] = pick
        busy.setdefault(pick, []).append((a, b))
    return chosen


def _event(clinic_id: str, visit_id: str, event_type: str, at: datetime, person: Dict[str, str], provider: str,
           **extra) -> VisitEventModel:
    return VisitEventModel(
        event_key=f"case:{clinic_id}:{visit_id}:{event_type}", clinic_id=clinic_id, visit_id=visit_id,
        event_type=event_type, event_time=at, known_at=at, patient_ref=person.get("patient_ref"),
        patient_name=person.get("name"), provider=provider, department=CASE_DEPARTMENT, priority="Normal",
        source=CASE_SOURCE, source_role=SOURCE_ROLE_OF_EVENT.get(event_type), **extra)


def supersede_earlier_cases(db: Session, clinic_id: str, keep: List[str], now: datetime) -> int:
    """A new case run replaces earlier ones: their events stop counting from now (kept, never deleted)."""
    rows = (db.query(VisitEventModel)
            .filter(VisitEventModel.clinic_id == clinic_id, VisitEventModel.visit_id.like("WRC-%"),
                    VisitEventModel.visit_id.notin_(keep), VisitEventModel.superseded_at.is_(None)).all())
    for e in rows:
        e.superseded_at, e.superseded_by = now, "replaced by a new waiting-room case run"
    return len(rows)


def _schedule(t0: datetime) -> List[Tuple[int, List[Tuple[str, int, Optional[str]]], int]]:
    """(lane, [(event type, minute from T, room)], visit number) for the ten visits."""
    out = []
    n = 0
    for lane, starts in enumerate(LANE_STARTS):
        room = CASE_ROOMS[lane]
        for s in starts:
            n += 1
            a = s - WAIT_MINUTES
            end = s + CONSULT_MINUTES
            out.append((lane, [(ARRIVED, a, None), (FILE_READY, a + 2, None), (PAYER_APPROVED, a + 5, None),
                               (NURSING_DONE, a + 6, None), (HANDOFF_SENT, a + 6, None), (ROOMED, s - 1, room),
                               (HANDOFF_ACCEPTED, s - 1, None), (CONSULT_START, s, room), (CONSULT_END, end, room),
                               (BILLING, end + 2, None), (CHECKED_OUT, end + 5, None)], n))
    w = WAITING_ROOM_FREE_AT
    out.append((3, [(ARRIVED, 0, None), (FILE_READY, 2, None), (PAYER_APPROVED, 12, None), (NURSING_DONE, 15, None),
                    (HANDOFF_SENT, 15, None), (ROOMED, w - 1, CASE_ROOMS[0]), (HANDOFF_ACCEPTED, w - 1, None),
                    (CONSULT_START, w, CASE_ROOMS[0]), (CONSULT_END, w + CONSULT_MINUTES, CASE_ROOMS[0]),
                    (BILLING, w + CONSULT_MINUTES + 2, None), (CHECKED_OUT, w + CONSULT_MINUTES + 5, None)],
                CASE_VISITS))
    return out


def record_case(db: Session, clinic_id: str, arrival_utc: datetime, now: Optional[datetime] = None) -> List[str]:
    """Stores the case's events (once: delivering them again changes nothing). Returns the visit ids;
    the last one is the patient still waiting at T+45."""
    t0 = arrival_utc.replace(second=0, microsecond=0)
    now = now or datetime.utcnow()
    ids = case_visit_ids(t0)
    ensure_case_rooms(db, clinic_id)
    supersede_earlier_cases(db, clinic_id, ids, now)
    db.flush()
    schedule = _schedule(t0)
    providers = _assign_providers(db, clinic_id, t0, schedule, ids)
    people = [p for p in workbook_dataset.patients() if p.get("patient_ref")][:CASE_VISITS]
    existing = {k for (k,) in db.query(VisitEventModel.event_key).filter(VisitEventModel.visit_id.in_(ids)).all()}
    have_enc = {i for (i,) in db.query(EncounterModel.id).filter(EncounterModel.id.in_(ids)).all()}
    for lane, events, n in schedule:
        visit_id, person, provider = ids[n - 1], people[n - 1], providers[n]
        payer_required = person.get("payer") != "Self Pay"
        for event_type, minute, room in events:
            if event_type == PAYER_APPROVED and not payer_required:
                continue
            extra = {"room": room}
            if event_type == ARRIVED:
                extra["payer_required"] = payer_required
            if event_type in (HANDOFF_SENT, HANDOFF_ACCEPTED):
                extra.update(handoff_id=f"H-{visit_id}", destination="Consultation")
            e = _event(clinic_id, visit_id, event_type, t0 + timedelta(minutes=minute), person, provider, **extra)
            if e.event_key not in existing:
                db.add(e)
                existing.add(e.event_key)
        if visit_id not in have_enc:
            db.add(EncounterModel(
                id=visit_id, patient_id=person.get("name"), patient_ref=person.get("patient_ref"),
                mrn=person.get("mrn"), provider_id=provider, clinic_id=clinic_id,
                date=(t0 + timedelta(hours=4)).date().isoformat(), type=f"{CASE_DEPARTMENT} Visit",
                status=None, journey_stage=None, stage_timestamps=None, billing_status="Pending",
                priority="Normal", wait_time=None, department=CASE_DEPARTMENT, payer_network=person.get("payer"),
                avatar=None))
    db.commit()
    return ids


def latest_case(db: Session, clinic_id: str) -> Optional[List[str]]:
    row = (db.query(VisitEventModel.visit_id).filter(VisitEventModel.clinic_id == clinic_id,
                                                     VisitEventModel.visit_id.like("WRC-%"),
                                                     VisitEventModel.superseded_at.is_(None))
           .order_by(VisitEventModel.event_time.desc(), VisitEventModel.id.desc()).first())
    if not row:
        return None
    prefix = row[0].rsplit("-", 1)[0]
    return [f"{prefix}-{i:02d}" for i in range(1, CASE_VISITS + 1)]


def supersede(db: Session, visit_id: str, event_type: str, actor: str, now: Optional[datetime] = None) -> int:
    """A data correction: the event stops counting from now (it is kept, never deleted)."""
    now = now or datetime.utcnow()
    rows = db.query(VisitEventModel).filter(VisitEventModel.visit_id == visit_id,
                                            VisitEventModel.event_type == event_type,
                                            VisitEventModel.superseded_at.is_(None)).all()
    for e in rows:
        e.superseded_at, e.superseded_by = now, actor
    db.commit()
    return len(rows)


def restore(db: Session, visit_id: str, event_type: str) -> int:
    """Undoes a correction (the original event counts again)."""
    rows = db.query(VisitEventModel).filter(VisitEventModel.visit_id == visit_id,
                                            VisitEventModel.event_type == event_type,
                                            VisitEventModel.superseded_at.isnot(None),
                                            VisitEventModel.superseded_by != "replaced by a new waiting-room case run"
                                            ).all()
    for e in rows:
        e.superseded_at, e.superseded_by = None, None
    db.commit()
    return len(rows)


def redeliver(db: Session, visit_id: str, event_type: str) -> bool:
    """Delivers one of the case's events again with the same source id. Returns True if it was stored
    (it never is: the source id is unique, so a replayed event counts once)."""
    from sqlalchemy.exc import IntegrityError
    original = db.query(VisitEventModel).filter(VisitEventModel.visit_id == visit_id,
                                                VisitEventModel.event_type == event_type,
                                                VisitEventModel.source == CASE_SOURCE).first()
    if original is None:
        return False
    fields = ("event_key", "clinic_id", "visit_id", "event_type", "event_time", "patient_ref", "patient_name",
              "provider", "department", "priority", "room", "source", "source_role", "handoff_id", "destination",
              "payer_required")
    copy = VisitEventModel(**{f: getattr(original, f) for f in fields}, known_at=datetime.utcnow())
    db.add(copy)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def patient_left(db: Session, clinic_id: str, visit_id: str, now: Optional[datetime] = None) -> bool:
    """The waiting patient leaves before being seen (reception records it); the later consultation that
    had been expected for this patient no longer happens, so those future events are superseded."""
    now = (now or datetime.utcnow()).replace(second=0, microsecond=0)
    first = db.query(VisitEventModel).filter(VisitEventModel.visit_id == visit_id).first()
    if first is None:
        return False
    key = f"case:{clinic_id}:{visit_id}:{CANCELLED}"
    if db.query(VisitEventModel).filter(VisitEventModel.event_key == key).first():
        return False
    for e in db.query(VisitEventModel).filter(VisitEventModel.visit_id == visit_id, VisitEventModel.event_time > now,
                                              VisitEventModel.superseded_at.is_(None)).all():
        e.superseded_at, e.superseded_by = now, "patient left before being seen"
    db.add(VisitEventModel(event_key=key, clinic_id=clinic_id, visit_id=visit_id, event_type=CANCELLED,
                           event_time=now, known_at=now, patient_ref=first.patient_ref,
                           patient_name=first.patient_name, provider=first.provider, department=first.department,
                           priority=first.priority, source=CASE_SOURCE, source_role="reception",
                           detail="Left before being seen"))
    db.commit()
    return True
