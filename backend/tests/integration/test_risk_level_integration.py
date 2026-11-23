"""
Risk level end to end: the pipeline stores the evaluated level with its explanation, and the
signals API re-evaluates it at read time (a closer deadline raises the level by itself).
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.database import SessionLocal
from app.main import app
from app.models.signal import SignalModel
from app.models.user import User, UserRole
from app.schemas.service_communication import ServiceResponse, ServiceStatus
from app.services.processing_orchestrator import ProcessingOrchestrator

client = TestClient(app)


def _ok(payload):
    return ServiceResponse(status=ServiceStatus.SUCCESS, result_payload=payload, correlation_id="mock",
                           processing_time_ms=1)


@pytest.fixture
def mocked_pipeline():
    class Permitted:
        is_permitted = True

    def rules(rule_id):
        return _ok({"findings": [{"rule_id": rule_id, "result": "CONDITION_MET",
                                  "evaluation_id": f"EVAL-{uuid.uuid4().hex[:6]}"}]})

    with patch("app.services.processing_orchestrator.evidence_engine.invoke") as ev, \
            patch("app.services.processing_orchestrator.decision_context_engine.invoke") as ctx, \
            patch("app.services.processing_orchestrator.policy_engine.invoke") as pol, \
            patch("app.services.processing_orchestrator.rules_engine.invoke") as rule, \
            patch("app.services.processing_orchestrator.revenue_intelligence_engine.invoke") as rev:
        ev.return_value = _ok({"eos_003_package": {}})
        ctx.return_value = _ok({"primary_context": "Operations"})
        pol.return_value = _ok({"policy_result": Permitted(), "policy_version": "V1"})
        rev.return_value = _ok({"estimated_exposure": "UNAVAILABLE"})
        yield lambda rule_id: setattr(rule, "return_value", rules(rule_id))


def _run(rule_id, set_rule, risk_inputs):
    set_rule(rule_id)
    orchestrator = ProcessingOrchestrator()
    event = orchestrator.create_event(
        event_type="EHR", source="Practice Fusion",
        raw_payload={"detail": "synthetic", "org_id": "ORG-RISK", "clinic_id": "CLINIC-RISK",
                     "risk_inputs": risk_inputs},
        priority="Normal", correlation_id=f"J-{uuid.uuid4().hex[:6]}")
    orchestrator.process_event_background(event.id)
    db = SessionLocal()
    try:
        return db.query(SignalModel).filter(SignalModel.id == event.id).first()
    finally:
        db.close()


def test_pipeline_stores_the_evaluated_level_for_a_long_wait(mocked_pipeline):
    signal = _run("RULE-SCH-002", mocked_pipeline, {"wait_minutes": 52, "patient_priority": "Normal",
                                                    "room_available": True})
    assert signal is not None and signal.risk_level == "High"
    evaluation = signal.metadata_data["risk_evaluation"]
    assert evaluation["rule_id"] == "RISK-WAIT-TIME" and "52 minutes" in evaluation["reason"]
    assert signal.metadata_data["risk_kind"] == "wait_time"


def test_pipeline_urgent_no_show_is_critical_and_missing_data_caps_it(mocked_pipeline):
    urgent = _run("RULE-SCH-001", mocked_pipeline, {"visit_priority": "Urgent", "prior_no_shows_90d": 0,
                                                    "visit_value": "200"})
    assert urgent.risk_level == "Critical"
    unknown = _run("RULE-SCH-001", mocked_pipeline, {})
    assert unknown.risk_level == "Moderate"
    assert "visit priority" in unknown.metadata_data["risk_evaluation"]["missing_inputs"]


def org_admin():
    # No parameters: FastAPI would otherwise read them from the request's query string.
    return User(id=91, email="risk-admin@example.test", role=UserRole.ORGANIZATION_ADMINISTRATOR.value,
                org_id="ORG-RISK-API", clinic_id="CLINIC-RISK-API", is_active=True)


@pytest.fixture
def stored_claim_signal():
    sig_id = f"SIG-RISK-{uuid.uuid4().hex[:6]}"
    deadline = (datetime.utcnow() + timedelta(hours=20)).isoformat() + "Z"
    db = SessionLocal()
    db.add(SignalModel(
        id=sig_id, source="DHPO", type="Claim", message="[CO4] denial", timestamp=datetime.utcnow(),
        org_id="ORG-RISK-API", clinic_id="CLINIC-RISK-API", risk_level="Moderate",
        recommended_action="Append Modifier 25.",
        metadata_data={"pipeline_event_id": sig_id, "risk_kind": "claim_denial",
                       "risk_inputs": {"denial_code": "CO4", "denied_amount": "640.00", "currency": "AED",
                                       "resubmission_deadline": deadline, "claim_id": "CLM-RISK-1",
                                       "payer": "Daman", "denial_reason": "Modifier 25 missing",
                                       "required_evidence": ["Corrected claim with modifier 25"],
                                       "evidence_available": ["Corrected claim with modifier 25"]}}))
    db.commit()
    db.close()
    app.dependency_overrides[get_current_user] = org_admin
    yield sig_id
    app.dependency_overrides.clear()
    db = SessionLocal()
    db.query(SignalModel).filter(SignalModel.id == sig_id).delete()
    db.commit()
    db.close()


def test_signals_api_reevaluates_the_stored_inputs(stored_claim_signal):
    rows = client.get("/api/v1/signals").json()
    row = next(r for r in rows if r["id"] == stored_claim_signal)
    # Stored as Moderate when the deadline was far; with 20 hours left it is Critical now.
    assert row["risk_level"] == "Critical"
    assert row["risk_evaluation"]["rule_id"] == "RISK-CLAIM-DENIAL"
    assert "must start today" in row["risk_evaluation"]["reason"]
    # The screen's existing Problem, Reason and Recommended Action fields show the evaluated texts.
    assert row["problem"].startswith("Claim CLM-RISK-1 was denied by Daman with CO4: Modifier 25 missing.")
    assert "hours left before the payer resubmission deadline" in row["reason"]
    assert row["recommended_action"].startswith("Attach Corrected claim with modifier 25 from the EHR")
    assert row["risk_evaluation"]["autopilot_eligible"] is True
    summary = client.get("/api/v1/signals/summary").json()
    assert summary["critical_events"] >= 1


def _resubmit(sig_id):
    from types import SimpleNamespace
    from app.services.governance_registry import ActionType
    from app.services.operational_execution_engine import operational_execution_engine
    return operational_execution_engine._mock_claim_resubmission(
        SimpleNamespace(target_reference=sig_id, action_type=ActionType.RESUBMIT_CLAIM))


def test_synthetic_payer_resubmits_only_with_all_evidence(stored_claim_signal):
    from app.services.governance_registry import ExecutionResult
    ok = _resubmit(stored_claim_signal)
    assert ok["result"] == ExecutionResult.SUCCESS
    assert ok["submission"]["claim_id"] == "CLM-RISK-1" and ok["submission"]["acknowledgement"].startswith("ACK-")
    db = SessionLocal()
    s = db.query(SignalModel).filter(SignalModel.id == stored_claim_signal).first()
    meta = dict(s.metadata_data)
    meta["risk_inputs"] = dict(meta["risk_inputs"], evidence_available=[])
    s.metadata_data = meta
    db.commit()
    db.close()
    missing = _resubmit(stored_claim_signal)
    assert missing["result"] == ExecutionResult.FAILED and missing["error"] == "EVIDENCE_MISSING"
    assert _resubmit("NO-SUCH-SIGNAL")["error"] == "NO_CLAIM_DATA"


def test_uae_mappings_permit_claim_resubmission():
    from app.services.governance_registry import governance_registry
    from app.services.uae_claims_governance import UAE_RULE_VERSION, register_uae_claims_governance
    register_uae_claims_governance()
    mapping = governance_registry.get_recommendation_mapping_by_version("MAP-UAE-CO4", UAE_RULE_VERSION)
    assert mapping.allowed_action_types[0] == "RESUBMIT_CLAIM"


def _set_inputs(sig_id, **changes):
    db = SessionLocal()
    s = db.query(SignalModel).filter(SignalModel.id == sig_id).first()
    meta = dict(s.metadata_data)
    meta["risk_inputs"] = dict(meta["risk_inputs"], **changes)
    s.metadata_data = meta
    db.commit()
    db.close()


def test_evidence_retrieval_finds_ehr_documents(stored_claim_signal):
    r = client.post(f"/api/v1/signals/{stored_claim_signal}/evidence-retrieval")
    assert r.status_code == 200, r.text
    retrieval = r.json()["retrieval"]
    assert retrieval["complete"] is True and retrieval["documents"][0]["status"] == "FOUND"
    assert retrieval["documents"][0]["source"].startswith("EHR") and retrieval["documents"][0]["document_ref"]
    assert retrieval["requested_by"] == "risk-admin@example.test"


def test_missing_evidence_is_searched_and_raises_one_bell_notification(stored_claim_signal):
    from app.services.evidence_retrieval import _search_other_sources
    names = [f"Document {i}" for i in range(12)]
    found = [n for n in names if _search_other_sources("CLM-RISK-1", n)]
    lost = [n for n in names if not _search_other_sources("CLM-RISK-1", n)]
    assert found and lost  # the synthetic sources find some documents and not others
    _set_inputs(stored_claim_signal, required_evidence=[found[0], lost[0]], evidence_available=[])
    first = client.post(f"/api/v1/signals/{stored_claim_signal}/evidence-retrieval").json()
    second = client.post(f"/api/v1/signals/{stored_claim_signal}/evidence-retrieval").json()
    assert first["retrieval"]["complete"] is False and first["retrieval"]["not_found"] == [lost[0]]
    statuses = {d["name"]: d["status"] for d in first["retrieval"]["documents"]}
    assert statuses == {found[0]: "FOUND", lost[0]: "NOT_FOUND"}
    db = SessionLocal()
    try:
        notes = db.query(SignalModel).filter(SignalModel.type == "Notification").all()
        mine = [n for n in notes if (n.metadata_data or {}).get("about_signal") == stored_claim_signal]
        assert len(mine) == 1 and lost[0] in mine[0].message and "CLM-RISK-1" in mine[0].message
        stored = db.query(SignalModel).filter(SignalModel.id == stored_claim_signal).first()
        assert stored.metadata_data["risk_inputs"]["evidence_available"] == [found[0]]
        for n in mine:
            db.delete(n)
        db.commit()
    finally:
        db.close()
    assert second["retrieval"]["not_found"] == [lost[0]]


def test_evidence_retrieval_is_scoped_to_the_users_organization(stored_claim_signal):
    def outsider():
        return User(id=92, email="other@example.test", role=UserRole.ORGANIZATION_ADMINISTRATOR.value,
                    org_id="OTHER-ORG", clinic_id="OTHER-CLINIC", is_active=True)
    app.dependency_overrides[get_current_user] = outsider
    r = client.post(f"/api/v1/signals/{stored_claim_signal}/evidence-retrieval")
    assert r.status_code == 404
