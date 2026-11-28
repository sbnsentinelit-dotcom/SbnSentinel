"""
MS-001 founding V1 acceptance (auditor's report on main 665fa4f, "Developer se kis saboot par sign-off lein").

Every test goes through the real API with real sign-in tokens: the normal user action, the server
decision, the database change and the audit row it must leave.

1. Tenant isolation: two organizations, two clinics each; settings, users, clinics, audit, connectors and
   notifications stay inside their owner.
2. Organizations and roles: create / edit / suspend / activate; the six approved roles are provisioned.
3. Identity lifecycle: invitation link -> terms -> password -> sign-in; reset link; signed-in change;
   logout and idle timeout end the session on the server.
4. Truthful interface: profile edits persist; an empty clinic shows nothing imported; a connection shows
   draft / registered / verified as it is.
5. Notifications and audit: a read mark is per user; administrative changes are audited with scope.
"""
import re
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.v1.endpoints.auth as auth_endpoints
from app.core.security import get_password_hash
from app.db.database import SessionLocal
from app.main import app
from app.models.audit import AuditLogModel
from app.models.identity import UserSessionModel
from app.models.signal import SignalModel
from app.models.user import User, UserRole

client = TestClient(app)
API = "/api/v1"
PW = "Sentinel2026pass"
OUTBOX = []


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    """Capture invitation and reset emails instead of sending them."""
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


def _db_user(email, role, org_id=None, clinic_id=None, active=True):
    db = SessionLocal()
    try:
        u = User(email=email, full_name=email.split("@")[0], role=role, org_id=org_id, clinic_id=clinic_id,
                 is_active=active, hashed_password=get_password_hash(PW))
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _login(email, password=PW):
    auth_endpoints._ip_rate_limits.clear()
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    return r


def _h(email, password=PW):
    r = _login(email, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _audit(action, **where):
    db = SessionLocal()
    try:
        q = db.query(AuditLogModel).filter(AuditLogModel.action == action)
        for k, v in where.items():
            q = q.filter(getattr(AuditLogModel, k) == v)
        return q.all()
    finally:
        db.close()


def _link_token(kind):
    """The token in the newest captured email link (invite_token or reset_token)."""
    for mail in reversed(OUTBOX):
        m = re.search(rf"{kind}=([A-Za-z0-9_\-]+)", mail["body"])
        if m:
            return m.group(1)
    raise AssertionError(f"no {kind} link in {OUTBOX}")


@pytest.fixture
def platform():
    email = f"platform-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.SYSTEM_ADMINISTRATOR.value)
    return _h(email)


@pytest.fixture
def two_orgs(platform):
    """Two organizations with two clinics and an Organization Administrator each."""
    out = {}
    for key in ("A", "B"):
        t = _tag()
        org = client.post(f"{API}/organizations", headers=platform, json={"name": f"Org {key} {t}"})
        assert org.status_code == 201, org.text
        org_id = org.json()["id"]
        admin = f"admin{key.lower()}-{t}@sentinel-test.com"
        _db_user(admin, UserRole.ORGANIZATION_ADMINISTRATOR.value, org_id=org_id)
        h = _h(admin)
        clinics = []
        for n in (1, 2):
            c = client.post(f"{API}/clinics/", headers=h, json={"name": f"Clinic {key}{n} {t}", "address": f"Street {n}"})
            assert c.status_code == 201, c.text
            clinics.append(c.json()["id"])
        out[key] = {"org": org_id, "admin": admin, "h": h, "clinics": clinics}
    return out


# ---------------------------------------------------------------- 1. Tenant isolation

def test_settings_are_owned_by_their_organization(two_orgs):
    a, b = two_orgs["A"], two_orgs["B"]
    r = client.post(f"{API}/settings", headers=a["h"], json={"practice_name": "Practice of A"})
    assert r.status_code == 200 and r.json()["org_id"] == a["org"]
    assert client.get(f"{API}/settings", headers=b["h"]).json()["practice_name"] != "Practice of A"
    assert client.get(f"{API}/settings", headers=a["h"]).json()["practice_name"] == "Practice of A"
    # naming another organization is refused
    assert client.get(f"{API}/settings", headers=b["h"], params={"org_id": a["org"]}).status_code == 403
    rows = _audit("SETTINGS_UPDATED", org_id=a["org"])
    assert len(rows) == 1 and rows[0].user_system == a["admin"] and "practice_name" in rows[0].detail


def test_clinics_users_audit_and_connectors_stay_inside_their_organization(two_orgs):
    a, b = two_orgs["A"], two_orgs["B"]
    seen_by_b = {c["id"] for c in client.get(f"{API}/clinics/", headers=b["h"]).json()}
    assert seen_by_b == set(b["clinics"])
    assert client.get(f"{API}/clinics/{a['clinics'][0]}", headers=b["h"]).status_code == 404
    assert client.patch(f"{API}/clinics/{a['clinics'][0]}", headers=b["h"], json={"name": "x"}).status_code == 404

    inv = client.post(f"{API}/settings/team/invite", headers=a["h"],
                      json={"email": f"staff-{_tag()}@sentinel-test.com", "role": "Staff User", "clinic_id": a["clinics"][0]})
    assert inv.status_code == 200, inv.text
    team_b = client.get(f"{API}/settings/team", headers=b["h"]).json()
    assert all(m["org_id"] == b["org"] for m in team_b)
    assert client.patch(f"{API}/settings/team/{inv.json()['id']}", headers=b["h"], json={"name": "x"}).status_code == 404

    audit_b = client.get(f"{API}/audit/", headers=b["h"]).json()
    assert audit_b and all(row["org_id"] == b["org"] for row in audit_b)

    conn = client.post(f"{API}/reality/connect", headers=a["h"],
                       json={"id": f"conn-{_tag()}", "name": "Gateway", "type": "Clearinghouse",
                             "clinic_id": a["clinics"][1], "config": {"endpoint": "https://example.org"}})
    assert conn.status_code == 201, conn.text
    assert conn.json()["verification"] == "registered_unverified"
    assert conn.json()["connector_id"] not in {c["id"] for c in client.get(f"{API}/reality/health", headers=b["h"]).json()}
    assert client.delete(f"{API}/reality/disconnect/{conn.json()['connector_id']}", headers=b["h"]).status_code == 404


def test_clinic_roles_see_only_their_own_clinic(two_orgs):
    a = two_orgs["A"]
    email = f"cadmin-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.CLINIC_ADMINISTRATOR.value, org_id=a["org"], clinic_id=a["clinics"][0])
    h = _h(email)
    assert [c["id"] for c in client.get(f"{API}/clinics/", headers=h).json()] == [a["clinics"][0]]
    assert client.get(f"{API}/clinics/{a['clinics'][1]}", headers=h).status_code == 404
    s = client.get(f"{API}/settings", headers=h).json()
    assert s["clinic_id"] == a["clinics"][0] and s["scope"] == "clinic"
    # a clinic administrator cannot create clinics or give an organization role
    assert client.post(f"{API}/clinics/", headers=h, json={"name": "Nope"}).status_code == 403
    r = client.post(f"{API}/settings/team/invite", headers=h,
                    json={"email": f"x-{_tag()}@sentinel-test.com", "role": "Organization Administrator"})
    assert r.status_code == 403


# ---------------------------------------------------------------- 2. Organizations and roles

def test_platform_administrator_manages_the_organization_lifecycle(platform):
    name = f"Lifecycle Org {_tag()}"
    org = client.post(f"{API}/organizations", headers=platform, json={"name": name}).json()
    assert client.post(f"{API}/organizations", headers=platform, json={"name": name.upper()}).status_code == 409
    upd = client.patch(f"{API}/organizations/{org['id']}", headers=platform, json={"name": name + " Renamed"})
    assert upd.json()["name"] == name + " Renamed"

    admin = f"life-{_tag()}@sentinel-test.com"
    _db_user(admin, UserRole.ORGANIZATION_ADMINISTRATOR.value, org_id=org["id"])
    h = _h(admin)
    sus = client.post(f"{API}/organizations/{org['id']}/suspend", headers=platform, json={"reason": "contract paused"})
    assert sus.json()["status"] == "Suspended"
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401        # the open session ended
    assert _login(admin).status_code == 403                                  # sign-in refused
    assert client.post(f"{API}/organizations/{org['id']}/activate", headers=platform).json()["status"] == "Active"
    h = _h(admin)                                                           # signs in again once active
    for action in ("ORGANIZATION_CREATED", "ORGANIZATION_UPDATED", "ORGANIZATION_SUSPENDED", "ORGANIZATION_ACTIVATED"):
        assert _audit(action, org_id=org["id"]), action
    assert _audit("SECURITY:FAILED_LOGIN_ORG_SUSPENDED", org_id=org["id"])
    # only the platform administrator manages organizations
    assert client.post(f"{API}/organizations", headers=h, json={"name": "x"}).status_code == 403


def test_org_admin_creates_edits_and_deactivates_a_clinic(two_orgs):
    a = two_orgs["A"]
    cid = a["clinics"][0]
    upd = client.patch(f"{API}/clinics/{cid}", headers=a["h"], json={"name": "Renamed Clinic " + _tag(), "address": "New Road"})
    assert upd.status_code == 200 and upd.json()["address"] == "New Road"
    off = client.post(f"{API}/clinics/{cid}/status", headers=a["h"], json={"status": "Inactive"})
    assert off.json()["status"] == "Inactive" and off.json()["deactivated_at"]
    assert client.get(f"{API}/clinics/{cid}", headers=a["h"]).json()["status"] == "Inactive"   # persisted
    dup = client.post(f"{API}/clinics/", headers=a["h"], json={"name": upd.json()["name"]})
    assert dup.status_code == 409
    for action in ("CLINIC_CREATED", "CLINIC_UPDATED", "CLINIC_DEACTIVATED"):
        assert _audit(action, clinic_id=cid), action


def test_the_six_roles_are_provisioned_from_the_normal_invite(two_orgs, platform):
    a = two_orgs["A"]
    roles = {r["role"] for r in client.get(f"{API}/settings/roles", headers=a["h"]).json()}
    assert {"Organization Administrator", "Clinic Administrator", "Operations Manager", "Practice Manager",
            "Staff User"} <= roles
    for role in ("Clinic Administrator", "Operations Manager", "Practice Manager", "Staff User"):
        r = client.post(f"{API}/settings/team/invite", headers=a["h"],
                        json={"email": f"{role[:4].lower()}-{_tag()}@sentinel-test.com", "role": role,
                              "clinic_id": a["clinics"][1]})
        assert r.status_code == 200, (role, r.text)
        assert r.json()["status"] == "Pending" and r.json()["clinic_id"] == a["clinics"][1]
        assert r.json()["email_sent"] is True
    # a clinic role needs a clinic when the organization has more than one
    r = client.post(f"{API}/settings/team/invite", headers=a["h"],
                    json={"email": f"noclinic-{_tag()}@sentinel-test.com", "role": "Staff User"})
    assert r.status_code == 400
    # the earlier role name still works and is stored under its new name
    r = client.post(f"{API}/settings/team/invite", headers=a["h"],
                    json={"email": f"legacy-{_tag()}@sentinel-test.com", "role": "Front Desk", "clinic_id": a["clinics"][0]})
    assert r.status_code == 200 and r.json()["role"] == "Staff User"
    # platform overview and invite no longer fail
    stats = client.get(f"{API}/super-admin/stats", headers=platform)
    assert stats.status_code == 200 and stats.json()["total_organizations"] >= 2
    assert stats.json()["ai_token_usage_today"] == "Not measured"
    inv = client.post(f"{API}/super-admin/users/invite", headers=platform,
                      json={"email": f"owner-{_tag()}@sentinel-test.com", "full_name": "Owner", "org_id": a["org"]})
    assert inv.status_code == 200, inv.text
    assert inv.json()["role"] == "Organization Administrator" and "temp_password" not in inv.json()


def test_role_change_and_removal_are_audited_and_end_the_users_sessions(two_orgs):
    a = two_orgs["A"]
    email = f"ops-{_tag()}@sentinel-test.com"
    uid = _db_user(email, UserRole.OPERATIONS_MANAGER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    h = _h(email)
    r = client.patch(f"{API}/settings/team/{uid}", headers=a["h"], json={"role": "Practice Manager"})
    assert r.status_code == 200 and r.json()["role"] == "Practice Manager"
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401
    r = client.patch(f"{API}/settings/team/{uid}", headers=a["h"], json={"role": "Unassigned"})
    assert r.json()["role"] == "Unassigned" and r.json()["clinic_id"] is None
    assert _audit("USER_ROLE_CHANGED", target_id=str(uid)) and _audit("USER_ROLE_REMOVED", target_id=str(uid))
    # an administrator cannot change their own role
    me = client.get(f"{API}/auth/me", headers=a["h"]).json()
    assert client.patch(f"{API}/settings/team/{me['id']}", headers=a["h"], json={"role": "Staff User"}).status_code == 403


# ---------------------------------------------------------------- 3. Identity lifecycle

def test_invitation_link_terms_password_and_first_sign_in(two_orgs):
    a = two_orgs["A"]
    email = f"newstaff-{_tag()}@sentinel-test.com"
    r = client.post(f"{API}/settings/team/invite", headers=a["h"],
                    json={"email": email, "name": "New Staff", "role": "Staff User", "clinic_id": a["clinics"][0]})
    assert r.status_code == 200
    token = _link_token("invite_token")
    info = client.get(f"{API}/auth/invitation", params={"token": token}).json()
    assert info["email"] == email and info["role_label"] == "Staff User" and info["clinic"]
    base = {"token": token, "new_password": "Welcome2026", "confirm_password": "Welcome2026"}
    assert client.post(f"{API}/auth/accept-invite", json={**base, "accept_terms": False}).status_code == 400
    assert client.post(f"{API}/auth/accept-invite",
                       json={**base, "confirm_password": "Different2026", "accept_terms": True}).status_code == 400
    assert client.post(f"{API}/auth/accept-invite",
                       json={**base, "new_password": "short", "confirm_password": "short", "accept_terms": True}).status_code == 400
    ok = client.post(f"{API}/auth/accept-invite", json={**base, "accept_terms": True})
    assert ok.status_code == 200, ok.text
    assert client.post(f"{API}/auth/accept-invite", json={**base, "accept_terms": True}).status_code == 400   # used once
    me = client.get(f"{API}/auth/me", headers=_h(email, "Welcome2026")).json()
    assert me["terms_accepted_at"] and me["clinic_id"] == a["clinics"][0]
    assert _audit("USER_INVITED", org_id=a["org"]) and _audit("USER_ACTIVATED", user_system=email)


def test_reset_link_retires_every_earlier_credential_and_ends_sessions(two_orgs):
    a = two_orgs["A"]
    email = f"reset-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.STAFF_USER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    h = _h(email)
    auth_endpoints._ip_rate_limits.clear()
    assert client.post(f"{API}/auth/forgot-password", json={"email": email}).status_code == 200
    first = _link_token("reset_token")
    db = SessionLocal()   # the next request would be throttled within a minute; age the first one
    from app.models.otp import OTPModel
    for row in db.query(OTPModel).filter(OTPModel.email == email).all():
        row.created_at = row.created_at - timedelta(minutes=2)
    db.commit()
    db.close()
    auth_endpoints._ip_rate_limits.clear()
    client.post(f"{API}/auth/forgot-password", json={"email": email})
    second = _link_token("reset_token")
    assert first != second
    auth_endpoints._ip_rate_limits.clear()
    assert client.post(f"{API}/auth/reset-password",
                       json={"token": first, "new_password": "Changed2026", "confirm_password": "Changed2026"}).status_code == 400
    r = client.post(f"{API}/auth/reset-password",
                    json={"token": second, "new_password": "Changed2026", "confirm_password": "Changed2026"})
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401
    assert _login(email, "Changed2026").status_code == 200
    assert _audit("SECURITY:PASSWORD_RESET", user_system=email)
    # an unknown email gets the same answer
    auth_endpoints._ip_rate_limits.clear()
    unknown = client.post(f"{API}/auth/forgot-password", json={"email": f"nobody-{_tag()}@sentinel-test.com"})
    auth_endpoints._ip_rate_limits.clear()
    assert unknown.json() == client.post(f"{API}/auth/forgot-password", json={"email": email}).json()


def test_signed_in_password_change_needs_the_current_password(two_orgs):
    a = two_orgs["A"]
    email = f"change-{_tag()}@sentinel-test.com"
    _db_user(email, UserRole.PRACTICE_MANAGER.value, org_id=a["org"], clinic_id=a["clinics"][0])
    h1, h2 = _h(email), _h(email)
    bad = client.post(f"{API}/auth/change-password", headers=h1,
                      json={"current_password": "wrong", "new_password": "Brandnew2026", "confirm_password": "Brandnew2026"})
    assert bad.status_code == 400
    ok = client.post(f"{API}/auth/change-password", headers=h1,
                     json={"current_password": PW, "new_password": "Brandnew2026", "confirm_password": "Brandnew2026"})
    assert ok.status_code == 200 and ok.json()["other_sessions_ended"] == 1
    assert client.get(f"{API}/auth/me", headers=h1).status_code == 200     # this session stays
    assert client.get(f"{API}/auth/me", headers=h2).status_code == 401     # the other one ended
    assert _login(email).status_code == 400 and _login(email, "Brandnew2026").status_code == 200
    assert _audit("SECURITY:PASSWORD_CHANGED", user_system=email)


def test_logout_ends_the_session_on_the_server(two_orgs):
    a = two_orgs["A"]
    r = client.post(f"{API}/auth/logout", headers=a["h"])
    assert r.status_code == 200 and r.json()["session_id"]
    assert client.get(f"{API}/auth/me", headers=a["h"]).status_code == 401
    db = SessionLocal()
    try:
        row = db.query(UserSessionModel).filter(UserSessionModel.id == r.json()["session_id"]).first()
        assert row.ended_at is not None and row.end_reason == "logout"
    finally:
        db.close()
    assert _audit("SECURITY:USER_LOGOUT", user_system=a["admin"], org_id=a["org"])


def test_an_idle_session_expires_on_the_server(two_orgs):
    a = two_orgs["A"]
    sessions = client.get(f"{API}/auth/sessions", headers=a["h"]).json()
    current = next(s for s in sessions["sessions"] if s["current"])
    db = SessionLocal()
    try:
        row = db.query(UserSessionModel).filter(UserSessionModel.id == current["id"]).first()
        row.last_seen_at = datetime.utcnow() - timedelta(minutes=sessions["idle_timeout_minutes"] + 1)
        db.commit()
    finally:
        db.close()
    r = client.get(f"{API}/auth/me", headers=a["h"])
    assert r.status_code == 401 and "idle_timeout" in r.json()["detail"]
    assert _audit("SECURITY:SESSION_IDLE_TIMEOUT", user_system=a["admin"])


# ---------------------------------------------------------------- 4. Truthful interface

def test_profile_edits_persist_and_are_audited(two_orgs):
    a = two_orgs["A"]
    r = client.patch(f"{API}/auth/me", headers=a["h"], json={"full_name": "Renamed Admin", "phone": "+971 4 000 0000"})
    assert r.status_code == 200
    me = client.get(f"{API}/auth/me", headers=a["h"]).json()
    assert me["full_name"] == "Renamed Admin" and me["phone"] == "+971 4 000 0000"
    rows = _audit("PROFILE_UPDATED", user_system=a["admin"])
    assert rows and "full_name" in rows[0].detail


def test_an_empty_clinic_shows_nothing_imported(two_orgs):
    a = two_orgs["A"]
    clinic = client.get(f"{API}/clinics/{a['clinics'][0]}", headers=a["h"]).json()
    src = clinic["data_sources"][0]
    assert src["records_count"] == 0 and src["status"] == "Not configured" and src["sha256"] is None


def test_a_connection_is_shown_as_draft_registered_or_verified(two_orgs):
    a = two_orgs["A"]
    body = {"id": "conn_uae", "name": "Gateway", "type": "Clearinghouse", "status": "Draft",
            "clinic_id": a["clinics"][0], "config": {"endpoint": "https://example.org"}}
    d = client.post(f"{API}/reality/connect", headers=a["h"], json=body)
    assert d.status_code == 201 and d.json()["verification"] == "draft"
    cid = d.json()["connector_id"]
    assert cid == f"{a['clinics'][0]}:conn_uae"           # connection ids are per clinic
    again = client.post(f"{API}/reality/connect", headers=a["h"],
                        json={**body, "config": {"endpoint": "https://example.net"}})
    assert again.status_code == 201                       # a draft can be saved again
    health = {c["id"]: c for c in client.get(f"{API}/reality/health", headers=a["h"]).json()}
    assert health[cid]["status"] == "Draft" and health[cid]["last_sync"] is None and health[cid]["latency_ms"] is None
    reg = client.post(f"{API}/reality/connect", headers=a["h"], json={**body, "status": None})
    assert reg.json()["status"] == "Configured" and reg.json()["verification"] == "registered_unverified"
    dup = client.post(f"{API}/reality/connect", headers=a["h"], json={**body, "status": None})
    assert dup.status_code == 409 and "not verified" in dup.json()["detail"]
    # the same name in another organization's clinic is its own connection
    b = two_orgs["B"]
    other = client.post(f"{API}/reality/connect", headers=b["h"], json={**body, "clinic_id": b["clinics"][0]})
    assert other.status_code == 201 and other.json()["connector_id"] != cid
    assert _audit("CONNECTOR_DRAFT_SAVED", target_id=cid) and _audit("CONNECTOR_REGISTERED", target_id=cid)


# ---------------------------------------------------------------- 5. Notifications and audit

def test_a_read_mark_belongs_to_one_user(two_orgs):
    a = two_orgs["A"]
    sid = f"SIG-{_tag()}"
    db = SessionLocal()
    try:
        db.add(SignalModel(id=sid, org_id=a["org"], clinic_id=a["clinics"][0], source="Test", type="Claim",
                           message="Shared alert", timestamp=datetime.utcnow(), risk_level="High"))
        db.commit()
    finally:
        db.close()
    other = f"second-{_tag()}@sentinel-test.com"
    _db_user(other, UserRole.ORGANIZATION_ADMINISTRATOR.value, org_id=a["org"])
    h2 = _h(other)

    def item(h):
        return next(i for i in client.get(f"{API}/notifications", headers=h).json()["items"] if i["id"] == sid)
    assert item(a["h"])["read"] is False and item(h2)["read"] is False
    before = client.get(f"{API}/notifications", headers=h2).json()["unread_count"]
    assert client.post(f"{API}/notifications/{sid}/read", headers=a["h"]).json()["read"] is True
    assert item(a["h"])["read"] is True                    # after a refresh
    assert item(h2)["read"] is False
    assert client.get(f"{API}/notifications", headers=h2).json()["unread_count"] == before
    unread = client.get(f"{API}/notifications", headers=a["h"], params={"status": "unread"}).json()["items"]
    assert sid not in {i["id"] for i in unread}
    # another organization cannot see or mark it
    assert client.post(f"{API}/notifications/{sid}/read", headers=two_orgs["B"]["h"]).status_code == 404


def test_audit_rows_carry_actor_action_and_scope(two_orgs):
    a = two_orgs["A"]
    rows = client.get(f"{API}/audit/", headers=a["h"], params={"action": "CLINIC_CREATED"}).json()
    assert len(rows) == 2
    assert all(r["user_system"] == a["admin"] and r["org_id"] == a["org"] and r["target_type"] == "clinic"
               for r in rows)
    assert {r["clinic_id"] for r in rows} == set(a["clinics"])


def test_the_platform_rules_list_and_toggle_answer(platform):
    """The platform panel loads the rule list with its tenants; it answered 500 (a list read as a dict),
    which left the panel's tenant and user lists empty."""
    rules = client.get(f"{API}/pasme/rules", headers=platform)
    assert rules.status_code == 200, rules.text
    assert isinstance(rules.json(), list)
    if rules.json():
        # The toggle no longer fails on the same list; whether it keeps its state is a separate,
        # older defect (logged in the bug log), so only the answer is checked here.
        rid = rules.json()[0]["rule_id"]
        assert client.patch(f"{API}/pasme/rules/{rid}/toggle", headers=platform).status_code == 200
