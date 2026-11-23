"""
Patient Flow end to end: the workbook import, the visit generator, the read-only snapshot API and
the recorded staff actions (Production Check 05, tests 1 and 7, and the clinic scope).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user
from app.db.database import Base, SessionLocal
from app.main import app
from app.models.encounter import EncounterModel
from app.models.patient_flow import (
    ARRIVED, CONSULT_START, READY, ClinicRoomModel, PatientFlowActionModel, VisitEventModel,
)
from app.models.user import User, UserRole
from app.services import workbook_dataset
from app.services.patient_flow_service import ensure_rooms, snapshot
from app.services.visit_generator import plan_day, sync_generated_events

client = TestClient(app)


def _counts(db, clinic):
    return (db.query(VisitEventModel).filter(VisitEventModel.clinic_id == clinic).count(),
            db.query(EncounterModel).filter(EncounterModel.clinic_id == clinic).count(),
            db.query(PatientFlowActionModel).filter(PatientFlowActionModel.clinic_id == clinic).count(),
            db.query(ClinicRoomModel).filter(ClinicRoomModel.clinic_id == clinic).count())


def test_workbook_encounters_keep_their_identifiers_and_dates():
    rows = workbook_dataset.encounters()
    assert len(rows) == 789
    first = next(r for r in rows if r["encounter_id"] == "ENC2026000001")
    assert first["patient_ref"] == "PAT20260001" and first["mrn"] == "MRN-000001"
    assert first["patient_name"] == "Mariam Ali" and first["date"] == "2026-01-15"
    assert {r["status"] for r in rows} == {"Completed", "Cancelled"}
    assert min(r["date"] for r in rows) == "2026-01-01" and max(r["date"] for r in rows) == "2026-10-10"
    reem = [r for r in rows if r["patient_ref"] == "PAT20260002"]
    assert len(reem) == 2 and {r["patient_name"] for r in reem} == {"Reem Malik"}


def test_seed_imports_the_workbook_without_invented_live_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        from app.services.dubai_seed_service import seed_dubai_data
        seed_dubai_data(db)
        encounters = db.query(EncounterModel).all()
        assert len(encounters) == 789
        assert all(e.wait_time is None and not e.stage_timestamps for e in encounters)
        assert {e.status for e in encounters} == {"Completed", "Cancelled"}
        enc = db.query(EncounterModel).filter(EncounterModel.id == "ENC2026000001").first()
        assert (enc.patient_ref, enc.mrn, enc.date) == ("PAT20260001", "MRN-000001", "2026-01-15")
        assert db.query(VisitEventModel).count() == 0
        # Workbook only: 789 historical visits, no open visit, no measured wait, no suggestion.
        snap = snapshot(db, "DXB-CLINIC-01")
        assert snap["queue"] == [] and snap["suggestions"] == []
        assert snap["cards"]["avg_wait"]["display"] == "No verified waits"
        assert all(r["status"] == "Available" for r in snap["rooms"])
        assert snap["feed"] == {"last_event_at": None, "synced": False}
    finally:
        db.close()


def test_generator_plan_is_deterministic_and_uses_workbook_people():
    day = datetime(2026, 11, 20).date()
    a, b = plan_day("C-DET", day), plan_day("C-DET", day)
    assert [(v.visit_id, v.patient_ref, v.events) for v in a] == [(v.visit_id, v.patient_ref, v.events) for v in b]
    refs = set(workbook_dataset.patients_by_ref())
    providers = set(workbook_dataset.providers())
    assert all(v.patient_ref in refs and v.provider in providers for v in a)
    # one provider sees one patient at a time
    for prov in providers:
        spans = sorted((dict((t, x) for t, x, _ in v.events).get(CONSULT_START), dict((t, x) for t, x, _ in v.events).get("CONSULT_END"))
                       for v in a if v.provider == prov and any(t == CONSULT_START for t, _, _ in v.events))
        assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))


@pytest.fixture
def clinic():
    clinic_id = f"CLINIC-PF-{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    ensure_rooms(db, clinic_id)
    db.commit()
    db.close()
    yield clinic_id
    app.dependency_overrides.clear()
    db = SessionLocal()
    for model in (VisitEventModel, PatientFlowActionModel, ClinicRoomModel, EncounterModel):
        db.query(model).filter(model.clinic_id == clinic_id).delete()
    db.commit()
    db.close()


def _login(clinic_id, role=UserRole.FRONT_DESK.value):
    def user():
        return User(id=1, email="frontdesk@test.local", role=role, org_id="ORG-PF", clinic_id=clinic_id,
                    is_active=True, hashed_password="x")
    app.dependency_overrides[get_current_user] = user


def _add(db, clinic, visit, event_type, minutes_ago, provider="Dr. Reem Malik", room=None,
         ref="PAT20260001", name="Mariam Ali"):
    now = datetime.utcnow()
    db.add(VisitEventModel(event_key=f"test:{clinic}:{visit}:{event_type}", clinic_id=clinic, visit_id=visit,
                           event_type=event_type, event_time=now - timedelta(minutes=minutes_ago),
                           patient_ref=ref, patient_name=name, provider=provider, department="ENT",
                           priority="Normal", room=room, source="test"))
    if event_type == ARRIVED:
        db.add(EncounterModel(id=visit, patient_id=name, patient_ref=ref, mrn="MRN-000001", provider_id=provider,
                              clinic_id=clinic, date=now.date().isoformat(), type="ENT Visit"))


def test_generator_is_idempotent_and_reads_never_write(clinic):
    db = SessionLocal()
    try:
        added = sync_generated_events(db, clinic, days=1)
        assert added > 0
        assert sync_generated_events(db, clinic, days=1) == 0
        before = _counts(db, clinic)
    finally:
        db.close()
    _login(clinic)
    for period in ("Today", "Yesterday", "Last 7 Days"):
        res = client.get(f"/api/v1/patient-flow?period={period}")
        assert res.status_code == 200
        body = res.json()
        assert set(body["cards"]) == {"checked_in", "in_consultation", "avg_wait", "delayed"}
    res = client.get("/api/v1/patient-flow?period=Today")
    for row in res.json()["queue"][:3]:
        assert client.get(f"/api/v1/encounters/{row['id']}/journey").status_code == 200
    db = SessionLocal()
    try:
        assert _counts(db, clinic) == before
    finally:
        db.close()


def test_filters_apply_to_rows_and_cards(clinic):
    db = SessionLocal()
    _add(db, clinic, "V-ENT", ARRIVED, 10)
    db.add(VisitEventModel(event_key=f"test:{clinic}:V-CARD:A", clinic_id=clinic, visit_id="V-CARD",
                           event_type=ARRIVED, event_time=datetime.utcnow() - timedelta(minutes=12),
                           patient_name="Omar Khan", provider="Dr. Sara Khan", department="Cardiology",
                           priority="Urgent", source="test"))
    db.commit()
    db.close()
    _login(clinic)
    everything = client.get("/api/v1/patient-flow").json()
    cardio = client.get("/api/v1/patient-flow?department=Cardiology").json()
    urgent = client.get("/api/v1/patient-flow?priority=High").json()
    assert {r["id"] for r in everything["queue"]} == {"V-ENT", "V-CARD"}
    assert [r["id"] for r in cardio["queue"]] == ["V-CARD"] and cardio["cards"]["checked_in"]["value"] == 1
    assert [r["id"] for r in urgent["queue"]] == ["V-CARD"]
    assert everything["queue"][0]["id"] == "V-CARD"  # urgent first


def test_staff_reports_are_recorded_as_events_and_delayed_is_calculated(clinic):
    db = SessionLocal()
    _add(db, clinic, "V1", ARRIVED, 40)
    _add(db, clinic, "V1", READY, 35)
    db.commit()
    db.close()
    _login(clinic)
    assert client.post("/api/v1/patient-flow/visits/V1/status", json={"status": "Delayed"}).status_code == 409
    # Status is never overwritten: 403 in the workbook profile, 409 for event-backed visits otherwise.
    put = client.put("/api/v1/encounters/V1", json={"status": "Completed"})
    assert put.status_code in (403, 409)
    notes = client.put("/api/v1/encounters/V1", json={"clinical_notes": "Seen at triage."})
    assert notes.status_code == 200
    row = client.get("/api/v1/patient-flow").json()["queue"][0]
    assert row["status"] == "Delayed" and row["wait_minutes"] >= 40
    res = client.post("/api/v1/patient-flow/visits/V1/status", json={"status": "In Room"})
    assert res.status_code == 200 and res.json()["recorded"] is True and res.json()["room"] == "Room 1"
    db = SessionLocal()
    try:
        manual = db.query(VisitEventModel).filter(VisitEventModel.visit_id == "V1",
                                                  VisitEventModel.source == "manual").one()
        assert manual.event_type == CONSULT_START and manual.recorded_by == "frontdesk@test.local"
    finally:
        db.close()
    assert client.post("/api/v1/patient-flow/visits/V1/status", json={"status": "Waiting"}).status_code == 409
    journey = client.get("/api/v1/encounters/V1/journey").json()
    assert journey["current_stage"] == "Consultation" and journey["stage_sources"]["Consultation"] == "manual"


def test_suggestion_approval_sends_once_and_never_moves_the_patient(clinic):
    db = SessionLocal()
    _add(db, clinic, "C1", ARRIVED, 60, name="Hassan Ali", ref="PAT20260003")
    _add(db, clinic, "C1", CONSULT_START, 35, room="Room 1", name="Hassan Ali", ref="PAT20260003")
    _add(db, clinic, "N1", ARRIVED, 20)
    db.commit()
    db.close()
    _login(clinic)
    snap = client.get("/api/v1/patient-flow").json()
    late = next(s for s in snap["suggestions"] if s["type"] == "PROVIDER_RUNNING_LATE")
    assert late["visit_id"] == "N1" and "running 15 mins late" in late["text"]
    first = client.post(f"/api/v1/patient-flow/suggestions/{late['id']}/approve", json={}).json()
    again = client.post(f"/api/v1/patient-flow/suggestions/{late['id']}/approve", json={}).json()
    assert first["action_id"] == again["action_id"] and first["status"] == "SENT" and first["receipt_ref"]
    after = client.get("/api/v1/patient-flow").json()
    assert all(s["id"] != late["id"] for s in after["suggestions"])
    assert next(r for r in after["queue"] if r["id"] == "N1")["status"] == "Waiting"


def test_call_is_recorded_once_per_key_and_rooms_are_checked(clinic):
    db = SessionLocal()
    _add(db, clinic, "W1", ARRIVED, 5)
    _add(db, clinic, "W2", ARRIVED, 4, ref="PAT20260002", name="Reem Malik")
    db.commit()
    db.close()
    _login(clinic)
    key = {"idempotency_key": f"call:{uuid.uuid4().hex}"}
    a = client.post("/api/v1/patient-flow/visits/W1/call", json=key).json()
    b = client.post("/api/v1/patient-flow/visits/W1/call", json=key).json()
    assert a["action_id"] == b["action_id"] and a["status"] in ("COMPLETED", "NO_ANSWER") and a["receipt_ref"]
    assert client.post("/api/v1/patient-flow/visits/W1/room", json={"room": "Room 2"}).status_code == 200
    taken = client.post("/api/v1/patient-flow/visits/W2/room", json={"room": "Room 2"})
    assert taken.status_code == 409 and "occupied" in taken.json()["detail"]
    rooms = {r["name"]: r for r in client.get("/api/v1/patient-flow").json()["rooms"]}
    assert rooms["Room 2"]["status"] == "Occupied" and rooms["Room 2"]["visit_id"] == "W1"


def test_clinic_scope_is_enforced(clinic):
    _login(clinic)
    assert client.get("/api/v1/patient-flow?clinic_id=OTHER-CLINIC").status_code == 403
    _login(None)
    assert client.get("/api/v1/patient-flow").status_code == 403
    _login(clinic, role=UserRole.READ_ONLY_AUDITOR.value)
    assert client.get("/api/v1/patient-flow").status_code == 200
    assert client.post("/api/v1/patient-flow/visits/X/status", json={"status": "In Room"}).status_code == 403
