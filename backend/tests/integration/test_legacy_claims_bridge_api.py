"""
Legacy Claims Continuity Bridge through the real API, service and database: an administrator opens a
transition and binds eight sources; each source delivers its page and completeness seal; the
assessment, figures, work items and subscription exit review are read back from the stored evidence.
Also: replay, token and predecessor conflicts, role and scope checks, missing bank proof, conflicting
content, raw IDs shared by two books, reproducible assessments and the feature flag.
"""
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import SessionLocal
from app.legacy_bridge import scenario
from app.legacy_bridge.assessment import digest_rows
from app.legacy_bridge.contracts import Record
from app.main import app
from app.models import legacy_bridge as m
from app.models.audit import AuditLogModel
from app.models.evidence import EvidenceModel
from app.models.user import User, UserRole
from app.services import legacy_bridge_service as svc

client = TestClient(app)
API = "/api/v1/legacy-claims"
CUTOVER = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _user(clinic, role=UserRole.ORGANIZATION_ADMINISTRATOR.value, org="ORG-T"):
    return User(id=hash(clinic + role) % 100000, email=f"{role.split()[0].lower()}@{clinic.lower()}.test",
                role=role, clinic_id=clinic, org_id=org, is_active=True)


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture
def clinic():
    clinic_id = f"LCB-{uuid.uuid4().hex[:8].upper()}"
    _as(_user(clinic_id))
    yield clinic_id
    app.dependency_overrides.clear()
    db = SessionLocal()
    ids = [t for (t,) in db.query(m.LegacyTransitionModel.id).filter(
        m.LegacyTransitionModel.clinic_id.like(clinic_id + "%")).all()]
    for model in (m.LegacyWorkItemModel, m.LegacyAssessmentModel, m.LegacySealModel, m.LegacyRecordModel,
                  m.LegacyStreamHeadModel, m.LegacyPageModel, m.LegacySourceBindingModel):
        db.query(model).filter(model.transition_id.in_(ids)).delete(synchronize_session=False)
    db.query(m.LegacyTransitionModel).filter(m.LegacyTransitionModel.id.in_(ids)).delete(synchronize_session=False)
    db.query(m.LegacyExitInventoryModel).filter(m.LegacyExitInventoryModel.clinic_id.like(clinic_id + "%")).delete(
        synchronize_session=False)
    db.query(EvidenceModel).filter(EvidenceModel.clinic_id.like(clinic_id + "%")).delete(synchronize_session=False)
    db.commit()
    db.close()


def _open(clinic_id):
    r = client.post(f"{API}/transitions", content=json.dumps({
        "facility": clinic_id, "payee": clinic_id, "payer": "TPA1", "currency": "AED",
        "cutover": "2026-11-01T00:00:00Z", "label": "Old billing run-off", **scenario.TRANSITION}))
    assert r.status_code == 201, r.text
    t = r.json()
    bindings = {}
    for role in scenario.ROLES:
        b = client.post(f"{API}/transitions/{t['id']}/sources", content=json.dumps({
            "connector": f"src-{role}", "role": role, "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2027-06-01T00:00:00Z"}))
        assert b.status_code == 201, b.text
        bindings[role] = b.json()["binding_id"]
    return t, bindings


def _seal(t, role, rows):
    recs = [Record.model_validate_json(json.dumps({**r, "scope": t["scope"], "connector": f"src-{role}"}))
            for r in rows]
    end = scenario.instants(CUTOVER.replace(tzinfo=None))["end"]
    return {"through": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "unique_rows": len(recs), "digest": digest_rows(recs),
            "receipt": f"seal-{role}"}


def _deliver(t, bindings, skip=(), change=None):
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    if change:
        change(rows)
    out = {}
    for role in scenario.ROLES:
        if role in skip:
            continue
        r = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps({
            "binding_id": bindings[role], "token": f"{role}-1", "records": rows[role],
            "seal": _seal(t, role, rows[role])}))
        assert r.status_code == 200, r.text
        out[role] = r.json()
    return out, rows


def _assess(t, **params):
    r = client.get(f"{API}/transitions/{t['id']}/assessment", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_full_transition_is_reviewable_with_figures_and_owned_work(clinic):
    t, bindings = _open(clinic)
    staged, _rows = _deliver(t, bindings)
    assert all(p["status"] == "STAGED" for p in staged.values())
    a = _assess(t)
    assert a["status"] == "SAMPLE_REVIEWABLE"
    assert a["issues"] == []
    assert a["as_of"] == "2026-11-05T18:00:00Z"
    assert a["review_quality"] == "RESEARCH_ONLY"
    s = a["summary"]
    assert s["opening"]["payer_ar"] == 100000 and s["closing"]["payer_ar"] == 30000
    assert s["opening"]["credit"] == -500 and s["closing"]["credit"] == -500
    assert s["cash"] == {"advised": 90000, "bank_confirmed": 90000, "ledger_posted": 90000,
                         "provider_adjustments": -10000, "unapplied_bank": 0, "noncash_advices": 0}
    # The same raw claim and line IDs in the old and new books stay two lines.
    assert s["lines"] == {"legacy": 3, "new": 1}
    assert all(r["proved"] for r in s["routes"]) and len(s["routes"]) == 6
    assert {(w["code"], w["owner"], w["state"]) for w in a["work_items"]} == {
        ("WORK_DENIAL", "bill-team", "OPEN"), ("REVIEW_PROVIDER_ADJUSTMENT", "finance-lead", "OPEN")}
    assert s["denials"][0]["due"] == "2026-11-06T18:00:00Z" and s["denials"][0]["owner"] == "bill-team"
    # Every page left a custody row in the evidence repository.
    db = SessionLocal()
    assert db.query(EvidenceModel).filter(EvidenceModel.clinic_id == clinic,
                                          EvidenceModel.canonical_entity == "LegacyClaimsPage").count() == 8
    db.close()


def test_same_evidence_gives_the_same_assessment_row(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    first, second = _assess(t), _assess(t)
    assert first["assessment_id"] == second["assessment_id"]
    history = client.get(f"{API}/transitions/{t['id']}/assessments").json()
    assert len(history) == 1


def test_replayed_page_is_stored_once_and_a_changed_page_under_the_token_is_refused(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    body = {"binding_id": bindings["bank"], "token": "bank-1", "records": rows["bank"],
            "seal": _seal(t, "bank", rows["bank"])}
    again = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(body))
    assert again.status_code == 200 and again.json()["status"] == "ALREADY_STAGED"
    changed = copy.deepcopy(body)
    changed["records"][0]["data"]["minor"] = 91000
    assert client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(changed)).status_code == 409
    db = SessionLocal()
    assert db.query(m.LegacyRecordModel).filter(m.LegacyRecordModel.transition_id == t["id"],
                                                m.LegacyRecordModel.kind == "deposit").count() == 1
    db.close()


def test_a_page_must_follow_the_stream_head(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    late = {"binding_id": bindings["bank"], "token": "bank-2", "previous": None, "records": rows["bank"]}
    r = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(late))
    assert r.status_code == 409
    assert r.json()["detail"]["expected_previous"] == "bank-1"
    late["previous"] = "bank-1"
    assert client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(late)).json()["status"] == "STAGED"


def test_a_source_cannot_deliver_another_roles_records_and_nothing_is_stored(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    r = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(
        {"binding_id": bindings["bank"], "token": "x-1", "records": rows["bank"] + rows["ledger"][:1]}))
    assert r.status_code == 422
    db = SessionLocal()
    assert db.query(m.LegacyRecordModel).filter(m.LegacyRecordModel.transition_id == t["id"]).count() == 0
    assert db.query(m.LegacyPageModel).filter(m.LegacyPageModel.transition_id == t["id"]).count() == 0
    db.close()


def test_a_record_for_another_scope_or_observed_in_the_future_is_refused(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    other = copy.deepcopy(rows["bank"][0])
    other["scope"] = {**t["scope"], "payer": "OTHER"}
    r = client.post(f"{API}/transitions/{t['id']}/pages",
                    content=json.dumps({"binding_id": bindings["bank"], "records": [other]}))
    assert r.status_code == 422
    future = copy.deepcopy(rows["bank"][0])
    future["seen"] = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = client.post(f"{API}/transitions/{t['id']}/pages",
                    content=json.dumps({"binding_id": bindings["bank"], "records": [future]}))
    assert r.status_code == 422


def test_missing_bank_evidence_holds_the_transition(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings, skip=("bank",))
    a = _assess(t)
    codes = {i["code"] for i in a["issues"]}
    assert a["status"] == "HOLD"
    assert {"BANK_PROOF_INCOMPLETE", "COVERAGE_UNPROVEN"} <= codes
    assert all(i["message"] for i in a["issues"])
    assert a["summary"]["cash"]["bank_confirmed"] == 0


def test_the_same_business_record_with_other_content_is_held(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    resent = copy.deepcopy(rows["bank"])
    resent[0]["data"]["minor"] = 80000
    resent[0]["evidence"] = "ev-bank-1-reissue"
    r = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(
        {"binding_id": bindings["bank"], "token": "bank-2", "previous": "bank-1", "records": resent}))
    assert r.json()["status"] == "STAGED"
    a = _assess(t)
    assert a["status"] == "HOLD"
    assert "CONFLICTING_BUSINESS_EVENT" in {i["code"] for i in a["issues"]}


def test_work_item_acceptance_is_recorded_and_audited(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    item = next(w for w in _assess(t)["work_items"] if w["code"] == "WORK_DENIAL")
    r = client.post(f"{API}/work-items/{item['id']}/accept", json={"note": "Appeal packet in preparation"})
    assert r.status_code == 200 and r.json()["state"] == "ACCEPTED"
    assert client.post(f"{API}/work-items/{item['id']}/accept", json={}).status_code == 409
    db = SessionLocal()
    assert db.query(AuditLogModel).filter(AuditLogModel.action == "LEGACY_WORK_ITEM_ACCEPTED",
                                          AuditLogModel.correlation_id == f"{t['id']}:{item['id']}").count() == 1
    db.close()


def test_scope_and_roles_are_enforced(clinic):
    t, bindings = _open(clinic)
    _as(_user(clinic + "-OTHER", org="ORG-OTHER"))
    assert client.get(f"{API}/transitions/{t['id']}").status_code == 404
    assert client.get(f"{API}/transitions").json() == []
    _as(_user(clinic, role=UserRole.FRONT_DESK.value))
    assert client.get(f"{API}/transitions").status_code == 403
    _as(_user(clinic, role=UserRole.READ_ONLY_AUDITOR.value))
    assert client.get(f"{API}/transitions/{t['id']}").status_code == 200
    assert client.post(f"{API}/transitions/{t['id']}/pages", content="{}").status_code == 403
    _as(_user(clinic, role=UserRole.CLINIC_MANAGER.value))
    assert client.post(f"{API}/transitions/{t['id']}/sources", content="{}").status_code == 403
    # Source names never reach the screen.
    assert "connector" not in json.dumps(client.get(f"{API}/transitions/{t['id']}/sources").json())


def test_subscription_exit_review(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    inventory = scenario.exit_inventory(t["scope"], CUTOVER.replace(tzinfo=None), "old-billing")
    r = client.post(f"{API}/exit-inventories", content=json.dumps(inventory))
    assert r.status_code == 201, r.text
    review = client.get(f"{API}/exit-inventories/{r.json()['inventory_id']}/review").json()
    assert review["status"] == "HUMAN_EXIT_REVIEW" and review["issues"] == []
    assert review["scopes"][0]["status"] == "SAMPLE_REVIEWABLE"
    assert review["savings"] == {"currency": "AED", "avoided": 600000, "incremental": 220000, "net": 380000}
    del inventory["signoffs"]["records"]
    r = client.post(f"{API}/exit-inventories", content=json.dumps(inventory))
    review = client.get(f"{API}/exit-inventories/{r.json()['inventory_id']}/review").json()
    assert review["status"] == "HOLD"
    assert "MISSING_SIGNOFF" in {i["code"] for i in review["issues"]}


def test_feature_flag_turns_the_bridge_off(clinic, monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_BRIDGE_ENABLED", False, raising=False)
    assert client.get(f"{API}/transitions").status_code == 404
    assert client.get(f"{API}/status").json()["enabled"] is False
    monkeypatch.setattr(settings, "LEGACY_BRIDGE_ENABLED", None, raising=False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "PRODUCTION", raising=False)
    assert svc.bridge_enabled() is False


def test_synthetic_sources_deliver_through_the_intake_once(clinic):
    db = SessionLocal()
    now = datetime(2026, 11, 25, 12, 0)
    tid = svc.deliver_synthetic(db, clinic, now=now)
    assert svc.deliver_synthetic(db, clinic, now=now) == tid
    t = db.get(m.LegacyTransitionModel, tid)
    assert db.query(m.LegacyRecordModel).filter(m.LegacyRecordModel.transition_id == tid).count() == 32
    assert svc.latest_assessment(db, t).status == "SAMPLE_REVIEWABLE"
    assert db.query(m.LegacyAssessmentModel).filter(m.LegacyAssessmentModel.transition_id == tid).count() == 1
    db.close()
