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
