"""
PR #8 MS-001 remediation audit (commit c48511b6): one test per "Accept when" line.

A1  one encounter scope for list, revenue, export and summary (2 organizations x 2 clinics)
A2  reading never extends a session; only recorded user activity does
A3  logout ends both live sockets; a token without a server session is refused
A4  a password reset in the same second as the next sign-in leaves the new session working
A5  pending, inactive and Unicode credentials; required confirmation fields
A6  "Sync Live Data" only runs a verified connection and records every run
A7  verified / failed / unavailable / stale come from server-side provider checks
A8  a hand-typed encounter is not an import
A9  clinic ownership is reconciled from evidence (r2 upgrade on a populated fixture)
A10 historical audit scope is never taken from today's membership
plus: one settings row per scope under concurrent first opens, atomic one-time links, and no
inbox for a removed role.
"""
import importlib.util
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from starlette.websockets import WebSocketDisconnect

import app.api.v1.endpoints.auth as auth_endpoints
import app.api.v1.endpoints.reality_connectors as reality
from app.core.config import settings as app_settings
from app.core.security import create_access_token, get_password_hash
from app.db.database import SessionLocal
from app.main import app
from app.models.audit import AuditLogModel
from app.models.connector import ConnectorModel
from app.models.encounter import EncounterModel
from app.models.identity import UserSessionModel
from app.models.integration_evidence import ConnectorProbeModel, ConnectorSyncRunModel, DataImportModel
from app.models.otp import OTPModel
from app.models.settings import SettingsModel
from app.models.user import User, UserRole

client = TestClient(app)
API = "/api/v1"
PW = "Sentinel2026pass"
OUTBOX = []
ORIGIN = app_settings.ALLOWED_WS_ORIGINS[0]


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    OUTBOX.clear()

    def fake_send(to_email, subject, body, is_html=False):
        OUTBOX.append({"to": to_email, "subject": subject, "body": body})
        return True
    import app.core.email as email_mod
    import app.api.v1.endpoints.settings as settings_mod
    monkeypatch.setattr(email_mod, "send_email", fake_send)
    monkeypatch.setattr(settings_mod, "send_email", fake_send)
    yield


def _tag():
    return uuid.uuid4().hex[:8]


def _db_user(email, role, org_id=None, clinic_id=None, active=True, password=PW):
    db = SessionLocal()
    try:
        u = User(email=email, full_name=email.split("@")[0], role=role, org_id=org_id, clinic_id=clinic_id,
                 is_active=active, hashed_password=get_password_hash(password) if password else "")
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _login(email, password=PW):
    auth_endpoints._ip_rate_limits.clear()
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


def _token(email, password=PW):
    r = _login(email, password)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(email, password=PW):
    return {"Authorization": f"Bearer {_token(email, password)}"}


def _audit(action, **where):
    db = SessionLocal()
    try:
        q = db.query(AuditLogModel).filter(AuditLogModel.action == action)
        for k, v in where.items():
            q = q.filter(getattr(AuditLogModel, k) == v)
        return q.all()
    finally:
        db.close()


def _current_session(h):
    sessions = client.get(f"{API}/auth/sessions", headers=h).json()
    return next(s for s in sessions["sessions"] if s["current"])["id"], sessions["idle_timeout_minutes"]


def _session_row(sid):
    db = SessionLocal()
    try:
        return db.query(UserSessionModel).filter(UserSessionModel.id == sid).first()
    finally:
        db.close()


def _age_session(sid, minutes):
    db = SessionLocal()
    try:
        row = db.query(UserSessionModel).filter(UserSessionModel.id == sid).first()
        row.last_seen_at = datetime.utcnow() - timedelta(minutes=minutes)
        db.commit()
        return row.last_seen_at
    finally:
        db.close()


@pytest.fixture
def platform():
    email = f"platform-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.SYSTEM_ADMINISTRATOR.value)
    return _h(email)


@pytest.fixture
def two_orgs(platform):
    out = {}
    for key in ("A", "B"):
        t = _tag()
        org = client.post(f"{API}/organizations", headers=platform, json={"name": f"PR8 Org {key} {t}"})
        assert org.status_code == 201, org.text
        org_id = org.json()["id"]
        admin = f"pr8admin{key.lower()}-{t}@sentinel-test.com"
        _db_user(admin, UserRole.ORGANIZATION_ADMINISTRATOR.value, org_id=org_id)
        h = _h(admin)
        clinics = []
        for n in (1, 2):
            c = client.post(f"{API}/clinics/", headers=h, json={"name": f"PR8 Clinic {key}{n} {t}", "address": f"Street {n}"})
            assert c.status_code == 201, c.text
            clinics.append(c.json()["id"])
        out[key] = {"org": org_id, "admin": admin, "h": h, "clinics": clinics}
    return out


# ---------------------------------------------------------------- A1 encounter scope

def _add_encounters(clinic_amounts):
    """{clinic_id: [amount, ...]} as paid encounters dated today."""
    from app.services.revenue_overview import clinic_today
    today = clinic_today().isoformat()
    db = SessionLocal()
    try:
        for clinic_id, amounts in clinic_amounts.items():
            for amount in amounts:
                db.add(EncounterModel(id=f"PR8-{_tag()}", patient_id=f"P-{_tag()}", provider_id="DR-PR8",
                                      clinic_id=clinic_id, date=today, type="Consultation", status="Completed",
                                      billing_status="Paid", billing_amount=amount))
        db.commit()
    finally:
        db.close()


def _totals(h, clinic_id=None):
    params = {"period": "week"}
    if clinic_id:
        params["clinic_id"] = clinic_id
    lst = client.get(f"{API}/encounters", headers=h, params={**params, "page": 1, "page_size": 5})
    rev = client.get(f"{API}/encounters/revenue-overview", headers=h, params=params)
    exp = client.get(f"{API}/encounters/export", headers=h, params=params)
    summ = client.get(f"{API}/encounters/summary-stats", headers=h, params={"clinic_id": clinic_id} if clinic_id else {})
    for r in (lst, rev, exp, summ):
        assert r.status_code == 200, r.text
    export_rows = [line for line in exp.text.splitlines()[1:] if line.strip()]
    return {"list": int(lst.headers["X-Total-Count"]), "revenue_claims": rev.json()["total_claims"],
            "revenue_charges": rev.json()["cards"]["charges"]["value"], "export": len(export_rows),
            "summary": summ.json()["total_encounters"]}


def test_a1_every_encounter_view_uses_one_scope(two_orgs):
    a, b = two_orgs["A"], two_orgs["B"]
    _add_encounters({a["clinics"][0]: [100.0, 50.0], a["clinics"][1]: [25.0],
                     b["clinics"][0]: [999.0, 999.0, 999.0], b["clinics"][1]: [1.0]})
    whole = _totals(a["h"])
    assert whole == {"list": 3, "revenue_claims": 3, "revenue_charges": 175.0, "export": 3, "summary": 3}
    one = _totals(a["h"], a["clinics"][1])
    assert one == {"list": 1, "revenue_claims": 1, "revenue_charges": 25.0, "export": 1, "summary": 1}
    # a clinic of another organization is not found, in every view
    for path in ("encounters", "encounters/revenue-overview", "encounters/export", "encounters/summary-stats"):
        assert client.get(f"{API}/{path}", headers=a["h"], params={"clinic_id": b["clinics"][0]}).status_code == 404
    # a clinic-bound role sees only its own clinic
    staff = f"pr8staff-{_tag()}@sentinel-test.com"
    _db_user(staff, UserRole.PRACTICE_MANAGER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    hs = _h(staff)
    assert _totals(hs) == {"list": 2, "revenue_claims": 2, "revenue_charges": 150.0, "export": 2, "summary": 2}
    assert client.get(f"{API}/encounters/summary-stats", headers=hs,
                      params={"clinic_id": a["clinics"][1]}).status_code == 404


def test_a1_create_update_and_journey_stay_in_scope(two_orgs):
    a, b = two_orgs["A"], two_orgs["B"]
    body = {"id": f"PR8-{_tag()}", "patient_name": "Test Patient", "provider_name": "Dr Test",
            "clinic_id": b["clinics"][0], "date": datetime.utcnow().date().isoformat(), "type": "Consultation"}
    assert client.post(f"{API}/encounters", headers=a["h"], json=body).status_code == 404
    _add_encounters({b["clinics"][0]: [10.0]})
    db = SessionLocal()
    try:
        enc_id = db.query(EncounterModel.id).filter(EncounterModel.clinic_id == b["clinics"][0]).first()[0]
    finally:
        db.close()
    assert client.get(f"{API}/encounters/{enc_id}/journey", headers=a["h"]).status_code == 404
    assert client.put(f"{API}/encounters/{enc_id}", headers=a["h"], json={"diagnosis": "Changed"}).status_code == 404
    assert client.delete(f"{API}/encounters/{enc_id}", headers=a["h"]).status_code == 404
    assert client.get(f"{API}/encounters/{enc_id}/journey", headers=b["h"]).status_code == 200


# ---------------------------------------------------------------- A2 activity, not polling

def test_a2_polling_never_extends_a_session_and_activity_does(two_orgs):
    h = two_orgs["A"]["h"]
    sid, idle = _current_session(h)
    aged = _age_session(sid, 2)
    for path in ("auth/me", "notifications", "encounters/summary-stats", "auth/sessions"):
        assert client.get(f"{API}/{path}", headers=h).status_code == 200
    assert _session_row(sid).last_seen_at == aged          # reads left the timestamp where it was
    r = client.post(f"{API}/auth/activity", headers=h)
    assert r.status_code == 200 and r.json()["recorded"] is True and r.json()["idle_timeout_minutes"] == idle
    moved = _session_row(sid).last_seen_at
    assert moved > aged + timedelta(minutes=1)
    # a second report within a minute writes nothing
    again = client.post(f"{API}/auth/activity", headers=h).json()
    assert again["recorded"] is False and _session_row(sid).last_seen_at == moved


def test_a2_an_unattended_screen_that_keeps_polling_still_times_out(two_orgs):
    a = two_orgs["A"]
    sid, idle = _current_session(a["h"])
    _age_session(sid, idle - 1)
    assert client.get(f"{API}/notifications", headers=a["h"]).status_code == 200   # a poll inside the limit
    _age_session(sid, idle + 1)
    r = client.get(f"{API}/notifications", headers=a["h"])
    assert r.status_code == 401 and "idle_timeout" in r.json()["detail"]
    row = _session_row(sid)
    assert row.ended_at is not None and row.end_reason == "idle_timeout"
    assert client.post(f"{API}/auth/activity", headers=a["h"]).status_code == 401   # too late to revive
    assert _audit("SECURITY:SESSION_IDLE_TIMEOUT", user_system=a["admin"])


# ---------------------------------------------------------------- A3 sockets and legacy tokens

def test_a3_logout_closes_the_signal_stream(two_orgs, monkeypatch):
    import app.api.v1.endpoints.signals as signals
    monkeypatch.setattr(signals, "STREAM_RECHECK_SECONDS", 0.2)
    token = _token(two_orgs["A"]["admin"])
    with client.websocket_connect(f"{API}/signals/ws?token={token}", headers={"origin": ORIGIN}) as ws:
        assert client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_text()
        assert closed.value.code == 1008


def test_a3_an_ended_session_cannot_join_or_stay_in_chat(two_orgs):
    a = two_orgs["A"]
    token = _token(a["admin"])
    room = f"org_{a['org']}"
    with client.websocket_connect(f"{API}/pasme/chat/ws/{room}?token={token}", headers={"origin": ORIGIN}) as ws:
        ws.send_json({"text": "hello"})
        assert ws.receive_json()["text"] == "hello"
        assert client.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        time.sleep(1.1)                                    # past the chat's one-message-a-second limit
        ws.send_json({"text": "after logout"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"{API}/pasme/chat/ws/{room}?token={token}", headers={"origin": ORIGIN}) as ws:
            ws.receive_json()


def test_a3_a_token_without_a_server_session_is_refused_everywhere(two_orgs):
    a = two_orgs["A"]
    db = SessionLocal()
    try:
        uid = db.query(User.id).filter(User.email == a["admin"]).first()[0]
    finally:
        db.close()
    legacy = create_access_token({"sub": a["admin"], "uid": uid})
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 401 and "reauthenticate" in r.json()["detail"]
    for url in (f"{API}/signals/ws?token={legacy}", f"{API}/pasme/chat/ws/org_{a['org']}?token={legacy}"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(url, headers={"origin": ORIGIN}) as ws:
                ws.receive_text()


# ---------------------------------------------------------------- A4 same-second reset

def _reset_link(email):
    auth_endpoints._ip_rate_limits.clear()
    assert client.post(f"{API}/auth/forgot-password", json={"email": email}).status_code == 200
    for mail in reversed(OUTBOX):
        m = re.search(r"reset_token=([A-Za-z0-9_\-]+)", mail["body"])
        if m:
            return m.group(1)
    raise AssertionError("no reset link")


def test_a4_a_sign_in_in_the_same_second_as_the_reset_keeps_working(two_orgs):
    a = two_orgs["A"]
    email = f"pr8reset-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.STAFF_USER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    old = _h(email)
    token = _reset_link(email)
    auth_endpoints._ip_rate_limits.clear()
    reset = {"token": token, "new_password": "Changed2026x", "confirm_password": "Changed2026x"}
    assert client.post(f"{API}/auth/reset-password", json=reset).status_code == 200
    fresh = _h(email, "Changed2026x")                      # same second as the reset
    assert client.get(f"{API}/auth/me", headers=fresh).status_code == 200
    assert client.get(f"{API}/auth/me", headers=old).status_code == 401
    assert client.get(f"{API}/auth/me", headers=fresh).status_code == 200


# ---------------------------------------------------------------- A5 credentials

def test_a5_pending_and_inactive_accounts_get_the_generic_answer(two_orgs):
    a = two_orgs["A"]
    pending = f"pr8pending-{_tag()}@sentinel-test.com"
    _db_user(pending, UserRole.STAFF_USER.value, org_id=a["org"], clinic_id=a["clinics"][0], password=None)
    inactive = f"pr8inactive-{_tag()}@sentinel-test.com"
    _db_user(inactive, UserRole.STAFF_USER.value, org_id=a["org"], clinic_id=a["clinics"][0], active=False)
    attempts = ((pending, PW), (pending, ""), (inactive, PW), (f"nobody-{_tag()}@sentinel-test.com", PW))
    answers = {_login(e, p).json()["detail"] for e, p in attempts}
    assert answers == {"Incorrect email or password"}
    assert _audit("SECURITY:FAILED_LOGIN_PENDING_ACTIVATION", user_system=pending)
    assert _audit("SECURITY:FAILED_LOGIN_INACTIVE", user_system=inactive)


def test_a5_unicode_passwords_and_required_confirmation(two_orgs):
    a = two_orgs["A"]
    email = f"pr8uni-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.STAFF_USER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    token = _reset_link(email)
    auth_endpoints._ip_rate_limits.clear()
    missing = client.post(f"{API}/auth/reset-password", json={"token": token, "new_password": "Pässwört2026"})
    assert missing.status_code == 422                       # confirmation is required, not optional
    mismatch = client.post(f"{API}/auth/reset-password",
                           json={"token": token, "new_password": "Pässwört2026", "confirm_password": "Passwort2026"})
    assert mismatch.status_code == 400                      # a Unicode mismatch is a clean 400, not a 500
    ok = client.post(f"{API}/auth/reset-password",
                     json={"token": token, "new_password": "Pässwört2026", "confirm_password": "Pässwört2026"})
    assert ok.status_code == 200, ok.text
    assert _login(email, "Pässwört2026").status_code == 200
    assert _login(email, "Pässwört2026 ").status_code == 400


def test_a5_signup_does_not_reveal_existing_accounts(two_orgs):
    auth_endpoints._ip_rate_limits.clear()
    existing = client.post(f"{API}/auth/register/initiate", json={"email": two_orgs["A"]["admin"], "password": PW, "full_name": "Someone"})
    auth_endpoints._ip_rate_limits.clear()
    fresh = {"email": f"pr8new-{_tag()}@sentinel-test.com", "password": PW, "full_name": "Someone"}
    new = client.post(f"{API}/auth/register/initiate", json=fresh)
    assert existing.status_code == new.status_code == 200
    assert existing.json() == new.json()


# ---------------------------------------------------------------- A6 / A7 connections and syncs

def _connector(clinic_id, status="Configured"):
    cid = f"{clinic_id}:pr8_{_tag()}"
    db = SessionLocal()
    try:
        db.add(ConnectorModel(id=cid, name="Gateway", type="Clearinghouse", status=status, clinic_id=clinic_id,
                              config={"endpoint": "https://example.org"}))
        db.commit()
    finally:
        db.close()
    return cid


def _probe(cid, outcome, age=timedelta(0)):
    db = SessionLocal()
    try:
        db.add(ConnectorProbeModel(connector_id=cid, outcome=outcome, probed_at=datetime.utcnow() - age,
                                   detail=None if outcome == "verified" else "fixture"))
        db.commit()
    finally:
        db.close()


def test_a7_every_connection_state_comes_from_a_provider_check(two_orgs, monkeypatch):
    a = two_orgs["A"]
    clinic = a["clinics"][0]

    def state(cid):
        return next(c for c in client.get(f"{API}/reality/health", headers=a["h"]).json() if c["id"] == cid)

    assert state(_connector(clinic, "Draft"))["verification"] == "draft"
    registered = _connector(clinic)
    assert state(registered)["verification"] == "registered_unverified"
    for outcome, expected in (("unavailable", "registered_unverified"), ("failed", "failed"),
                              ("verified", "verified")):
        monkeypatch.setattr(reality, "run_probe", lambda c, o=outcome: (o, None if o == "verified" else "fixture"))
        r = client.post(f"{API}/reality/verify/{registered}", headers=a["h"])
        assert r.status_code == 200, r.text
        assert state(registered)["verification"] == expected
    stale = _connector(clinic)
    _probe(stale, "verified", age=timedelta(hours=25))
    assert state(stale)["verification"] == "stale"
    # the settings list agrees with the Integration Center
    rows = {i["id"]: i for i in client.get(f"{API}/settings/integrations", headers=a["h"]).json()}
    assert rows[registered]["connected"] is True and rows[stale]["connected"] is False
    assert rows[stale]["verification"] == "stale"


def test_a6_sync_live_data_needs_a_verified_connection_and_records_every_run(two_orgs, monkeypatch):
    a = two_orgs["A"]
    empty, clinic = a["clinics"]
    r = client.post(f"{API}/reality/sync-clinic", headers=a["h"], json={"clinic_id": empty})
    assert r.status_code == 409 and "No connection is configured" in r.json()["detail"]
    status = client.get(f"{API}/reality/sync-status", headers=a["h"], params={"clinic_id": empty}).json()
    assert status["configured"] is False and status["last_success"] is None
    cid = _connector(clinic)
    r = client.post(f"{API}/reality/sync-clinic", headers=a["h"], json={"clinic_id": clinic})
    assert r.status_code == 409 and "not verified" in r.json()["detail"]
    _probe(cid, "verified")

    async def ok(_id):
        return {"status": "Success", "imported_count": 7}

    async def broken(_id):
        return {"status": "Error", "error": "Provider timed out"}
    monkeypatch.setattr(reality.connector_manager, "sync_connector", ok)
    r = client.post(f"{API}/reality/sync-clinic", headers=a["h"], json={"clinic_id": clinic})
    assert r.status_code == 200, r.text
    first = r.json()["last_success"]
    assert first["imported_count"] == 7 and first["status"] == "succeeded"
    monkeypatch.setattr(reality.connector_manager, "sync_connector", broken)
    r = client.post(f"{API}/reality/sync-clinic", headers=a["h"], json={"clinic_id": clinic})
    assert r.status_code == 200
    body = r.json()
    assert body["last_run"]["status"] == "failed" and body["last_run"]["error"] == "Provider timed out"
    assert body["last_success"]["run_id"] == first["run_id"]       # a failure keeps the last success
    db = SessionLocal()
    try:
        runs = db.query(ConnectorSyncRunModel).filter(ConnectorSyncRunModel.clinic_id == clinic).all()
        imports = db.query(DataImportModel).filter(DataImportModel.clinic_id == clinic).all()
        assert sorted(r.status for r in runs) == ["failed", "succeeded"]
        assert len(imports) == 1 and imports[0].accepted_count == 7 and imports[0].run_id == first["run_id"]
    finally:
        db.close()
    assert _audit("CONNECTOR_SYNC_SUCCEEDED", clinic_id=clinic) and _audit("CONNECTOR_SYNC_FAILED", clinic_id=clinic)
    # another organization's clinic is out of reach
    assert client.post(f"{API}/reality/sync-clinic", headers=two_orgs["B"]["h"],
                       json={"clinic_id": clinic}).status_code in (403, 404)


# ---------------------------------------------------------------- A8 imports

def test_a8_a_hand_typed_encounter_is_not_an_import(two_orgs):
    a = two_orgs["A"]
    body = {"id": f"PR8-{_tag()}", "patient_name": "Typed Patient", "provider_name": "Dr Test",
            "clinic_id": a["clinics"][0], "date": datetime.utcnow().date().isoformat(), "type": "Consultation"}
    assert client.post(f"{API}/encounters", headers=a["h"], json=body).status_code == 201
    clinic = client.get(f"{API}/clinics/{a['clinics'][0]}", headers=a["h"]).json()
    workbook = next(s for s in clinic["data_sources"] if s["kind"] == "workbook")
    assert workbook["status"] == "Not configured" and workbook["records_count"] == 0 and workbook["imports"] == 0
    assert clinic["external_connectors"] == []


# ---------------------------------------------------------------- A9 / A10 the r2 upgrade

def _load_r2():
    here = os.path.dirname(__file__)
    path = os.path.abspath(os.path.join(here, "../../alembic/versions/r2_ms001_ownership.py"))
    spec = importlib.util.spec_from_file_location("r2_ms001_ownership_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _populated_r1_database(engine):
    """A populated database as r1 left it, with every ownership case the audit names."""
    now = datetime.utcnow()
    with engine.begin() as c:
        for ddl in (
            "CREATE TABLE organizations (id VARCHAR PRIMARY KEY, name VARCHAR, status VARCHAR, time_zone VARCHAR, "
            "created_date DATETIME, updated_at DATETIME)",
            "CREATE TABLE clinics (id VARCHAR PRIMARY KEY, name VARCHAR, address VARCHAR, phone VARCHAR, "
            "status VARCHAR, org_id VARCHAR, created_at DATETIME, updated_at DATETIME)",
            "CREATE TABLE org_clinics (id VARCHAR PRIMARY KEY, organization_id VARCHAR, name VARCHAR, "
            "location VARCHAR, is_active BOOLEAN)",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, org_id VARCHAR, clinic_id VARCHAR)",
            "CREATE TABLE signals (id VARCHAR PRIMARY KEY, org_id VARCHAR, clinic_id VARCHAR)",
            "CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, action VARCHAR, user_system VARCHAR, org_id VARCHAR, "
            "clinic_id VARCHAR, target_type VARCHAR, target_id VARCHAR)",
            "CREATE TABLE settings (id INTEGER PRIMARY KEY, org_id VARCHAR, clinic_id VARCHAR, practice_name VARCHAR)",
        ):
            c.execute(text(ddl))
        for oid in ("ORG-1", "ORG-2"):
            c.execute(text("INSERT INTO organizations VALUES (:i, :i, 'Active', 'UTC', :n, :n)"), {"i": oid, "n": now})
        clinics = {
            "C-QUIET": None,         # mapped to ORG-1, no users or signals yet
            "C-STAFFED": None,       # no mapping; its users and signals all belong to ORG-2
            "C-CONFLICT": None,      # mapped to ORG-1, but its users belong to ORG-2
            "C-ORPHAN": None,        # nothing names an owner
            "C-SPLIT": None,         # no mapping; activity names two organizations
            "C-OWNED": "ORG-2",      # already owned, and the mapping agrees
        }
        for cid, owner in clinics.items():
            c.execute(text("INSERT INTO clinics VALUES (:c, :c, '', '', 'Active', :o, :n, :n)"),
                      {"c": cid, "o": owner, "n": now})
        for cid, oid in (("C-QUIET", "ORG-1"), ("C-CONFLICT", "ORG-1"), ("C-OLDONLY", "ORG-1"),
                         ("C-OWNED", "ORG-2"), ("C-NEWORG", "ORG-9")):
            c.execute(text("INSERT INTO org_clinics VALUES (:c, :o, :c, 'Old street', 1)"), {"c": cid, "o": oid})
        for i, (oid, cid) in enumerate((("ORG-2", "C-STAFFED"), ("ORG-2", "C-CONFLICT"), ("ORG-1", "C-SPLIT"),
                                        ("ORG-2", "C-SPLIT"))):
            c.execute(text("INSERT INTO users VALUES (:i, :e, :o, :c)"), {"i": i + 1, "e": f"u{i}@x", "o": oid, "c": cid})
        c.execute(text("INSERT INTO signals VALUES ('S-1', 'ORG-2', 'C-STAFFED')"))
        # audit: an MS-001 row (scope recorded with its subject) and an old row r1 had given today's account
        c.execute(text("INSERT INTO audit_logs VALUES (1, 'CLINIC_CREATED', 'a@x', 'ORG-1', 'C-QUIET', 'clinic', 'C-QUIET')"))
        c.execute(text("INSERT INTO audit_logs VALUES (2, 'LEGACY_EVENT', 'u1@x', 'ORG-2', 'C-CONFLICT', NULL, NULL)"))
        for i, (oid, cid) in enumerate((("ORG-1", None), ("ORG-1", None), ("ORG-1", "C-QUIET"), ("ORG-1", "C-QUIET"),
                                        (None, None))):
            c.execute(text("INSERT INTO settings VALUES (:i, :o, :c, :p)"), {"i": i + 1, "o": oid, "c": cid, "p": f"p{i}"})


def _upgrade_with_r2(engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    r2 = _load_r2()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            r2.upgrade()
    return r2


def test_a9_a10_the_upgrade_reconciles_ownership_from_evidence_only():
    engine = create_engine("sqlite:///:memory:")
    _populated_r1_database(engine)
    _upgrade_with_r2(engine)
    with engine.connect() as c:
        owner = dict(c.execute(text("SELECT id, org_id FROM clinics")).fetchall())
        review = {(r[0], r[1]) for r in c.execute(text(
            "SELECT subject_id, kind FROM ownership_review WHERE resolved_at IS NULL AND subject_type = 'clinic'"))}
        settings_review = {r[0] for r in c.execute(text(
            "SELECT subject_id FROM ownership_review WHERE subject_type = 'settings'"))}
        audit = {r[0]: (r[1], r[2], r[3]) for r in c.execute(text(
            "SELECT id, org_id, clinic_id, scope_source FROM audit_logs"))}
        settings_rows = c.execute(text("SELECT id, org_id, clinic_id FROM settings ORDER BY id")).fetchall()
        orgs = {r[0] for r in c.execute(text("SELECT id FROM organizations"))}
    assert owner["C-QUIET"] == "ORG-1"                     # a quiet clinic keeps its mapped owner
    assert owner["C-STAFFED"] == "ORG-2"                   # unambiguous activity
    assert owner["C-OLDONLY"] == "ORG-1"                   # known only to the old table: brought back
    assert owner["C-OWNED"] == "ORG-2"
    assert owner["C-NEWORG"] == "ORG-9" and "ORG-9" in orgs
    for cid in ("C-CONFLICT", "C-ORPHAN", "C-SPLIT"):      # never guessed
        assert owner[cid] is None
    assert review == {("C-CONFLICT", "conflict"), ("C-ORPHAN", "orphan"), ("C-SPLIT", "conflict")}
    assert audit[1] == ("ORG-1", "C-QUIET", "event")
    assert audit[2] == (None, None, "unresolved")          # today's membership is not history
    assert [tuple(r) for r in settings_rows] == [(1, "ORG-1", None), (3, "ORG-1", "C-QUIET"), (5, None, None)]
    assert settings_review == {"5"}                        # the unowned global row is listed, not assigned
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO settings (id, org_id, clinic_id) VALUES (9, 'ORG-1', NULL)"))


def test_a9_running_the_upgrade_twice_changes_nothing():
    engine = create_engine("sqlite:///:memory:")
    _populated_r1_database(engine)
    r2 = _upgrade_with_r2(engine)

    def snapshot():
        with engine.connect() as c:
            return (c.execute(text("SELECT id, org_id FROM clinics ORDER BY id")).fetchall(),
                    c.execute(text("SELECT subject_id, kind FROM ownership_review ORDER BY id")).fetchall(),
                    c.execute(text("SELECT id, org_id, clinic_id, scope_source FROM audit_logs ORDER BY id")).fetchall())
    before = snapshot()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            r2.upgrade()
    assert snapshot() == before


def test_a10_the_platform_sees_unresolved_owners(platform, two_orgs):
    from app.models.integration_evidence import OwnershipReviewModel
    db = SessionLocal()
    try:
        subject = f"C-REVIEW-{_tag()}"
        db.add(OwnershipReviewModel(subject_type="clinic", subject_id=subject, kind="orphan", detail="fixture"))
        db.commit()
    finally:
        db.close()
    rows = client.get(f"{API}/organizations/ownership-review", headers=platform)
    assert rows.status_code == 200 and subject in {r["subject_id"] for r in rows.json()}
    assert client.get(f"{API}/organizations/ownership-review", headers=two_orgs["A"]["h"]).status_code == 403


def test_a10_new_audit_rows_record_their_scope_at_the_time(two_orgs):
    a = two_orgs["A"]
    rows = _audit("CLINIC_CREATED", org_id=a["org"])
    assert rows and all(r.scope_source == "event" for r in rows)


# ---------------------------------------------------------------- settings, links, inbox

def test_settings_first_open_from_many_requests_makes_one_row(two_orgs):
    a = two_orgs["A"]
    results = []

    def open_settings():
        results.append(client.get(f"{API}/settings", headers=a["h"]).status_code)
    threads = [threading.Thread(target=open_settings) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results and set(results) == {200}
    db = SessionLocal()
    try:
        rows = db.query(SettingsModel).filter(SettingsModel.org_id == a["org"], SettingsModel.clinic_id.is_(None)).all()
        assert len(rows) == 1
        db.add(SettingsModel(org_id=a["org"], clinic_id=None))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_a_one_time_link_is_consumed_once(two_orgs):
    from fastapi import HTTPException
    from app.core import passwords
    a = two_orgs["A"]
    email = f"pr8link-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.STAFF_USER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    _reset_link(email)
    first, second = SessionLocal(), SessionLocal()
    try:
        row1 = first.query(OTPModel).filter(OTPModel.email == email, OTPModel.is_used == False).first()  # noqa: E712
        row2 = second.query(OTPModel).filter(OTPModel.id == row1.id).first()
        passwords.consume(first, row1)          # both requests read the link as unused ...
        first.commit()
        with pytest.raises(HTTPException) as refused:
            passwords.consume(second, row2)     # ... only one can use it
        assert refused.value.status_code == 400
    finally:
        first.close()
        second.close()


def test_a_removed_role_has_no_inbox_after_a_fresh_sign_in(two_orgs):
    a = two_orgs["A"]
    email = f"pr8unassigned-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.UNASSIGNED.value, org_id=a["org"])
    r = _login(email)
    if r.status_code == 200:
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert client.get(f"{API}/notifications", headers=h).status_code == 403
        assert client.post(f"{API}/notifications/read-all", headers=h).status_code == 403
    else:
        assert r.status_code in (400, 403)
