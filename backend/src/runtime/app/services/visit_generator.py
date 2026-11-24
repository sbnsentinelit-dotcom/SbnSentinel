"""
Synthetic visit generator for the Live Patient Flow screen.

While no hospital / EHR queue feed is connected, this generator plays the part of the feed: it
produces visit events (arrived, ready, consultation start / end, lab, billing, checked out,
cancelled) for the workbook's patients and providers, the same events a real feed would deliver.
It never writes a status, a wait or a count: every number on the screen is calculated from the
events by services/patient_flow_service.py, and the calculation does not depend on the source.
Each event carries the team that records it (reception, records, nursing, payer, rooms, consultation
team, clinician), when it was known, the handoff identity, and whether the visit's service needs payer
approval (taken from the patient's workbook encounters).

The day plan is deterministic (seeded by clinic and date), so the same day always produces the
same visits; events are stored only once their time has passed, and only once (event_key).
Each provider sees one patient at a time and each consultation needs a free room, so waits and
delays come out of the clinic's load rather than being chosen.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.encounter import EncounterModel
from app.models.patient_flow import (
    ARRIVED, BILLING, CANCELLED, CHECKED_OUT, CONSULT_END, CONSULT_START, FILE_READY, HANDOFF_ACCEPTED,
    HANDOFF_SENT, LAB_END, LAB_START, NURSING_DONE, PAYER_APPROVED, READY, ROOMED, SOURCE_GENERATOR,
    SOURCE_ROLE_OF_EVENT, VisitEventModel,
)
from app.services import workbook_dataset
from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY, PatientFlowPolicy, local_midnight_utc

PLAN_VERSION = "v3"
HANDOFF_DESTINATION = "Consultation"
DEFAULT_CONSULT_ROOMS = ("Room 1", "Room 2", "Room 3")
LAB_ROOM = "Lab"


@dataclass
class PlannedVisit:
    visit_id: str
    day: date
    patient_ref: str
    patient_name: str
    mrn: Optional[str]
    payer: Optional[str]
    provider: str
    department: str
    priority: str
    payer_required: Optional[bool] = None
    events: List[Tuple[str, datetime, Optional[str]]] = field(default_factory=list)  # (type, utc time, room)


def _patient_departments() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for e in workbook_dataset.encounters():
        if e.get("patient_ref") and e.get("specialty"):
            out.setdefault(e["patient_ref"], []).append(e["specialty"])
    return out


def _authorization_by_patient_department() -> Dict[Tuple[str, str], bool]:
    """Whether the patient's workbook encounters in a department needed payer authorization."""
    out: Dict[Tuple[str, str], bool] = {}
    for e in workbook_dataset.encounters():
        if e.get("patient_ref") and e.get("specialty") and e.get("authorization_required") is not None:
            key = (e["patient_ref"], e["specialty"])
            out[key] = out.get(key, False) or bool(e["authorization_required"])
    return out


def plan_day(clinic_id: str, day: date, policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY,
             consult_rooms: Iterable[str] = DEFAULT_CONSULT_ROOMS) -> List[PlannedVisit]:
    """The clinic's synthetic visits for one local day, with every event time (UTC)."""
    patients = [p for p in workbook_dataset.patients() if p.get("patient_ref")]
    providers = workbook_dataset.providers()
    departments = workbook_dataset.specialties()
    if not patients or not providers:
        return []
    history = _patient_departments()
    authorization = _authorization_by_patient_department()
    rng = random.Random(f"{clinic_id}|{day.isoformat()}|{PLAN_VERSION}")
    open_utc = local_midnight_utc(day, policy) + timedelta(hours=policy.open_hour)
    span_minutes = (policy.close_hour - policy.open_hour) * 60 - 45

    count = rng.randint(40, 50)  # a busy outpatient day for 5 providers and 3 rooms
    arrivals = sorted(open_utc + timedelta(minutes=int(rng.triangular(0, span_minutes, span_minutes * 0.3)))
                      for _ in range(count))
    chosen = rng.sample(patients, count)

    rooms = list(consult_rooms) or list(DEFAULT_CONSULT_ROOMS)
    provider_free = {p: open_utc for p in providers}
    room_free = {r: open_utc for r in rooms}
    visits: List[PlannedVisit] = []

    for k, (arrival, pat) in enumerate(zip(arrivals, chosen), start=1):
        provider = rng.choice(providers)
        dept = rng.choice(history.get(pat["patient_ref"]) or departments)
        priority = "Urgent" if rng.random() < 0.22 else "Normal"
        visit = PlannedVisit(
            visit_id=f"VIS-{day.strftime('%Y%m%d')}-{k:03d}", day=day,
            patient_ref=pat["patient_ref"], patient_name=pat["name"], mrn=pat.get("mrn"),
            payer=pat.get("payer"), provider=provider, department=dept, priority=priority,
            # Self-pay needs no payer approval; otherwise the workbook says whether this service did.
            payer_required=(False if pat.get("payer") == "Self Pay"
                            else authorization.get((pat["patient_ref"], dept))),
        )
        # Preparation runs in parallel: file (records), nursing assessment, payer approval when needed.
        file_ready = arrival + timedelta(minutes=rng.randint(1, 4))
        nursing = arrival + timedelta(minutes=rng.randint(4, 12))
        approval = arrival + timedelta(minutes=rng.randint(3, 15)) if visit.payer_required else None
        ready = max(t for t in (file_ready, nursing, approval) if t)
        prep = [(ARRIVED, arrival, None), (FILE_READY, file_ready, None), (NURSING_DONE, nursing, None)]
        if approval:
            prep.append((PAYER_APPROVED, approval, None))
        prep += [(READY, ready, None), (HANDOFF_SENT, ready, None)]

        room = min(rooms, key=lambda r: (room_free[r], r))
        start = max(ready, provider_free[provider], room_free[room])
        expected_wait = (start - arrival).total_seconds() / 60
        if expected_wait > 45 and rng.random() < 0.35:
            # Left without being seen.
            left = arrival + timedelta(minutes=rng.randint(35, 50))
            visit.events.extend(e for e in prep if e[1] < left)
            visit.events.append((CANCELLED, left, None))
            visits.append(visit)
            continue

        duration = rng.randint(12, 28)
        end = start + timedelta(minutes=duration)
        # The patient is roomed a little before the consultation starts (once the room is free); the
        # consultation team accepts the handoff when it calls the patient in.
        roomed = max(room_free[room], ready, start - timedelta(minutes=rng.randint(1, 3)))
        provider_free[provider] = end
        room_free[room] = end + timedelta(minutes=policy.cleaning_minutes)
        visit.events.extend(prep)
        visit.events.append((ROOMED, roomed, room))
        visit.events.append((HANDOFF_ACCEPTED, roomed, None))
        visit.events.append((CONSULT_START, start, room))
        visit.events.append((CONSULT_END, end, room))

        after = end
        if rng.random() < 0.3:
            lab_start = end + timedelta(minutes=rng.randint(2, 5))
            lab_end = lab_start + timedelta(minutes=rng.randint(10, 20))
            visit.events.append((LAB_START, lab_start, LAB_ROOM))
            visit.events.append((LAB_END, lab_end, LAB_ROOM))
            after = lab_end
        billing = after + timedelta(minutes=rng.randint(2, 5))
        visit.events.append((BILLING, billing, None))
        visit.events.append((CHECKED_OUT, billing + timedelta(minutes=rng.randint(3, 8)), None))
        visits.append(visit)
    return visits


def _event_key(clinic_id: str, visit_id: str, event_type: str) -> str:
    return f"gen:{clinic_id}:{visit_id}:{event_type}"


def sync_generated_events(db: Session, clinic_id: str, now: Optional[datetime] = None, days: int = 7,
                          policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> int:
    """Stores the planned events whose time has passed (today and the previous `days` days) and
    creates the encounter record of each visit that has arrived. Returns the number of new events."""
    from app.services.patient_flow_service import (
        clinic_room_names, ensure_rooms, record_feed_heartbeat, refresh_encounter_cache)

    now = now or datetime.utcnow()
    record_feed_heartbeat(clinic_id, now)
    today = policy.local_date(now)
    ensure_rooms(db, clinic_id)
    rooms = [r for r in clinic_room_names(db, clinic_id, kind="consult")] or list(DEFAULT_CONSULT_ROOMS)
    window_start = local_midnight_utc(today - timedelta(days=days), policy)
    existing = {k for (k,) in db.query(VisitEventModel.event_key)
                .filter(VisitEventModel.clinic_id == clinic_id,
                        VisitEventModel.source == SOURCE_GENERATOR,
                        VisitEventModel.event_time >= window_start).all()}
    existing_visits = {v for (v,) in db.query(EncounterModel.id)
                       .filter(EncounterModel.id.like("VIS-%"), EncounterModel.clinic_id == clinic_id).all()}

    added = 0
    touched = set()
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        for visit in plan_day(clinic_id, day, policy, rooms):
            due = [(t, at, room) for (t, at, room) in visit.events if at <= now]
            if not due:
                continue
            if visit.visit_id not in existing_visits:
                db.add(EncounterModel(
                    id=visit.visit_id, patient_id=visit.patient_name, patient_ref=visit.patient_ref,
                    mrn=visit.mrn, provider_id=visit.provider, clinic_id=clinic_id,
                    date=visit.day.isoformat(), type=f"{visit.department} Visit", status=None,
                    journey_stage=None, stage_timestamps=None, billing_status="Pending",
                    priority=visit.priority, wait_time=None, department=visit.department,
                    payer_network=visit.payer, avatar=None,
                ))
                existing_visits.add(visit.visit_id)
            for event_type, at, room in due:
                key = _event_key(clinic_id, visit.visit_id, event_type)
                if key in existing:
                    continue
                handoff = event_type in (HANDOFF_SENT, HANDOFF_ACCEPTED)
                db.add(VisitEventModel(
                    event_key=key, clinic_id=clinic_id, visit_id=visit.visit_id, event_type=event_type,
                    event_time=at, patient_ref=visit.patient_ref, patient_name=visit.patient_name,
                    provider=visit.provider, department=visit.department, priority=visit.priority,
                    room=room, source=SOURCE_GENERATOR, known_at=at,
                    source_role=SOURCE_ROLE_OF_EVENT.get(event_type),
                    handoff_id=f"H-{visit.visit_id}" if handoff else None,
                    destination=HANDOFF_DESTINATION if handoff else None,
                    payer_required=visit.payer_required if event_type == ARRIVED else None,
                ))
                existing.add(key)
                touched.add(visit.visit_id)
                added += 1
    db.flush()
    if touched:
        refresh_encounter_cache(db, clinic_id, touched, now)
    db.commit()
    return added
