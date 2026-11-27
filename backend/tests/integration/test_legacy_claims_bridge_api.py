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
    for model in (m.LegacyWorkItemEventModel, m.LegacyWorkItemModel, m.LegacyAssessmentModel, m.LegacySealModel, m.LegacyRecordModel,
                  m.LegacyStreamHeadModel, m.LegacyPageModel, m.LegacySourceBindingModel):
        db.query(model).filter(model.transition_id.in_(ids)).delete(synchronize_session=False)
    db.query(m.LegacyTransitionModel).filter(m.LegacyTransitionModel.id.in_(ids)).delete(synchronize_session=False)
    inv_ids = [i for (i,) in db.query(m.LegacyExitInventoryModel.id).filter(
        m.LegacyExitInventoryModel.clinic_id.like(clinic_id + "%")).all()]
    db.query(m.LegacyExitSignoffModel).filter(m.LegacyExitSignoffModel.inventory_id.in_(inv_ids)).delete(
        synchronize_session=False)
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
    """Publish (POST), as a source does after its pages; then the assessment as it was published."""
    r = client.post(f"{API}/transitions/{t['id']}/assessments")
    assert r.status_code == 200, r.text
    return r.json()


def _read(t, **params):
    """Read only (GET)."""
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
    assert s["cash"] == {"advised": 90000, "bank_observed": 90000, "bank_confirmed": 90000, "bank_unmatched": 0,
                         "bank_coverage": "complete", "ledger_posted": 90000, "ledger_coverage": "complete",
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
    # No bank evidence: unknown, not a measured zero.
    cash = a["summary"]["cash"]
    assert cash["bank_coverage"] == "missing"
    assert cash["bank_observed"] is None and cash["bank_confirmed"] is None and cash["unapplied_bank"] is None


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
    assert svc.current_assessment(db, t).status == "SAMPLE_REVIEWABLE"
    assert db.query(m.LegacyAssessmentModel).filter(m.LegacyAssessmentModel.transition_id == tid).count() == 1
    db.close()


# ---------------------------------------------------------------- PR 07 review: findings 07.1 - 07.5 and release check

AT = scenario.instants(CUTOVER.replace(tzinfo=None))


def _z(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seal_at(t, role, rows, through, receipt):
    recs = [Record.model_validate_json(json.dumps({**r, "scope": t["scope"], "connector": f"src-{role}"}))
            for r in rows]
    return {"through": _z(through), "unique_rows": len(recs), "digest": digest_rows(recs), "receipt": receipt}


def _page(t, bindings, role, token, previous, records, sealed_rows, through):
    r = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps({
        "binding_id": bindings[role], "token": token, "previous": previous, "records": records,
        "seal": _seal_at(t, role, sealed_rows, through, f"seal-{role}-{token}")}))
    assert r.status_code == 200, r.text
    return r.json()


def _new_cutoff(t, bindings, rows, through, n, extra=None, closing=None):
    """Every source re-states its population through a new cutoff with a fresh completeness seal.
    The control source's population is its controls plus the closing snapshot of that cutoff."""
    extra = extra or {}
    for role in scenario.ROLES:
        new = extra.get(role, [])
        rows[role] = rows[role] + new
        sealed = rows[role]
        if role == "control":
            sealed = [r for r in rows["control"] if r["data"]["kind"] == "control"] + closing
        _page(t, bindings, role, f"{role}-{n}", f"{role}-{n - 1}", new, sealed, through)


def _closing_set(prefix, at, values):
    out = []
    for name, line, bucket in [("ar", dict(book="legacy", claim="C7", line="01"), "payer_ar"),
                               ("credit", dict(book="legacy", claim="C8", line="01"), "credit"),
                               ("watch", dict(book="legacy", claim="C9", line="01"), "closed_watch"),
                               ("patient", dict(book="legacy", claim="C7", line="01"), "patient_ar")]:
        if name in values:
            out.append(dict(key=f"{prefix}-{name}", evidence=f"ev-{prefix}-{name}", effective=_z(at), seen=_z(at),
                            revision=1, data=dict(kind="closing", line=line, bucket=bucket, minor=values[name])))
    return out


def _items(a):
    return {(w["code"], w["item"]): w for w in a["work_items"]}


def _accepting_user(clinic):
    return _user(clinic).email


def test_07_1_one_deposit_named_by_two_advices_is_observed_once_and_unmatched(clinic):
    t, bindings = _open(clinic)

    def second_advice(rows):
        twin = copy.deepcopy(next(r for r in rows["payer"] if r["key"] == "advice-1"))
        twin.update(key="advice-2", evidence="ev-advice-2")
        rows["payer"].append(twin)
    _deliver(t, bindings, change=second_advice)
    a = _assess(t)
    assert a["status"] == "HOLD"
    assert "AMBIGUOUS_PAYMENT_REFERENCE" in {i["code"] for i in a["issues"]}
    cash = a["summary"]["cash"]
    assert cash["bank_observed"] == 90000            # AED 900 once, not 1,800
    assert cash["bank_confirmed"] == 0               # the match is unresolved
    assert cash["bank_unmatched"] == 90000
    assert cash["bank_coverage"] == "complete"


def test_07_1_a_complete_bank_source_with_no_deposits_is_a_measured_zero(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings, change=lambda rows: rows.__setitem__("bank", []))
    cash = _assess(t)["summary"]["cash"]
    assert cash["bank_coverage"] == "complete"
    assert (cash["bank_observed"], cash["bank_confirmed"], cash["bank_unmatched"]) == (0, 0, 0)


def test_07_2_successive_unchanged_closing_snapshots_each_show_the_same_balance(clinic):
    t, bindings = _open(clinic)
    _, rows = _deliver(t, bindings)
    first = _assess(t)
    assert first["status"] == "SAMPLE_REVIEWABLE" and first["summary"]["closing"]["payer_ar"] == 30000
    # The next snapshot of the same unchanged balances, with distinct keys and fresh seals.
    end2 = AT["end"] + timedelta(hours=12)
    closing2 = _closing_set("closing2", end2, {"ar": 30000, "credit": -500, "watch": 0, "patient": 0})
    _new_cutoff(t, bindings, rows, end2, 2, extra={"control": closing2}, closing=closing2)
    second = _assess(t)
    assert second["as_of"] == _z(end2)
    assert second["status"] == "SAMPLE_REVIEWABLE", second["issues"]
    assert second["summary"]["closing"]["payer_ar"] == 30000          # not 60000
    assert second["summary"]["closing"]["credit"] == -500
    # Reading the earlier cutoff reproduces its original result, row and figures.
    earlier = _read(t, as_of=_z(AT["end"]))
    assert earlier["assessment_id"] == first["assessment_id"]
    assert earlier["status"] == "SAMPLE_REVIEWABLE" and earlier["summary"]["closing"]["payer_ar"] == 30000
    assert earlier["is_current"] is False and _read(t)["assessment_id"] == second["assessment_id"]


def test_07_2_a_changed_movement_reconciles_and_a_missing_closing_line_holds(clinic):
    t, bindings = _open(clinic)
    _, rows = _deliver(t, bindings)
    _assess(t)
    legacy = dict(book="legacy", claim="C7", line="01")
    end3 = AT["end"] + timedelta(hours=18)
    at = AT["end"] + timedelta(hours=6)
    approval = dict(key="approval-1", evidence="ev-approval-1", effective=_z(at), seen=_z(at), revision=1,
                    data=dict(kind="approval", line=legacy, bucket="payer_ar", minor=-10000,
                              reason="approved_adjustment"))
    movement = dict(key="delta-W", evidence="ev-delta-W", effective=_z(at), seen=_z(at), revision=1,
                    data=dict(kind="movement", line=legacy, bucket="payer_ar", minor=-10000,
                              reason="approved_adjustment", link="approval-1"))
    closing3 = _closing_set("closing3", end3, {"ar": 20000, "credit": -500, "watch": 0, "patient": 0})
    _new_cutoff(t, bindings, rows, end3, 2,
                extra={"control": closing3, "ledger": [movement], "operations": [approval]}, closing=closing3)
    changed = _assess(t)
    assert changed["status"] == "SAMPLE_REVIEWABLE", changed["issues"]
    assert changed["summary"]["closing"]["payer_ar"] == 20000

    end4 = end3 + timedelta(hours=1)
    closing4 = _closing_set("closing4", end4, {"ar": 20000, "watch": 0, "patient": 0})   # credit line missing
    _new_cutoff(t, bindings, rows, end4, 3, extra={"control": closing4}, closing=closing4)
    missing = _assess(t)
    assert missing["status"] == "HOLD"
    assert "CLOSING_VARIANCE" in {i["code"] for i in missing["issues"]}


def test_07_2_two_rows_for_one_position_in_a_snapshot_hold_with_an_explicit_reason(clinic):
    t, bindings = _open(clinic)

    def overlap(rows):
        dup = copy.deepcopy(next(r for r in rows["control"] if r["key"] == "closing-ar"))
        dup.update(key="closing-ar-copy", evidence="ev-closing-ar-copy")
        rows["control"].append(dup)
    _deliver(t, bindings, change=overlap)
    a = _assess(t)
    assert a["status"] == "HOLD"
    assert "CLOSING_SNAPSHOT_OVERLAP" in {i["code"] for i in a["issues"]}


def test_07_3_reads_never_change_work_or_the_current_assessment(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    published = _assess(t)
    task = _items(published)[("REVIEW_PROVIDER_ADJUSTMENT", "offset-18")]
    assert client.post(f"{API}/work-items/{task['id']}/accept", json={}).status_code == 200
    db = SessionLocal()

    def counts():
        return (db.query(m.LegacyAssessmentModel).filter(m.LegacyAssessmentModel.transition_id == t["id"]).count(),
                db.query(m.LegacyWorkItemEventModel).filter(
                    m.LegacyWorkItemEventModel.transition_id == t["id"]).count())
    before = counts()
    current = _read(t)
    earlier = _read(t, as_of=_z(AT["end"] - timedelta(hours=1)))    # an earlier cutoff: preview only
    assert earlier["published"] is False and earlier["work_items"] == []
    again, third = _read(t), _read(t)
    for view in (again, third):
        assert view["assessment_id"] == current["assessment_id"] == published["assessment_id"]
        assert _items(view) == _items(current)
    w = _items(third)[("REVIEW_PROVIDER_ADJUSTMENT", "offset-18")]
    assert w["state"] == "ACCEPTED" and w["accepted_by"] == _accepting_user(clinic)
    assert counts() == before
    db.close()


def test_07_3_resolution_and_reopening_keep_the_earlier_acceptance_in_history(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    _deliver(t, bindings, skip=("bank",))
    held = _assess(t)
    task = _items(held)[("RECONCILE_BANK_EVIDENCE", "bank")]
    client.post(f"{API}/work-items/{task['id']}/accept", json={"note": "Requesting the statement"})
    # Valid bank evidence resolves the investigation.
    _page(t, bindings, "bank", "bank-1", None, rows["bank"], rows["bank"], AT["end"])
    resolved = _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]
    assert resolved["state"] == "RESOLVED"
    # A second deposit for the same trace reopens it; the acceptance stays in the history.
    extra = dict(copy.deepcopy(rows["bank"][0]), key="bank-2", evidence="ev-bank-2")
    _page(t, bindings, "bank", "bank-2", "bank-1", [extra], rows["bank"] + [extra], AT["end"])
    reopened = _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]
    assert reopened["state"] == "OPEN" and reopened["accepted_by"] is None
    assert [e["event"] for e in reopened["history"]] == ["OPENED", "ACCEPTED", "RESOLVED", "REOPENED"]
    accepted = reopened["history"][1]
    assert accepted["actor"] == _accepting_user(clinic) and accepted["note"] == "Requesting the statement"


def test_07_4_every_blocking_discrepancy_has_an_assignable_investigation(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings, skip=("bank",))
    a = _assess(t)
    bank = _items(a)[("RECONCILE_BANK_EVIDENCE", "bank")]
    assert bank["kind"] == "hold" and bank["state"] == "OPEN" and bank["owner"] == "finance-reconciliation"
    assert {(i["code"], i["item"]) for i in bank["issues"]} == {
        ("BANK_PROOF_INCOMPLETE", "advice-1"), ("COVERAGE_UNPROVEN", "bank")}
    # Every hold reason is linked to exactly one investigation a reviewer can accept.
    linked = [(i["code"], i["item"]) for w in a["work_items"] if w["kind"] == "hold" for i in w["issues"]]
    assert sorted(linked) == sorted((i["code"], i["item"]) for i in a["issues"])
    # Reassessing cannot duplicate it.
    again = _assess(t)
    assert len(again["work_items"]) == len(a["work_items"])
    db = SessionLocal()
    assert db.query(m.LegacyWorkItemModel).filter(m.LegacyWorkItemModel.transition_id == t["id"],
                                                  m.LegacyWorkItemModel.code == "RECONCILE_BANK_EVIDENCE").count() == 1
    db.close()


def test_07_4_the_clinic_ownership_policy_names_the_responsible_team(clinic):
    r = client.post(f"{API}/transitions", content=json.dumps({
        "facility": clinic, "payee": clinic, "payer": "TPA1", "currency": "AED", "cutover": "2026-11-01T00:00:00Z",
        "hold_owners": {"RECONCILE_BANK_EVIDENCE": "treasury-desk"}, **scenario.TRANSITION}))
    assert r.status_code == 201, r.text
    t = r.json()
    bindings = {}
    for role in scenario.ROLES:
        bindings[role] = client.post(f"{API}/transitions/{t['id']}/sources", content=json.dumps({
            "connector": f"src-{role}", "role": role, "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2027-06-01T00:00:00Z"})).json()["binding_id"]
    _deliver(t, bindings, skip=("bank",))
    assert _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]["owner"] == "treasury-desk"


def test_07_5_exit_inventories_are_listed_for_the_selected_transition_only(clinic):
    t_a, _bindings = _open(clinic)
    r = client.post(f"{API}/transitions", content=json.dumps({
        "facility": clinic, "payee": clinic, "payer": "TPA2", "currency": "AED", "cutover": "2026-11-01T00:00:00Z",
        **scenario.TRANSITION}))
    assert r.status_code == 201, r.text
    t_b = r.json()
    inv_a = scenario.exit_inventory(t_a["scope"], CUTOVER.replace(tzinfo=None), "subscription-a")
    inv_b = scenario.exit_inventory(t_b["scope"], CUTOVER.replace(tzinfo=None), "subscription-b")
    id_a = client.post(f"{API}/exit-inventories", content=json.dumps(inv_a)).json()["inventory_id"]
    id_b = client.post(f"{API}/exit-inventories", content=json.dumps(inv_b)).json()["inventory_id"]
    for t, expected in ((t_a, id_a), (t_b, id_b)):
        listed = client.get(f"{API}/exit-inventories", params={"transition_id": t["id"]}).json()
        assert [x["inventory_id"] for x in listed] == [expected]
    assert len(client.get(f"{API}/exit-inventories").json()) == 2


def test_release_check_concurrent_pages_with_the_same_predecessor(clinic, monkeypatch):
    """Two ordinary pages naming the same predecessor at the same time: exactly one becomes the
    successor; the other gets a retryable conflict and leaves no page, record, seal or custody row."""
    import threading
    from app.legacy_bridge.intake import PageIn
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    pages = {}
    for name, minor in (("bank-2a", 1000), ("bank-2b", 2000)):
        rec = dict(copy.deepcopy(rows["bank"][0]), key=name, evidence=f"ev-{name}")
        rec["data"] = dict(rec["data"], trace=f"trace-{name}", minor=minor)
        pages[name] = PageIn.model_validate_json(json.dumps({
            "binding_id": bindings["bank"], "token": name, "previous": "bank-1", "records": [rec]}))
    both_read = threading.Barrier(2, timeout=20)
    real_read = svc._read_head
    first_read = set()
    guard = threading.Lock()

    def read_then_wait(db, tr, connector, resource):
        head = real_read(db, tr, connector, resource)
        me = threading.get_ident()
        with guard:
            first = me not in first_read
            first_read.add(me)
        if first:
            both_read.wait()          # both deliveries have seen head "bank-1" before either writes
        return head
    monkeypatch.setattr(svc, "_read_head", read_then_wait)
    results = {}

    def deliver(name):
        db = SessionLocal()
        try:
            tr = db.get(m.LegacyTransitionModel, t["id"])
            results[name] = ("ok", svc.stage_page(db, tr, pages[name], "source"))
        except svc.BridgeError as e:
            db.rollback()
            results[name] = ("conflict", e)
        finally:
            db.close()
    threads = [threading.Thread(target=deliver, args=(n,)) for n in pages]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert sorted(kind for kind, _ in results.values()) == ["conflict", "ok"], results
    winner = next(n for n, (kind, _) in results.items() if kind == "ok")
    loser = next(n for n in pages if n != winner)
    err = results[loser][1]
    assert err.status == 409 and err.detail["retryable"] is True and err.detail["expected_previous"] == winner
    db = SessionLocal()
    head = db.query(m.LegacyStreamHeadModel).filter(m.LegacyStreamHeadModel.transition_id == t["id"],
                                                    m.LegacyStreamHeadModel.connector == "src-bank").one()
    assert head.head_token == winner
    assert db.query(m.LegacyPageModel).filter(m.LegacyPageModel.token == loser,
                                              m.LegacyPageModel.transition_id == t["id"]).count() == 0
    assert db.query(m.LegacyRecordModel).filter(m.LegacyRecordModel.business_key == loser,
                                                m.LegacyRecordModel.transition_id == t["id"]).count() == 0
    assert db.query(EvidenceModel).filter(EvidenceModel.clinic_id == clinic,
                                          EvidenceModel.external_record_id == loser).count() == 0
    db.close()


# ---------------------------------------------------------------- PR 07 second review (780144a): R2 - R6

def _events(tid):
    db = SessionLocal()
    try:
        return db.query(m.LegacyWorkItemEventModel).filter(m.LegacyWorkItemEventModel.transition_id == tid).count()
    finally:
        db.close()


def _rows(model, tid):
    db = SessionLocal()
    try:
        return db.query(model).filter(model.transition_id == tid).count()
    finally:
        db.close()


def test_r2_publishing_before_any_seal_is_only_a_preview(clinic):
    t, bindings = _open(clinic)
    first, second = _assess(t), _assess(t)            # nothing delivered yet: two clicks
    for view in (first, second):
        assert view["published"] is False and view["assessment_id"] is None and view["work_items"] == []
    assert _rows(m.LegacyAssessmentModel, t["id"]) == 0 and _rows(m.LegacyWorkItemModel, t["id"]) == 0
    # An earlier-dated complete export then arrives and is published.
    _deliver(t, bindings)
    sealed = _assess(t)
    assert sealed["published"] is True and sealed["is_current"] is True
    assert sealed["status"] == "SAMPLE_REVIEWABLE" and sealed["as_of"] == _z(AT["end"])
    read = _read(t)
    assert read["assessment_id"] == sealed["assessment_id"] and read["status"] == "SAMPLE_REVIEWABLE"
    assert {w["code"] for w in read["work_items"]} == {"WORK_DENIAL", "REVIEW_PROVIDER_ADJUSTMENT"}
    events = _events(t["id"])
    _assess(t)
    _assess(t)                                        # replaying either operation adds nothing
    assert _events(t["id"]) == events and _rows(m.LegacyAssessmentModel, t["id"]) == 1


def test_r3_a_trace_shared_with_a_noncash_advice_is_not_confirmed_cash(clinic):
    t, bindings = _open(clinic)

    def noncash_on_same_trace(rows):
        rows["payer"].append(dict(
            key="advice-nc", evidence="ev-advice-nc", effective=_z(AT["day"]), seen=_z(AT["day"]), revision=1,
            data=dict(kind="advice", trace="pay-9", net=0, settlement="noncash",
                      parts=[dict(part="Z", type="claim_payment", minor=0,
                                  line=dict(book="legacy", claim="C7", line="01"))])))
    _deliver(t, bindings, change=noncash_on_same_trace)
    a = _assess(t)
    codes = {i["code"] for i in a["issues"]}
    assert a["status"] == "HOLD" and "AMBIGUOUS_PAYMENT_REFERENCE" in codes
    cash = a["summary"]["cash"]
    assert (cash["bank_observed"], cash["bank_confirmed"], cash["bank_unmatched"]) == (90000, 0, 90000)
    bank_task = _items(a)[("RECONCILE_BANK_EVIDENCE", "bank")]
    assert ("AMBIGUOUS_PAYMENT_REFERENCE", "pay-9") in {(i["code"], i["item"]) for i in bank_task["issues"]}


def test_r4_a_duplicated_closing_position_is_unverified_and_never_counted_twice(clinic):
    t, bindings = _open(clinic)

    def overlap(rows):
        dup = copy.deepcopy(next(r for r in rows["control"] if r["key"] == "closing-ar"))
        dup.update(key="closing-ar-copy", evidence="ev-closing-ar-copy")
        rows["control"].append(dup)
    _deliver(t, bindings, change=overlap)
    a = _assess(t)
    assert a["status"] == "HOLD" and "CLOSING_SNAPSHOT_OVERLAP" in {i["code"] for i in a["issues"]}
    s = a["summary"]
    assert s["closing"]["payer_ar"] == 0                        # undisputed subtotal; never 60000
    assert [r["key"] for r in s["unverified"]["closing"]["payer_ar"]] == ["closing-ar", "closing-ar-copy"]
    assert s["closing"]["credit"] == -500 and "credit" not in s["unverified"]["closing"]


def test_r4_two_separate_valid_positions_still_sum(clinic):
    t, bindings = _open(clinic)
    line = dict(book="legacy", claim="C10", line="01")

    def second_line(rows):
        for kind, role, at in (("control", "control", "cut"), ("opening", "legacy", "cut"), ("closing", "control", "end")):
            rows[role].append(dict(key=f"{kind}-c10", evidence=f"ev-{kind}-c10", effective=_z(AT[at]), seen=_z(AT[at]),
                                   revision=1, data=dict(kind=kind, line=line, bucket="payer_ar", minor=5000)))
        rows["operations"].append(dict(key="owner-c10", evidence="ev-owner-c10", effective=_z(AT["cut"]),
                                       seen=_z(AT["cut"]), revision=1,
                                       data=dict(kind="assignment", line=line, owner="bill-team",
                                                 accepted_ref="accept-c10", next_action="monitor")))
    _deliver(t, bindings, change=second_line)
    a = _assess(t)
    assert a["status"] == "SAMPLE_REVIEWABLE", a["issues"]
    assert a["summary"]["closing"]["payer_ar"] == 35000 and a["summary"]["unverified"] == {}


def _two_workers(target, args_a, args_b, patch_lock, monkeypatch):
    """Run target(*args) in two threads whose transactions both start before either takes the lock."""
    import threading
    both_ready = threading.Barrier(2, timeout=30)
    real_lock = svc._lock
    first = set()
    guard = threading.Lock()

    def lock_after_both(db, tr):
        me = threading.get_ident()
        with guard:
            wait = me not in first
            first.add(me)
        if wait:
            both_ready.wait()
        return real_lock(db, tr)
    if patch_lock:
        monkeypatch.setattr(svc, "_lock", lock_after_both)
    results = {}

    def run(name, args):
        try:
            results[name] = ("ok", target(*args))
        except svc.BridgeError as e:
            results[name] = ("conflict", e)
    threads = [threading.Thread(target=run, args=("a", args_a)), threading.Thread(target=run, args=("b", args_b))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(90)
    return results


def test_r5_two_reviewers_accepting_the_same_open_task(clinic, monkeypatch):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    item_id = _items(_assess(t))[("WORK_DENIAL", "den1")]["id"]

    def accept(reviewer):
        db = SessionLocal()
        try:
            w = db.get(m.LegacyWorkItemModel, item_id)         # both read the OPEN copy first
            assert w.state == "OPEN"
            w = svc.accept_work_item(db, w, reviewer, None)
            return w.accepted_by
        except svc.BridgeError:
            db.rollback()
            raise
        finally:
            db.close()
    results = _two_workers(accept, ("reviewer-a@clinic.test",), ("reviewer-b@clinic.test",), True, monkeypatch)
    assert sorted(kind for kind, _ in results.values()) == ["conflict", "ok"], results
    winner = next(v for kind, v in results.values() if kind == "ok")
    err = next(v for kind, v in results.values() if kind == "conflict")
    assert err.status == 409 and err.detail["accepted_by"] == winner
    db = SessionLocal()
    w = db.get(m.LegacyWorkItemModel, item_id)
    assert w.state == "ACCEPTED" and w.accepted_by == winner
    assert db.query(m.LegacyWorkItemEventModel).filter(m.LegacyWorkItemEventModel.work_item_id == item_id,
                                                       m.LegacyWorkItemEventModel.event == "ACCEPTED").count() == 1
    assert db.query(AuditLogModel).filter(AuditLogModel.action == "LEGACY_WORK_ITEM_ACCEPTED",
                                          AuditLogModel.correlation_id == f"{t['id']}:{item_id}").count() == 1
    db.close()


def test_r5_an_old_acceptance_cannot_overwrite_a_resolution(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    _deliver(t, bindings, skip=("bank",))
    item_id = _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]["id"]
    stale = SessionLocal()
    w = stale.get(m.LegacyWorkItemModel, item_id)
    assert w.state == "OPEN"                                    # a reviewer's screen still shows OPEN
    _page(t, bindings, "bank", "bank-1", None, rows["bank"], rows["bank"], AT["end"])
    assert _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]["state"] == "RESOLVED"
    with pytest.raises(svc.BridgeError) as refused:
        svc.accept_work_item(stale, w, "late-reviewer@clinic.test", None)
    stale.close()
    assert refused.value.status == 409 and refused.value.detail["state"] == "RESOLVED"
    db = SessionLocal()
    assert db.get(m.LegacyWorkItemModel, item_id).state == "RESOLVED"
    assert db.query(m.LegacyWorkItemEventModel).filter(m.LegacyWorkItemEventModel.work_item_id == item_id,
                                                       m.LegacyWorkItemEventModel.event == "ACCEPTED").count() == 0
    db.close()


def test_r6_two_identical_publications_at_once_return_the_same_assessment(clinic, monkeypatch):
    t, bindings = _open(clinic)
    _deliver(t, bindings)

    def publish(actor):
        db = SessionLocal()
        try:
            return svc.publish_assessment(db, db.get(m.LegacyTransitionModel, t["id"]), actor).id
        finally:
            db.close()
    results = _two_workers(publish, ("worker-a",), ("worker-b",), True, monkeypatch)
    assert [kind for kind, _ in results.values()] == ["ok", "ok"], results
    ids = {v for _, v in results.values()}
    assert len(ids) == 1
    db = SessionLocal()
    t_row = db.get(m.LegacyTransitionModel, t["id"])
    assert t_row.current_assessment_id in ids
    assert db.query(m.LegacyAssessmentModel).filter(m.LegacyAssessmentModel.transition_id == t["id"]).count() == 1
    items = db.query(m.LegacyWorkItemModel).filter(m.LegacyWorkItemModel.transition_id == t["id"]).count()
    db.close()
    assert items == 2 and _events(t["id"]) == 2                # one OPENED event per work item, once


def test_r6_an_older_concurrent_publication_never_replaces_a_newer_cutoff(clinic, monkeypatch):
    t, bindings = _open(clinic)
    _, rows = _deliver(t, bindings)
    end2 = AT["end"] + timedelta(hours=12)
    closing2 = _closing_set("closing2", end2, {"ar": 30000, "credit": -500, "watch": 0, "patient": 0})
    _new_cutoff(t, bindings, rows, end2, 2, extra={"control": closing2}, closing=closing2)
    newer = _assess(t)
    assert newer["as_of"] == _z(end2)
    # A publication that evaluated the evidence before the newer cutoff arrived commits afterwards.
    real = svc._evaluate
    monkeypatch.setattr(svc, "_evaluate", lambda db, tr, actor, as_of, now: real(db, tr, actor, AT["end"], now))
    db = SessionLocal()
    older = svc.publish_assessment(db, db.get(m.LegacyTransitionModel, t["id"]), "late-worker")
    assert older.as_of == AT["end"]
    db.close()
    monkeypatch.setattr(svc, "_evaluate", real)
    current = _read(t)
    assert current["assessment_id"] == newer["assessment_id"] and current["as_of"] == _z(end2)


def test_r6_overlapping_publications_for_different_cutoffs_leave_the_newest_current(clinic, monkeypatch):
    t, bindings = _open(clinic)
    _, rows = _deliver(t, bindings)
    _assess(t)
    end2 = AT["end"] + timedelta(hours=12)
    closing2 = _closing_set("closing2", end2, {"ar": 30000, "credit": -500, "watch": 0, "patient": 0})
    _new_cutoff(t, bindings, rows, end2, 2, extra={"control": closing2}, closing=closing2)
    real = svc._evaluate

    def publish(as_of):
        db = SessionLocal()
        try:
            return svc.publish_assessment(db, db.get(m.LegacyTransitionModel, t["id"]), f"worker-{as_of}").as_of
        finally:
            db.close()

    import threading
    cutoff = threading.local()

    def evaluate_at(db, tr, actor, as_of, now):
        return real(db, tr, actor, getattr(cutoff, "value", None), now)
    monkeypatch.setattr(svc, "_evaluate", evaluate_at)

    def publish_at(at):
        cutoff.value = at
        return publish(at)
    results = _two_workers(publish_at, (AT["end"],), (end2,), True, monkeypatch)
    assert [kind for kind, _ in results.values()] == ["ok", "ok"], results
    monkeypatch.setattr(svc, "_evaluate", real)
    assert _read(t)["as_of"] == _z(end2)


# ---------------------------------------------------------------- working screen: evidence, work actions, history

def _evidence(t, figure, **params):
    r = client.get(f"{API}/transitions/{t['id']}/evidence", params={"figure": figure, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_every_figure_traces_to_its_stored_records(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    a = _assess(t)
    closing = _evidence(t, "balance:closing:payer_ar")
    assert closing["total"] == a["summary"]["closing"]["payer_ar"] == 30000
    [row] = closing["records"]
    assert row["key"] == "closing-ar" and row["status"] == "counted" and row["source"] == "control"
    assert row["received_by"] and row["custody_evidence_id"].startswith("evd_") and row["page_token"] == "control-1"
    assert row["fields"] == {"line": "legacy/C7/01", "bucket": "payer_ar", "minor": 30000}
    confirmed = _evidence(t, "cash:confirmed")
    assert confirmed["total"] == a["summary"]["cash"]["bank_confirmed"] == 90000
    assert [(r["key"], r["source"]) for r in confirmed["records"]] == [("bank-1", "bank"), ("advice-1", "payer")]
    assert _evidence(t, "cash:ledger_posted")["total"] == a["summary"]["cash"]["ledger_posted"]
    route = _evidence(t, "route:archive_restore")
    assert [(r["key"], r["status"]) for r in route["records"]] == [("archive_restore", "proven")]
    denial = _evidence(t, "denial:den1")
    assert [r["status"] for r in denial["records"]] == ["denial", "line owner"]
    bank = _evidence(t, "source:bank")
    assert [r["key"] for r in bank["records"]] == ["bank-1"] and bank["seals"][0]["applies"] is True
    # Source system names never leave the server.
    assert "connector" not in json.dumps([closing, confirmed, route, denial, bank]) and "src-" not in json.dumps(bank)
    assert client.get(f"{API}/transitions/{t['id']}/evidence", params={"figure": "balance:x:y"}).status_code == 422
    _as(_user(clinic + "-OTHER", org="ORG-OTHER"))
    assert client.get(f"{API}/transitions/{t['id']}/evidence", params={"figure": "cash:observed"}).status_code == 404


def test_evidence_shows_why_cash_is_unmatched_and_a_position_is_unverified(clinic):
    t, bindings = _open(clinic)

    def both(rows):
        twin = copy.deepcopy(next(r for r in rows["payer"] if r["key"] == "advice-1"))
        twin.update(key="advice-2", evidence="ev-advice-2")
        rows["payer"].append(twin)
        dup = copy.deepcopy(next(r for r in rows["control"] if r["key"] == "closing-ar"))
        dup.update(key="closing-ar-copy", evidence="ev-closing-ar-copy")
        rows["control"].append(dup)
    _deliver(t, bindings, change=both)
    _assess(t)
    unmatched = _evidence(t, "cash:unmatched")
    assert unmatched["total"] == 90000
    assert unmatched["records"][0] == {**unmatched["records"][0], "key": "bank-1", "status": "2 advices on this trace"}
    assert {r["key"] for r in unmatched["records"][1:]} == {"advice-1", "advice-2"}
    closing = _evidence(t, "balance:closing:payer_ar")
    assert closing["total"] == 0 and {r["status"] for r in closing["records"]} == {"unverified"}
    issue = _evidence(t, "issue:AMBIGUOUS_PAYMENT_REFERENCE:pay-9")
    assert {r["key"] for r in issue["records"]} == {"bank-1", "advice-1", "advice-2"}


def test_an_earlier_assessment_drills_into_its_own_records_and_compares_with_the_current(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    _deliver(t, bindings, skip=("bank",))
    held = _assess(t)
    _page(t, bindings, "bank", "bank-1", None, rows["bank"], rows["bank"], AT["end"])
    current = _assess(t)
    old = client.get(f"{API}/transitions/{t['id']}/assessments/{held['assessment_id']}").json()
    assert old["status"] == "HOLD" and old["is_current"] is False and old["work_items"] == []
    assert _evidence(t, "cash:observed", assessment_id=held["assessment_id"])["records"] == []
    assert [r["key"] for r in _evidence(t, "cash:observed")["records"]] == ["bank-1"]
    diff = client.get(f"{API}/transitions/{t['id']}/assessments/{held['assessment_id']}/compare").json()
    assert diff["from"]["status"] == "HOLD" and diff["to"]["status"] == "SAMPLE_REVIEWABLE"
    assert diff["to"]["assessment_id"] == current["assessment_id"]
    assert {"BANK_PROOF_INCOMPLETE", "COVERAGE_UNPROVEN"} <= {i["code"] for i in diff["issues_removed"]}
    assert diff["issues_added"] == []
    assert {"figure": "cash:bank_confirmed", "from": None, "to": 90000} in diff["figures_changed"]
    assert client.get(f"{API}/transitions/{t['id']}/assessments/LCA-NOTHERE").status_code == 404


def test_work_item_actions_assign_note_release_with_timeline_and_audit(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings, skip=("bank",))
    task = _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]
    url = f"{API}/work-items/{task['id']}"
    detail = client.get(url).json()
    assert {r["key"] for r in detail["evidence"]} >= {"advice-1"}
    assert [e["event"] for e in detail["history"]] == ["OPENED"]
    teams = {a["value"] for a in client.get(f"{API}/transitions/{t['id']}/assignees").json() if a["kind"] == "team"}
    assert {"finance-reconciliation", "revenue-cycle", "bill-team"} <= teams
    assert client.post(f"{url}/assign", json={"assignee": "someone-else"}).status_code == 422
    r = client.post(f"{url}/assign", json={"assignee": "revenue-cycle", "note": "Payer statement needed"})
    assert r.status_code == 200 and r.json()["assigned_to"] == "revenue-cycle"
    assert client.post(f"{url}/notes", json={"note": "  "}).status_code == 422
    assert client.post(f"{url}/notes", json={"note": "Called the bank; statement due Friday"}).status_code == 200
    assert client.post(f"{url}/release", json={}).status_code == 409          # not accepted yet
    assert client.post(f"{url}/accept", json={}).status_code == 200
    _as(_user(clinic, role=UserRole.CLINIC_MANAGER.value))
    assert client.post(f"{url}/release", json={}).status_code == 403          # someone else's acceptance
    _as(_user(clinic, role=UserRole.READ_ONLY_AUDITOR.value))
    assert client.post(f"{url}/assign", json={"assignee": "revenue-cycle"}).status_code == 403
    assert client.get(url).status_code == 200
    _as(_user(clinic))
    released = client.post(f"{url}/release", json={"note": "Back to the queue"}).json()
    assert released["state"] == "OPEN" and released["accepted_by"] is None
    assert [e["event"] for e in released["history"]] == ["OPENED", "ASSIGNED", "NOTE", "ACCEPTED", "RELEASED"]
    assert released["history"][2]["note"] == "Called the bank; statement due Friday"
    # A later publication keeps the reviewer's assignment.
    _assess(t)
    assert client.get(url).json()["assigned_to"] == "revenue-cycle"
    db = SessionLocal()
    actions = {a for (a,) in db.query(AuditLogModel.action).filter(
        AuditLogModel.correlation_id == f"{t['id']}:{task['id']}").all()}
    db.close()
    assert {"LEGACY_WORK_ITEM_ASSIGNED", "LEGACY_WORK_ITEM_NOTE", "LEGACY_WORK_ITEM_ACCEPTED",
            "LEGACY_WORK_ITEM_RELEASED"} <= actions


def test_the_pulse_changes_with_evidence_and_work_but_not_with_reads(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    pulse = lambda: client.get(f"{API}/transitions/{t['id']}/pulse").json()  # noqa: E731
    _deliver(t, bindings, skip=("bank",))
    held = _assess(t)
    p1 = pulse()
    _read(t)
    _evidence(t, "cash:observed")
    assert pulse() == p1 and p1["current_assessment_id"] == held["assessment_id"]
    task = _items(held)[("RECONCILE_BANK_EVIDENCE", "bank")]
    client.post(f"{API}/work-items/{task['id']}/notes", json={"note": "Checking"})
    p2 = pulse()
    assert p2["last_work_event"] != p1["last_work_event"]
    _page(t, bindings, "bank", "bank-1", None, rows["bank"], rows["bank"], AT["end"])
    _assess(t)
    p3 = pulse()
    assert p3["current_assessment_id"] != p2["current_assessment_id"] and p3["last_received_at"] is not None


# ---------------------------------------------------------------- synthetic run-off, one step per delivery

def test_synthetic_run_off_moves_step_by_step_through_the_intake_and_rules(clinic, monkeypatch):
    from app.services.revenue_overview import clinic_offset
    offset = clinic_offset()
    local_midnight = (datetime(2026, 11, 25, 12, 0) + offset).replace(hour=0, minute=0) - offset
    opened = local_midnight + timedelta(hours=1)          # the transition is opened at 01:00 clinic time
    monkeypatch.setattr(svc, "_utcnow", lambda: opened)
    db = SessionLocal()
    tid = svc.deliver_synthetic(db, clinic, now=opened)
    t = db.get(m.LegacyTransitionModel, tid)
    assert svc.current_assessment(db, t).status == "SAMPLE_REVIEWABLE"
    expected = [  # step: status, closing payer receivable, credit, confirmed cash, open work codes
        ("SAMPLE_REVIEWABLE", 30000, -500, 90000, {"OBTAIN_RECEIPT", "REVIEW_PROVIDER_ADJUSTMENT"}),
        ("SAMPLE_REVIEWABLE", 30000, -500, 90000, {"AWAIT_DECISION", "REVIEW_PROVIDER_ADJUSTMENT"}),
        ("SAMPLE_REVIEWABLE", 20000, -500, 100000, {"AWAIT_DECISION", "REVIEW_PROVIDER_ADJUSTMENT"}),
        ("HOLD", 5000, -500, 100000, {"AWAIT_DECISION", "REVIEW_PROVIDER_ADJUSTMENT", "RECONCILE_BANK_EVIDENCE"}),
        ("SAMPLE_REVIEWABLE", 5000, -500, 115000, {"AWAIT_DECISION", "REVIEW_PROVIDER_ADJUSTMENT"}),
        ("SAMPLE_REVIEWABLE", 5000, -500, 115000, {"VERIFY_FINANCIAL_OUTCOME", "REVIEW_PROVIDER_ADJUSTMENT"}),
        ("SAMPLE_REVIEWABLE", 0, -500, 120000, {"VERIFY_FINANCIAL_OUTCOME", "REVIEW_PROVIDER_ADJUSTMENT"}),
        ("SAMPLE_REVIEWABLE", 0, 0, 120000, {"VERIFY_FINANCIAL_OUTCOME", "REVIEW_PROVIDER_ADJUSTMENT"}),
    ]
    step = timedelta(minutes=svc.synthetic_step_minutes())
    for k, (status, payer_ar, credit, confirmed, work) in enumerate(expected, start=1):
        now = opened + k * step + timedelta(seconds=5)
        svc.deliver_synthetic(db, clinic, now=now)
        db.expire_all()
        t = db.get(m.LegacyTransitionModel, tid)
        a = svc.current_assessment(db, t)
        s = json.loads(a.summary_json)
        issues = [(i["code"], i["item"]) for i in json.loads(a.issues_json)]
        assert a.status == status, (k, issues)
        assert (s["closing"]["payer_ar"], s["closing"]["credit"], s["cash"]["bank_confirmed"]) == (payer_ar, credit, confirmed), k
        assert {w.code for w in svc.work_items(db, t) if w.state != "RESOLVED"} == work, k
        if status == "HOLD":
            assert issues == [("BANK_PROOF_INCOMPLETE", "advice-3")]
    assert len(svc.assessment_history(db, t, 50)) == 9
    # Delivering again at the same time adds nothing.
    pages = db.query(m.LegacyPageModel).filter(m.LegacyPageModel.transition_id == tid).count()
    svc.deliver_synthetic(db, clinic, now=opened + 8 * step + timedelta(seconds=5))
    assert db.query(m.LegacyPageModel).filter(m.LegacyPageModel.transition_id == tid).count() == pages == 72
    db.close()


# ---------------------------------------------------------------- setup, CSV upload, exit sign-offs

def test_an_administrator_sets_up_sources_and_teams_from_the_screen(clinic):
    r = client.post(f"{API}/transitions", content=json.dumps({
        "facility": clinic, "payee": clinic, "payer": "TPA-NEW", "currency": "AED", "cutover": "2026-11-01T00:00:00Z",
        "label": "Run-off from the old platform", **scenario.TRANSITION}))
    assert r.status_code == 201, r.text
    t = r.json()
    roles = client.get(f"{API}/transitions/{t['id']}/roles").json()
    assert [x["role"] for x in roles] == list(scenario.ROLES) and all(x["sources"] == [] for x in roles)
    b = client.post(f"{API}/transitions/{t['id']}/sources", content=json.dumps({
        "role": "bank", "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-06-01T00:00:00Z"}))
    assert b.status_code == 201, b.text
    again = client.post(f"{API}/transitions/{t['id']}/sources", content=json.dumps({
        "role": "bank", "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-06-01T00:00:00Z"}))
    assert again.status_code == 201                        # a second bank source gets its own name
    bank = next(x for x in client.get(f"{API}/transitions/{t['id']}/roles").json() if x["role"] == "bank")
    assert len(bank["sources"]) == 2 and "connector" not in json.dumps(bank)
    db = SessionLocal()
    names = {c for (c,) in db.query(m.LegacySourceBindingModel.connector).filter(
        m.LegacySourceBindingModel.transition_id == t["id"]).all()}
    db.close()
    assert names == {"clinic-bank", "clinic-bank-2"}
    _as(_user(clinic, role=UserRole.CLINIC_MANAGER.value))
    assert client.put(f"{API}/transitions/{t['id']}/owners", content=json.dumps(
        {"hold_owners": {"PROVE_ROUTES": "it-desk"}})).status_code == 403
    _as(_user(clinic))
    view = client.put(f"{API}/transitions/{t['id']}/owners", content=json.dumps(
        {"hold_owners": {"PROVE_ROUTES": "it-desk"}})).json()
    assert view["hold_owners"]["PROVE_ROUTES"] == "it-desk"
    assert view["hold_owners"]["RECONCILE_BANK_EVIDENCE"] == "finance-reconciliation"
    assert client.put(f"{API}/transitions/{t['id']}/owners", content=json.dumps(
        {"hold_owners": {"NOT_A_TASK": "x"}})).status_code == 422


def test_csv_templates_and_parsing(clinic):
    from app.legacy_bridge import csv_intake
    r = client.get(f"{API}/templates/bank")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0] == "key,kind,effective,seen,evidence,trace,amount"
    assert client.get(f"{API}/templates/nothing").status_code == 404
    offset = timedelta(hours=4)
    advice = ("key,kind,effective,trace,net,settlement,part,part_type,part_amount,part_book,part_claim,part_line\n"
              "adv-1,advice,2026-11-04 14:00,pay-9,900.00,cash,A,claim_payment,700.00,legacy,C7,01\n"
              "adv-1,advice,2026-11-04 14:00,pay-9,900.00,cash,B,claim_payment,200.00,new,C7,01\n")
    records, problems = csv_intake.parse("payer", advice, offset)
    assert problems == [] and len(records) == 1
    rec = records[0]
    assert rec["effective"] == "2026-11-04T10:00:00Z"            # clinic local 14:00 at UTC+4
    assert rec["data"]["net"] == 90000 and [p["minor"] for p in rec["data"]["parts"]] == [70000, 20000]
    bad = ("key,kind,effective,trace,amount\n"
           "b-1,deposit,2026-11-04T10:00:00Z,pay-9,9OO\n"
           "b-2,deposit,,pay-9,10.00\n"
           "b-3,advice,2026-11-04T10:00:00Z,pay-9,10.00\n"
           "b-4,deposit,2026-11-04T10:00:00Z,pay-9,10.005\n")
    _records, problems = csv_intake.parse("bank", bad, offset)
    assert [(p["row"], p["reason"]) for p in problems] == [
        (2, "amount must be an amount such as 900.00"), (3, "effective is required"),
        (4, "a bank source cannot deliver 'advice' records"), (5, "amount has more than two decimals")]


def test_a_csv_bank_export_completes_the_evidence_and_the_transition_becomes_reviewable(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings, skip=("bank",))
    held = _assess(t)
    assert held["status"] == "HOLD"
    csv_text = f"key,kind,effective,trace,amount\nbank-1,deposit,{_z(AT['day'])},pay-9,900.00\n"
    url = f"{API}/transitions/{t['id']}/uploads"
    db = SessionLocal()
    before = db.query(m.LegacyRecordModel).filter(m.LegacyRecordModel.transition_id == t["id"]).count()
    preview = client.post(f"{url}/preview", json={"binding_id": bindings["bank"], "content": csv_text}).json()
    assert preview["can_upload"] is True and preview["rows"] == 1 and preview["new_records"] == 1
    assert preview["by_kind"] == {"deposit": 1} and preview["this_source_complete_through"] is None
    assert preview["latest_statement"] == _z(AT["end"])
    assert db.query(m.LegacyRecordModel).filter(m.LegacyRecordModel.transition_id == t["id"]).count() == before
    wrong = client.post(f"{url}/preview", json={"binding_id": bindings["bank"],
                                                "content": "key,kind,effective,trace,amount\nx,deposit,,pay-9,1\n"}).json()
    assert wrong["can_upload"] is False and wrong["problems"][0]["row"] == 2
    assert client.post(url, json={"binding_id": bindings["bank"], "content": "key,kind\n"}).status_code == 422
    done = client.post(url, json={"binding_id": bindings["bank"], "content": csv_text, "filename": "bank-nov.csv",
                                  "complete_through": _z(AT["end"])})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "STAGED" and done.json()["sealed_rows"] == 1
    current = _assess(t)
    assert current["status"] == "SAMPLE_REVIEWABLE", current["issues"]
    assert current["summary"]["cash"]["bank_confirmed"] == 90000
    custody = db.query(EvidenceModel).filter(EvidenceModel.clinic_id == clinic,
                                             EvidenceModel.external_record_id == done.json()["token"]).one()
    attested = json.loads(custody.metadata_json)["attestation"]
    assert attested == {"file": "bank-nov.csv", "uploaded_by": _user(clinic).email, "complete_through": _z(AT["end"])}
    # The same file again is recognised; nothing new is stored.
    again = client.post(url, json={"binding_id": bindings["bank"], "content": csv_text}).json()
    assert again["status"] == "STAGED" and again["records_added"] == 0
    db.close()
    _as(_user(clinic, role=UserRole.READ_ONLY_AUDITOR.value))
    assert client.post(f"{url}/preview", json={"binding_id": bindings["bank"], "content": csv_text}).status_code == 403


def test_named_signoffs_from_the_screen_complete_the_exit_review(clinic):
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    _assess(t)
    inventory = scenario.exit_inventory(t["scope"], CUTOVER.replace(tzinfo=None), "old-billing")
    inventory["signoffs"] = {"finance": "decision-finance"}
    inv_id = client.post(f"{API}/exit-inventories", content=json.dumps(inventory)).json()["inventory_id"]
    review = client.get(f"{API}/exit-inventories/{inv_id}/review").json()
    assert review["status"] == "HOLD" and review["missing_signoffs"] == ["revenue_cycle", "records", "contract_owner"]
    url = f"{API}/exit-inventories/{inv_id}/signoffs"
    assert client.post(url, json={"role": "finance"}).status_code == 409          # signed in the inventory
    assert client.post(url, json={"role": "ceo"}).status_code == 422
    for role in ("revenue_cycle", "records"):
        assert client.post(url, json={"role": role, "note": "Reviewed"}).status_code == 200
    assert client.post(url, json={"role": "records"}).status_code == 409
    _as(_user(clinic, role=UserRole.READ_ONLY_AUDITOR.value))
    assert client.post(url, json={"role": "contract_owner"}).status_code == 403
    _as(_user(clinic, role=UserRole.CLINIC_MANAGER.value))
    final = client.post(url, json={"role": "contract_owner"}).json()
    assert final["status"] == "HUMAN_EXIT_REVIEW" and final["missing_signoffs"] == []
    details = {d["role"]: d for d in final["signoff_details"]}
    assert details["finance"]["given_on_screen"] is False
    assert details["contract_owner"]["signed_by"] == _user(clinic, role=UserRole.CLINIC_MANAGER.value).email
    db = SessionLocal()
    assert db.query(AuditLogModel).filter(AuditLogModel.action == "LEGACY_EXIT_SIGNOFF",
                                          AuditLogModel.correlation_id.like(f"{inv_id}:%")).count() == 3
    db.query(m.LegacyExitSignoffModel).filter(m.LegacyExitSignoffModel.inventory_id == inv_id).delete()
    db.commit()
    db.close()


def test_a_reviewer_upload_to_a_synthetic_source_never_blocks_the_run_off(clinic, monkeypatch):
    from app.services.revenue_overview import clinic_offset
    offset = clinic_offset()
    opened = (datetime(2026, 11, 25, 12, 0) + offset).replace(hour=0, minute=0) - offset + timedelta(hours=1)
    monkeypatch.setattr(svc, "_utcnow", lambda: opened)
    db = SessionLocal()
    tid = svc.deliver_synthetic(db, clinic, now=opened)
    step = timedelta(minutes=svc.synthetic_step_minutes())
    svc.deliver_synthetic(db, clinic, now=opened + step + timedelta(seconds=5))
    t = db.get(m.LegacyTransitionModel, tid)
    bank = db.query(m.LegacySourceBindingModel).filter(m.LegacySourceBindingModel.transition_id == tid,
                                                       m.LegacySourceBindingModel.role == "bank").one()
    from app.legacy_bridge.intake import PageIn
    extra = {"key": "bank-77", "evidence": "ev-bank-77", "effective": iso_z(opened), "seen": iso_z(opened),
             "revision": 1, "data": {"kind": "deposit", "trace": "pay-77", "minor": 5000}}
    staged = svc.stage_page(db, t, PageIn.model_validate_json(json.dumps(
        {"binding_id": bank.id, "previous": "synthetic-bank-1", "token": "reviewer-upload-1", "records": [extra]})),
        "reviewer@clinic.test", opened + step + timedelta(seconds=10))
    assert staged["status"] == "STAGED"
    svc.deliver_synthetic(db, clinic, now=opened + 2 * step + timedelta(seconds=5))
    db.expire_all()
    t = db.get(m.LegacyTransitionModel, tid)
    a = svc.current_assessment(db, t)
    codes = {(i["code"], i["item"]) for i in json.loads(a.issues_json)}
    assert ("COVERAGE_UNPROVEN", "bank") not in codes                 # the run-off's seal counts the upload
    assert ("UNAPPLIED_BANK_CASH", "pay-77") in codes                  # and the rules judge it
    assert db.query(m.LegacyPageModel).filter(m.LegacyPageModel.transition_id == tid,
                                              m.LegacyPageModel.token == "synthetic-bank-3").count() == 1
    db.close()


def iso_z(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- final verification: totals, authority, concurrency

CASH_FIGURES = {"advised": "cash:advised", "bank_observed": "cash:observed", "bank_confirmed": "cash:confirmed",
                "bank_unmatched": "cash:unmatched", "unapplied_bank": "cash:unapplied",
                "ledger_posted": "cash:ledger_posted", "provider_adjustments": "cash:provider_adjustments"}


def _line(claim):
    return dict(book="legacy", claim=claim, line="01")


def _scenario_changes():
    def two_cash(rows):
        twin = copy.deepcopy(next(r for r in rows["payer"] if r["key"] == "advice-1"))
        twin.update(key="advice-2", evidence="ev-advice-2")
        rows["payer"].append(twin)

    def noncash(rows):
        rows["payer"].append(dict(key="advice-nc", evidence="ev-advice-nc", effective=_z(AT["day"]), seen=_z(AT["day"]),
                                  revision=1, data=dict(kind="advice", trace="pay-9", net=0, settlement="noncash",
                                                        parts=[dict(part="Z", type="claim_payment", minor=0, line=_line("C7"))])))

    def unapplied(rows):
        rows["bank"].append(dict(key="bank-77", evidence="ev-bank-77", effective=_z(AT["day"]), seen=_z(AT["day"]),
                                 revision=1, data=dict(kind="deposit", trace="pay-77", minor=5000)))

    def duplicate_closing(rows):
        dup = copy.deepcopy(next(r for r in rows["control"] if r["key"] == "closing-ar"))
        dup.update(key="closing-ar-copy", evidence="ev-closing-ar-copy")
        rows["control"].append(dup)

    def writeoff_and_transfer(rows):
        at = _z(AT["day"])
        rec = lambda key, role, kind, **body: rows[role].append(  # noqa: E731
            dict(key=key, evidence="ev-" + key, effective=at, seen=at, revision=1, data=dict(kind=kind, **body)))
        rec("approval-wo", "operations", "approval", line=_line("C7"), bucket="payer_ar", minor=-2000,
            reason="approved_adjustment")
        rec("delta-wo", "ledger", "movement", line=_line("C7"), bucket="payer_ar", minor=-2000,
            reason="approved_adjustment", link="approval-wo")
        rec("delta-tr-out", "ledger", "movement", line=_line("C7"), bucket="payer_ar", minor=-5000, reason="transfer",
            link="transfer-1")
        rec("delta-tr-in", "ledger", "movement", line=_line("C7"), bucket="patient_ar", minor=5000, reason="transfer",
            link="transfer-1")
        for r in rows["control"]:
            if r["key"] == "closing-ar":
                r["data"]["minor"] = 23000
            if r["key"] == "closing-patient":
                r["data"]["minor"] = 5000

    return {"clean": None, "two cash advices": two_cash, "cash and noncash advice": noncash, "missing bank": "skip-bank",
            "unapplied deposit": unapplied, "duplicate closing row": duplicate_closing,
            "write-off and transfer": writeoff_and_transfer}


def test_every_screen_figure_equals_the_total_of_its_evidence_in_every_case(clinic):
    results = {}
    for n, (name, change) in enumerate(_scenario_changes().items()):
        sub = f"{clinic}-{n}"
        _as(_user(sub))
        t, bindings = _open(sub)
        _deliver(t, bindings, skip=("bank",) if change == "skip-bank" else (),
                 change=None if change in (None, "skip-bank") else change)
        a = _assess(t)
        s = a["summary"]
        for key, figure in CASH_FIGURES.items():
            total = _evidence(t, figure)["total"]
            assert total == (s["cash"][key] if s["cash"][key] is not None else 0), (name, key, total, s["cash"][key])
        for kind in ("control", "opening", "closing"):
            for bucket, value in s[kind].items():
                assert _evidence(t, f"balance:{kind}:{bucket}")["total"] == value, (name, kind, bucket)
        results[name] = a
    # A write-off and a payer-to-patient transfer change who owes, never the cash.
    clean, moved = results["clean"]["summary"], results["write-off and transfer"]
    assert moved["status"] == "SAMPLE_REVIEWABLE", moved["issues"]
    assert moved["summary"]["cash"] == clean["cash"]
    assert (moved["summary"]["closing"]["payer_ar"], moved["summary"]["closing"]["patient_ar"]) == (23000, 5000)


def test_an_internal_dispatch_cannot_certify_a_payer_receipt(clinic):
    t, bindings = _open(clinic)
    receipt = dict(key="appeal-received", evidence="ev-appeal-received", effective=_z(AT["day"]), seen=_z(AT["day"]),
                   revision=1, data=dict(kind="appeal", denial="den1", stage="received", receipt="r-1"))
    r = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(
        {"binding_id": bindings["operations"], "records": [receipt]}))
    assert r.status_code == 422
    assert "cannot deliver appeal" in json.dumps(r.json())
    ok = client.post(f"{API}/transitions/{t['id']}/pages", content=json.dumps(
        {"binding_id": bindings["payer"], "records": [receipt]}))
    assert ok.status_code == 200 and ok.json()["status"] == "STAGED"


def test_unknown_deadlines_and_failed_routes_are_priority_work_and_the_response_names_its_evidence(clinic):
    t, bindings = _open(clinic)

    def no_rule_and_no_probe(rows):
        den = next(r for r in rows["payer"] if r["key"] == "den1")
        den["data"]["rule"] = None
        den["data"]["due"] = None
        rows["operations"] = [r for r in rows["operations"] if r["key"] != "fallback_recovery"]
    _deliver(t, bindings, change=no_rule_and_no_probe)
    a = _assess(t)
    items = _items(a)
    assert items[("VERIFY_DENIAL_DEADLINES", "denials")]["priority"] == "high"
    assert items[("PROVE_ROUTES", "routes")]["priority"] == "high"
    assert items[("REVIEW_PROVIDER_ADJUSTMENT", "offset-18")]["priority"] == "normal"
    open_items = [w for w in a["work_items"] if w["state"] != "RESOLVED"]
    assert [w["priority"] for w in open_items][:2] == ["high", "high"]          # priority work first
    assert len(a["evidence_ids"]) == a["evidence_count"] and "ev-bank-1" in a["evidence_ids"]
    ids = a["summary"]["control_ids"]
    assert ids["control"] == ["control-ar", "control-credit", "control-patient", "control-watch"]
    assert ids["closing"] == ["closing-ar", "closing-credit", "closing-patient", "closing-watch"]
    assert a["summary"]["snapshot_at"] == _z(AT["end"])


def test_two_reviewers_signing_the_same_role_at_once(clinic, monkeypatch):
    import threading
    t, bindings = _open(clinic)
    _deliver(t, bindings)
    _assess(t)
    inventory = scenario.exit_inventory(t["scope"], CUTOVER.replace(tzinfo=None), "old-billing")
    inventory["signoffs"] = {}
    inv_id = client.post(f"{API}/exit-inventories", content=json.dumps(inventory)).json()["inventory_id"]
    both = threading.Barrier(2, timeout=30)
    real = svc.exit_signoffs
    seen = set()
    guard = threading.Lock()

    def read_then_wait(db, row):
        out = real(db, row)
        with guard:
            first = threading.get_ident() not in seen
            seen.add(threading.get_ident())
        if first:
            both.wait()            # both have checked that nobody signed yet
        return out
    monkeypatch.setattr(svc, "exit_signoffs", read_then_wait)
    results = {}

    def sign(name):
        db = SessionLocal()
        try:
            row = db.get(m.LegacyExitInventoryModel, inv_id)
            results[name] = ("ok", svc.sign_exit(db, row, "finance", f"{name}@clinic.test").signed_by)
        except svc.BridgeError as e:
            db.rollback()
            results[name] = ("conflict", e.status)
        finally:
            db.close()
    threads = [threading.Thread(target=sign, args=(n,)) for n in ("first", "second")]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert sorted(k for k, _ in results.values()) == ["conflict", "ok"], results
    assert next(v for k, v in results.values() if k == "conflict") == 409
    db = SessionLocal()
    assert db.query(m.LegacyExitSignoffModel).filter(m.LegacyExitSignoffModel.inventory_id == inv_id).count() == 1
    assert db.query(AuditLogModel).filter(AuditLogModel.action == "LEGACY_EXIT_SIGNOFF",
                                          AuditLogModel.correlation_id == f"{inv_id}:finance").count() == 1
    db.close()


def test_the_same_inventory_delivered_twice_at_once_is_stored_once(clinic, monkeypatch):
    import threading
    t, bindings = _open(clinic)
    raw = json.dumps(scenario.exit_inventory(t["scope"], CUTOVER.replace(tzinfo=None), "old-billing")).encode()
    both = threading.Barrier(2, timeout=30)
    real_query = svc.LegacyExitInventoryModel
    results = {}
    original = svc.store_exit_inventory

    def deliver(name):
        db = SessionLocal()
        try:
            both.wait()
            results[name] = original(db, raw, org_id="ORG-T", clinic_id=clinic, actor=name).id
        finally:
            db.close()
    threads = [threading.Thread(target=deliver, args=(n,)) for n in ("a", "b")]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert len(results) == 2 and len(set(results.values())) == 1, results
    db = SessionLocal()
    assert db.query(real_query).filter(real_query.clinic_id == clinic).count() == 1
    db.close()


# ---------------------------------------------------------------- separate outcomes (INTEGRATION.md slice 4)

def _appeal_steps(rows, decided=True):
    at = lambda h: _z(AT["day"] + timedelta(hours=h))  # noqa: E731
    rows["operations"].append(dict(key="appeal-sent", evidence="ev-appeal-sent", effective=at(1), seen=at(1), revision=1,
                                   data=dict(kind="appeal", denial="den1", stage="sent", receipt="r-sent")))
    rows["payer"].append(dict(key="appeal-received", evidence="ev-appeal-received", effective=at(2), seen=at(2),
                              revision=1, data=dict(kind="appeal", denial="den1", stage="received", receipt="r-recv")))
    if decided:
        rows["payer"].append(dict(key="appeal-decided", evidence="ev-appeal-decided", effective=at(3), seen=at(3),
                                  revision=1, data=dict(kind="appeal", denial="den1", stage="decided", receipt="r-dec")))


def test_a_write_off_after_the_payers_decision_is_a_proven_separate_outcome(clinic):
    t, bindings = _open(clinic)

    def decided_then_written_off(rows):
        _appeal_steps(rows)
        at = _z(AT["day"] + timedelta(hours=4))
        rows["operations"].append(dict(key="approval-wo", evidence="ev-approval-wo", effective=at, seen=at, revision=1,
                                       data=dict(kind="approval", line=_line("C7"), bucket="payer_ar", minor=-2000,
                                                 reason="approved_adjustment")))
        rows["ledger"].append(dict(key="delta-wo", evidence="ev-delta-wo", effective=at, seen=at, revision=1,
                                   data=dict(kind="movement", line=_line("C7"), bucket="payer_ar", minor=-2000,
                                             reason="approved_adjustment", link="approval-wo")))
        next(r for r in rows["control"] if r["key"] == "closing-ar")["data"]["minor"] = 28000
    _deliver(t, bindings, change=decided_then_written_off)
    a = _assess(t)
    assert a["status"] == "SAMPLE_REVIEWABLE", a["issues"]
    task = _items(a)[("VERIFY_FINANCIAL_OUTCOME", "den1")]
    assert task["allowed_outcomes"] == ["collected", "written_off", "risk_accepted"]
    url = f"{API}/work-items/{task['id']}/outcome"
    refused = client.post(url, json={"outcome": "collected"})
    assert refused.status_code == 422 and "payer cash" in refused.json()["detail"]   # the base payment predates the decision
    done = client.post(url, json={"outcome": "written_off", "note": "Approved by finance"}).json()
    assert done["state"] == "CLOSED" and done["outcome"] == "written_off" and done["outcome_by"] == _user(clinic).email
    assert "delta-wo AED 20.00" in done["outcome_note"] and done["allowed_outcomes"] == []
    assert done["history"][-1]["event"] == "OUTCOME"
    assert client.post(url, json={"outcome": "risk_accepted", "note": "x"}).status_code == 409
    assert client.post(f"{API}/work-items/{task['id']}/accept", json={}).status_code == 409
    again = _items(_assess(t))[("VERIFY_FINANCIAL_OUTCOME", "den1")]
    assert again["state"] == "CLOSED" and again["outcome"] == "written_off"           # publication keeps it closed
    db = SessionLocal()
    assert db.query(AuditLogModel).filter(AuditLogModel.action == "LEGACY_WORK_ITEM_OUTCOME",
                                          AuditLogModel.correlation_id == f"{t['id']}:{task['id']}").count() == 1
    db.close()


def test_collection_needs_payer_cash_after_the_decision_and_comes_only_after_it(clinic, monkeypatch):
    from app.services.revenue_overview import clinic_offset
    offset = clinic_offset()
    opened = (datetime(2026, 11, 25, 12, 0) + offset).replace(hour=0, minute=0) - offset + timedelta(hours=1)
    monkeypatch.setattr(svc, "_utcnow", lambda: opened)
    db = SessionLocal()
    tid = svc.deliver_synthetic(db, clinic, now=opened)
    step = timedelta(minutes=svc.synthetic_step_minutes())
    svc.deliver_synthetic(db, clinic, now=opened + 2 * step + timedelta(seconds=5))     # appeal received, no decision
    db.close()
    t = {"id": tid}
    waiting = _items(_read(t))[("AWAIT_DECISION", "den1")]
    assert waiting["allowed_outcomes"] == ["risk_accepted"]
    assert client.post(f"{API}/work-items/{waiting['id']}/outcome", json={"outcome": "collected"}).status_code == 422
    db = SessionLocal()
    svc.deliver_synthetic(db, clinic, now=opened + 6 * step + timedelta(seconds=5))     # decided; no payment after it yet
    db.close()
    verify = _items(_read(t))[("VERIFY_FINANCIAL_OUTCOME", "den1")]
    assert client.post(f"{API}/work-items/{verify['id']}/outcome", json={"outcome": "collected"}).status_code == 422
    db = SessionLocal()
    svc.deliver_synthetic(db, clinic, now=opened + 7 * step + timedelta(seconds=5))     # the final payment on the line
    db.close()
    collected = client.post(f"{API}/work-items/{verify['id']}/outcome", json={"outcome": "collected"}).json()
    assert collected["state"] == "CLOSED" and collected["outcome"] == "collected"
    assert "delta-4A" in collected["outcome_note"] and " 50.00 on " in collected["outcome_note"]   # the deployment currency


def test_an_accepted_risk_is_an_administrators_decision_and_changes_no_finding(clinic):
    t, bindings = _open(clinic)
    rows = scenario.records_by_role(CUTOVER.replace(tzinfo=None))
    _deliver(t, bindings, skip=("bank",))
    held = _assess(t)
    task = _items(held)[("RECONCILE_BANK_EVIDENCE", "bank")]
    url = f"{API}/work-items/{task['id']}/outcome"
    assert task["allowed_outcomes"] == ["risk_accepted"]
    assert client.post(url, json={"outcome": "written_off"}).status_code == 422
    _as(_user(clinic, role=UserRole.CLINIC_MANAGER.value))
    assert client.post(url, json={"outcome": "risk_accepted", "note": "Small amount"}).status_code == 403
    _as(_user(clinic))
    assert client.post(url, json={"outcome": "risk_accepted"}).status_code == 422                 # a reason is required
    closed = client.post(url, json={"outcome": "risk_accepted", "note": "Bank statement unobtainable; AED 900 at risk"}).json()
    assert closed["state"] == "CLOSED" and closed["outcome"] == "risk_accepted"
    still = _assess(t)
    assert still["status"] == "HOLD"                                                   # the evidence is still incomplete
    assert _items(still)[("RECONCILE_BANK_EVIDENCE", "bank")]["state"] == "CLOSED"
    _page(t, bindings, "bank", "bank-1", None, rows["bank"], rows["bank"], AT["end"])
    later = _items(_assess(t))[("RECONCILE_BANK_EVIDENCE", "bank")]
    assert later["state"] == "CLOSED" and later["outcome"] == "risk_accepted"           # the decision stays on record
    # Work that later evidence clears records that as its outcome.
    resolved = [w for w in _read(t)["work_items"] if w["state"] == "RESOLVED"]
    assert all(w["outcome"] == "resolved_by_evidence" for w in resolved)
