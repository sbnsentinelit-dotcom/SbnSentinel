"""
Production Checks 01-04: card values from the server summary, no-show evidence, connector
readiness and the provider list. Each test uses its own clinic and removes its rows.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1.endpoints.signals import get_signal_summary
from app.db.database import SessionLocal
from app.main import app
from app.models.connector import ConnectorModel
from app.models.signal import SignalModel
from app.models.user import User, UserRole

client = TestClient(app)


@pytest.fixture
def clinic():
    clinic_id = f"CLINIC-PC-{uuid.uuid4().hex[:6]}"
    yield clinic_id
    db = SessionLocal()
    db.query(SignalModel).filter(SignalModel.clinic_id == clinic_id).delete()
    db.commit()
    db.close()


def _user(clinic_id):
    user = MagicMock()
    user.clinic_id = clinic_id
    user.org_id = "ORG-PC"
    user.role = UserRole.CLINIC_MANAGER.value
    return user


def _signal(clinic_id, sid, when, level="Moderate", action="Resubmit claim", exposure=None, **meta):
    return SignalModel(id=sid, clinic_id=clinic_id, org_id="ORG-PC", source="Test", type="Claim",
                       message=sid, timestamp=when, risk_level=level, recommended_action=action,
                       estimated_financial_exposure=exposure, metadata_data={"currency": "AED", **meta})


def test_summary_without_history_reports_unavailable_trends(clinic):
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(_signal(clinic, f"{clinic}-1", now - timedelta(hours=1), level="Critical", exposure="AED 640.00"))
        db.commit()
        res = get_signal_summary(db=db, current_user=_user(clinic))
        assert res["history_available"] is False
        assert all(t["trend"] is None for t in res["card_trends"].values())
        assert res["trend"] is None
    finally:
        db.close()


def test_summary_counts_critical_only_awaiting_and_known_exposure(clinic):
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(days=2)
    db = SessionLocal()
    try:
        db.add_all([
            _signal(clinic, f"{clinic}-c", earlier, level="Critical", exposure="AED 640.00"),
            _signal(clinic, f"{clinic}-h", earlier, level="High", exposure="AED 100.00"),
            _signal(clinic, f"{clinic}-m", now - timedelta(hours=2), level="Moderate", exposure="Unavailable"),
            _signal(clinic, f"{clinic}-i", now - timedelta(hours=2), level="Information", action="None required."),
            _signal(clinic, f"{clinic}-n", now - timedelta(hours=1), level="Information", action=None,
                    notification=True),
        ])
        db.commit()
        res = get_signal_summary(db=db, current_user=_user(clinic))
        assert res["active_signals"] == 4                      # the bell notification is not a signal
        assert res["awaiting_decision"] == 3                   # "None required." awaits nothing
        assert res["critical_events"] == 1 and res["high_events"] == 1
        assert res["critical_awaiting"] == 1
        exposure = res["awaiting_exposure"]
        assert exposure["totals"] == {"AED": 740.0}
        assert exposure["known_count"] == 2 and exposure["count"] == 3
        # Same measure at the earlier cutoff (yesterday): Critical and High were already open.
        trends = res["card_trends"]
        assert res["history_available"] is True
        assert trends["critical_events"]["trend"] == "+0%" and trends["critical_events"]["trend_good"] is None
        assert trends["active_signals"]["trend"] == "+100%"
    finally:
        db.close()


def test_rising_critical_events_is_marked_bad():
    from app.api.v1.endpoints.signals import _count_trend
    assert _count_trend(3, 1, False) == {"trend": "+200%", "trend_up": True, "trend_good": False, "previous": 1}
    assert _count_trend(1, 3, False)["trend_good"] is True
    assert _count_trend(5, 0, True)["trend"] == "+5"
    assert _count_trend(5000, 1, None)["trend"] == ">+999%"
    assert _count_trend(4, None, True)["trend"] is None


def test_no_show_without_schedule_evidence_is_not_a_schedule_gap(monkeypatch):
    from app.services import evidence_engine
    from app.services.decision_context_engine import decision_context_engine
    from app.services.rules_engine import rules_engine

    def classify(facts):
        store = {f"ev-{i}": fact for i, fact in enumerate(facts)}
        monkeypatch.setattr(evidence_engine.evidence_repository, "retrieve", lambda ref: store.get(ref))
        return decision_context_engine._process({
            "event_type": "EHR", "eos_003_package": {"evidence_references": list(store)}})

    plain = classify([{"fact_key": "appointment_status", "fact_value": "NO_SHOW", "canonical_entity": "Appointment"}])
    assert plain["secondary_context"] == "Patient No-Show"
    assert "capacity effect is unknown" in plain["reason"]
    gap = classify([{"fact_key": "appointment_status", "fact_value": "NO_SHOW", "canonical_entity": "Appointment"},
                    {"fact_key": "slot_status", "fact_value": "UNFILLED", "canonical_entity": "Appointment"}])
    assert gap["secondary_context"] == "Provider Schedule Gap"
    handler = rules_engine._handlers[("RULE-SCH-001", "V1")]
    for ctx in ("Patient No-Show", "Provider Schedule Gap"):
        assert handler({"primary_context": "Operational", "secondary_context": ctx}) == "CONDITION_MET"


def test_connected_status_alone_does_not_make_a_connector_ready():
    from app.services.connector_manager import connector_runtime_state
    name = f"PC-CONN-{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        db.add(ConnectorModel(id=name, name=name, type="EHR System", status="Connected", latency_ms=10,
                              last_sync=datetime.utcnow(), config={}))
        db.commit()
        connector_runtime_state._states.pop(name, None)
        state = connector_runtime_state.get(name)
        assert state.capability_state != "AUTHORIZED_READY"
    finally:
        db.query(ConnectorModel).filter(ConnectorModel.id == name).delete()
        db.commit()
        db.close()


def test_provider_list_comes_from_the_clinic_records():
    def user():
        return User(id=1, email="pc@test.local", role=UserRole.ORGANIZATION_ADMINISTRATOR.value,
                    org_id="ORG-PC", clinic_id="CLINIC-PC", is_active=True, hashed_password="x")
    app.dependency_overrides[get_current_user] = user
    try:
        res = client.get("/api/v1/patient-flow/providers")
        assert res.status_code == 200
        names = [p["name"] for p in res.json()]
        assert "Dr. Omar Ali" in names and "Dr. Sarah Mitchell" not in names
        assert all(p["specialty"] for p in res.json())
    finally:
        app.dependency_overrides.clear()
