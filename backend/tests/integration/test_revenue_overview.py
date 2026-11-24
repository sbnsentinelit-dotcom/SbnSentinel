"""
Revenue Intelligence screen: every card, trend, chart point and ring segment is calculated from the
clinic's encounters for the selected period, and the export returns every matching record (not one
page). Also the signal card's revenue fields: a denied claim carries its own risk category, confidence
and operational dependency; the missed-appointment wording is used for no-shows only.
"""
import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user
from app.db.database import Base, SessionLocal
from app.main import app
from app.models.encounter import EncounterModel
from app.models.financial_reporting import FinancialPaymentReceiptModel
from app.models.signal import SignalModel
from app.models.user import User, UserRole
from app.services import revenue_overview as rev
from app.services.revenue_intelligence_engine import SLOT_DEPENDENCY, dependency_for_signal, revenue_fields

client = TestClient(app)

TODAY = date(2026, 11, 25)
CLAIM_DEPENDENCY = "Billing team must correct and resubmit or appeal the claim within the payer window."


def _enc(clinic, day, status, amount, n=[0]):
    n[0] += 1
    return EncounterModel(id=f"REV-{clinic}-{n[0]}", patient_id=f"Patient {n[0]}", patient_ref=f"PAT-T{n[0]:04d}",
                          provider_id="Dr. Test", clinic_id=clinic, date=day.isoformat(), type="Consultation",
                          billing_status=status, billing_amount=amount, cpt_code="99213", payer_network="Daman")


@pytest.fixture
def clinic():
    clinic_id = f"REV-{uuid.uuid4().hex[:8]}"
    yield clinic_id
    app.dependency_overrides.clear()
    db = SessionLocal()
    db.query(EncounterModel).filter(EncounterModel.clinic_id.in_([clinic_id, clinic_id + "-OTHER"])).delete(
        synchronize_session=False)
    db.query(FinancialPaymentReceiptModel).filter(FinancialPaymentReceiptModel.clinic_id == clinic_id).delete(
        synchronize_session=False)
    db.commit()
    db.close()


def _seed_fixed(db, clinic):
    d = TODAY
    rows = [
        (d, "Paid", 100.0), (d, "Claim Denied", 50.0), (d, "Pending", None),
        (d - timedelta(days=1), "Billed", 200.0), (d - timedelta(days=1), "Held - Auth Denied", 30.0),
        (d - timedelta(days=2), "Paid", 300.0),
        (date(2026, 11, 18), "Paid", 200.0), (date(2026, 11, 18), "Claim Denied", 20.0),  # the 7 days before
        (date(2026, 11, 10), "Settled", 400.0),                                              # last 30 days only
        (date(2026, 10, 20), "Paid", 100.0),                                                 # the 30 days before
    ]
    for day, status, amount in rows:
        db.add(_enc(clinic, day, status, amount))
    # Payments received (local noon), e.g. a payer paying for an earlier visit.
    for i, (day, amount) in enumerate(((d, 100.0), (d - timedelta(days=2), 300.0), (date(2026, 11, 18), 200.0),
                                       (date(2026, 11, 10), 400.0), (date(2026, 10, 20), 100.0))):
        db.add(_receipt(clinic, f"{clinic}-R{i}", day, amount))
    db.commit()


def _receipt(clinic, rid, day, amount):
    from datetime import datetime
    return FinancialPaymentReceiptModel(receipt_id=rid, clinic_id=clinic, receipt_type="payer_payment",
                                        amount=amount, currency="AED", bank_confirmed=True,
                                        received_date=datetime.combine(day, datetime.min.time()) + timedelta(hours=8))


def _q(db, clinic):
    return db.query(EncounterModel).filter(EncounterModel.clinic_id == clinic)


def test_periods_end_today_and_compare_with_the_period_before():
    assert rev.period_bounds("today", TODAY) == (TODAY, TODAY, date(2026, 11, 24), date(2026, 11, 24))
    assert rev.period_bounds("week", TODAY) == (date(2026, 11, 19), TODAY, date(2026, 11, 12), date(2026, 11, 18))
    assert rev.period_bounds("month", TODAY) == (date(2026, 10, 27), TODAY, date(2026, 9, 27), date(2026, 10, 26))
    assert rev.normalize_period("This Week") == "week"
    with pytest.raises(ValueError):
        rev.normalize_period("year")


def test_week_cards_ring_and_chart_come_from_the_encounters(clinic):
    db = SessionLocal()
    try:
        _seed_fixed(db, clinic)
        o = rev.revenue_overview(_q(db, clinic), "week", TODAY, "AED")
        c = o["cards"]
        assert c["charges"]["value"] == 680.0 and c["paid_amount"]["value"] == 400.0
        assert c["pending"]["value"] == 2 and c["denied"]["value"] == 1
        # The 7 days before (Nov 12-18): charges 220, payments received 200, one denial.
        assert c["charges"]["trend"] == "+209.1%" and c["charges"]["good"] is True
        assert c["paid_amount"]["trend"] == "+100.0%"
        assert c["denied"]["trend"] == "+0.0%" and c["denied"]["good"] is None
        assert c["pending"]["trend"] is None  # nothing pending last week: no percentage to show
        # Ring: every claim in the period once; the held claim is its own segment; 100% in total.
        assert o["total_claims"] == 6
        assert [(s["label"], s["count"], s["pct"]) for s in o["status_breakdown"]] == [
            ("Paid", 2, 33), ("Pending", 2, 33), ("Denied", 1, 17), ("Held - Auth Denied", 1, 17)]
        # Chart: one point per day; the points add up to the revenue card.
        assert [p["date"] for p in o["series"]] == [f"2026-11-{d}" for d in range(19, 26)]
        assert [p["charges"] for p in o["series"]] == [0.0, 0.0, 0.0, 0.0, 300.0, 230.0, 150.0]
        assert sum(p["charges"] for p in o["series"]) == c["charges"]["value"]
        assert [p["paid_amount"] for p in o["series"]] == [0.0, 0.0, 0.0, 0.0, 300.0, 0.0, 100.0]  # received
        assert c["charges"]["spark"] == [0.0, 0.0, 0.0, 0.0, 300.0, 230.0, 150.0]
    finally:
        db.close()


def test_today_and_month_follow_the_filter(clinic, monkeypatch):
    monkeypatch.setattr(rev, "clinic_offset", lambda: timedelta(hours=4))  # a UAE clinic
    db = SessionLocal()
    try:
        _seed_fixed(db, clinic)
        from datetime import datetime
        today = rev.revenue_overview(_q(db, clinic), "today", TODAY, "AED", now_utc=datetime(2026, 11, 25, 8, 30))
        # Today's chart: one point per clinic hour so far (08:00 .. 12:00 UAE); the hours add up to the cards.
        assert today["total_claims"] == 3 and [p["hour"] for p in today["series"]] == [
            "08:00", "09:00", "10:00", "11:00", "12:00"]
        assert sum(p["charges"] for p in today["series"]) == 150.0
        assert [p["paid_amount"] for p in today["series"]] == [0.0, 0.0, 0.0, 0.0, 100.0]  # received at noon
        assert today["cards"]["charges"]["value"] == 150.0 and today["cards"]["paid_amount"]["value"] == 100.0
        assert today["cards"]["paid_amount"]["trend"] is None  # nothing was paid yesterday
        month = rev.revenue_overview(_q(db, clinic), "month", TODAY, "AED")
        assert month["cards"]["charges"]["value"] == 1300.0 and month["cards"]["paid_amount"]["value"] == 1000.0
        assert month["cards"]["paid_amount"]["trend"] == "+900.0%"
        assert len(month["series"]) == 30 and month["total_claims"] == 9
        assert sum(s["pct"] for s in month["status_breakdown"]) == 100
    finally:
        db.close()


def test_percentages_always_add_up_to_100():
    assert rev.percentages([1, 1, 1]) == [34, 33, 33]
    assert rev.percentages([0, 0]) == [0, 0]
    assert sum(rev.percentages([326, 984, 161, 23])) == 100


def _login(clinic_id):
    def user():
        return User(id=1, email="manager@test.local", role=UserRole.CLINIC_MANAGER.value, org_id="ORG-REV",
                    clinic_id=clinic_id, is_active=True, hashed_password="x")
    app.dependency_overrides[get_current_user] = user


def test_api_overview_table_and_export_use_the_same_records(clinic):
    today = rev.clinic_today()
    db = SessionLocal()
    for status, amount in (("Paid", 100.0), ("Claim Denied", 50.0), ("Held - Auth Denied", 30.0)):
        db.add(_enc(clinic, today, status, amount))
    db.add(_enc(clinic + "-OTHER", today, "Claim Denied", 999.0))  # another clinic: never shown
    db.commit()
    db.close()
    _login(clinic)

    o = client.get("/api/v1/encounters/revenue-overview?period=today").json()
    assert o["total_claims"] == 3 and o["cards"]["charges"]["value"] == 180.0 and o["cards"]["denied"]["value"] == 1

    page = client.get("/api/v1/encounters?page=1&page_size=15&period=today")
    assert page.headers["X-Total-Count"] == "3"
    denied = client.get("/api/v1/encounters?page=1&page_size=15&period=today&billing_status=denied")
    assert denied.headers["X-Total-Count"] == "1" and denied.json()[0]["billing_status"] == "Claim Denied"

    def export(status):
        res = client.get(f"/api/v1/encounters/export?period=today&billing_status={status}")
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
        return res, list(csv.reader(io.StringIO(res.text)))

    res, rows = export("all")
    assert rows[0] == list(rev.EXPORT_COLUMNS) and len(rows) == 4
    assert "_today_" in res.headers["content-disposition"] and res.headers["content-disposition"].endswith('_all.csv"')
    _res, rows = export("denied")
    assert len(rows) == 2 and rows[1][8] == "Claim Denied" and rows[1][6] == "50.00"
    _res, rows = export("Held - Auth Denied")
    assert len(rows) == 2 and rows[1][8] == "Held - Auth Denied"
    assert client.get("/api/v1/encounters/export?period=year").status_code == 422


def test_export_is_not_limited_to_one_page(clinic):
    today = rev.clinic_today()
    db = SessionLocal()
    for _ in range(40):
        db.add(_enc(clinic, today, "Paid", 10.0))
    db.commit()
    db.close()
    _login(clinic)
    rows = list(csv.reader(io.StringIO(client.get("/api/v1/encounters/export?period=today").text)))
    assert len(rows) == 41


# ---- Signal card: risk category, dependency, verification -------------------------------------

def test_dependency_comes_from_the_signal_rule():
    assert revenue_fields("RULE-UAE-CO4")["revenue_risk_category"] == "Claim Denial Revenue Risk"
    assert dependency_for_signal("claim_denial") == CLAIM_DEPENDENCY
    assert dependency_for_signal("no_show") == SLOT_DEPENDENCY
    assert dependency_for_signal("wait_time") == "Queue congestion impacting daily clinic schedule."
    assert dependency_for_signal(None, "Patient appointment missed") == SLOT_DEPENDENCY
    assert dependency_for_signal(None, "Lab result pending") is None
    assert revenue_fields("RULE-SCH-001")["operational_dependency"] == SLOT_DEPENDENCY


def test_seeded_claim_denials_carry_category_confidence_and_dependency(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        from app.services.dubai_seed_service import repair_seeded_revenue_fields, seed_dubai_data
        seed_dubai_data(db)
        claims = db.query(SignalModel).filter(SignalModel.id.like("SIG-DXB-2026-%")).all()
        assert claims and all(s.revenue_risk_category == "Claim Denial Revenue Risk" for s in claims)
        assert all(s.revenue_confidence == "HIGH" and s.operational_dependency == CLAIM_DEPENDENCY for s in claims)
        # Rows stored by an older seed (no category) are filled once at startup.
        for s in claims:
            s.revenue_risk_category = s.revenue_confidence = s.operational_dependency = None
        db.commit()
        assert repair_seeded_revenue_fields(db) == len(claims)
        assert repair_seeded_revenue_fields(db) == 0
        s = db.get(SignalModel, "SIG-DXB-2026-02")
        assert (s.revenue_risk_category, s.revenue_confidence, s.operational_dependency) == (
            "Claim Denial Revenue Risk", "HIGH", CLAIM_DEPENDENCY)
    finally:
        db.close()
