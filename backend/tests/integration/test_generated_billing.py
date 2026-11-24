"""
Billing of the generated visits: each completed visit follows one of the clinic's workbook claims in its
department (CPT, amount, outcome, submission and payment timing), the status moves with time, a visit
that ended without a consultation has no charge, and a paid claim posts its payment receipt once.
The Revenue Intelligence figures are then calculated from those records.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.db.database import SessionLocal
from app.models.encounter import EncounterModel
from app.models.financial_reporting import FinancialPaymentReceiptModel
from app.models.patient_flow import ClinicRoomModel, VisitEventModel
from app.services import revenue_overview as rev
from app.services.generated_billing import (
    FINAL_STATUSES, NO_CHARGE, VisitBilling, claim_templates, plan_billing, update_generated_billing,
)
from app.core.config import get_deployment_currency
from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY
from app.services.visit_generator import history_days, sync_generated_events

NOW = datetime(2026, 11, 25, 12, 0)  # 16:00 UAE


def test_templates_come_from_the_workbook_claims():
    t = claim_templates()
    assert set(t) == {"ENT", "Cardiology", "Family Medicine", "Pediatrics", "Internal Medicine", "Dermatology",
                      "Orthopedics"}
    every = [x for xs in t.values() for x in xs]
    # Held charges (authorization denied) belong to cancelled visits, which are not billed.
    assert {x.outcome for x in every} == {"Paid", "Claim Denied", "Billed"}
    paid = [x for x in every if x.outcome == "Paid"]
    assert paid and all(x.decision_lag_days is not None and x.paid_amount for x in paid)
    assert all(0 <= x.submit_lag_days <= 5 for x in every if x.submit_lag_days is not None)


def test_a_visit_always_follows_the_same_claim_and_its_status_moves_with_time():
    a = plan_billing("VIS-20261101-007", "ENT", date(2026, 11, 1))
    assert a == plan_billing("VIS-20261101-007", "ENT", date(2026, 11, 1))
    bill = VisitBilling("C", "99213", 200.0, "Paid", date(2026, 11, 3), date(2026, 11, 20), None, 150.0)
    assert [bill.status_on(date(2026, 11, d)) for d in (2, 3, 19, 20)] == ["Pending", "Billed", "Billed", "Paid"]
    held = VisitBilling("C", "99213", 200.0, "Held - Auth Denied", None, None, None)
    assert held.status_on(date(2026, 11, 1)) == "Held - Auth Denied"


def test_history_continues_from_the_workbook():
    assert history_days(date(2026, 11, 24)) == 45  # workbook's last visit: 2026-10-10
    assert history_days(date(2026, 10, 12)) == 14
    assert history_days(date(2027, 6, 1)) == 60


@pytest.fixture
def clinic():
    clinic_id = f"BILL-{uuid.uuid4().hex[:8]}"
    yield clinic_id
    db = SessionLocal()
    for model in (VisitEventModel, EncounterModel, ClinicRoomModel, FinancialPaymentReceiptModel):
        db.query(model).filter(model.clinic_id == clinic_id).delete(synchronize_session=False)
    db.commit()
    db.close()


def test_generated_visits_are_billed_and_paid_claims_post_receipts(clinic):
    db = SessionLocal()
    try:
        sync_generated_events(db, clinic, now=NOW, days=35)
        visits = db.query(EncounterModel).filter(EncounterModel.clinic_id == clinic).all()
        ended = {v for (v,) in db.query(VisitEventModel.visit_id).filter(
            VisitEventModel.clinic_id == clinic, VisitEventModel.event_type == "CHECKED_OUT").all()}
        left = {v for (v,) in db.query(VisitEventModel.visit_id).filter(
            VisitEventModel.clinic_id == clinic, VisitEventModel.event_type == "CANCELLED").all()}
        statuses = {e.billing_status for e in visits}
        assert {"Paid", "Billed", NO_CHARGE} <= statuses
        for e in visits:
            if e.id in left:
                assert e.billing_status == NO_CHARGE and e.billing_amount is None
            elif e.id in ended:
                plan = plan_billing(e.id, e.department, date.fromisoformat(e.date))
                assert (e.cpt_code, e.billing_amount) == (plan.cpt_code, plan.amount)
                assert e.billing_status == plan.status_on(DEFAULT_FLOW_POLICY.local_date(NOW))
            else:
                assert e.billing_status == "Pending" and e.billing_amount is None  # still in the clinic
        paid = [e for e in visits if e.billing_status == "Paid"]
        receipts = db.query(FinancialPaymentReceiptModel).filter(FinancialPaymentReceiptModel.clinic_id == clinic).all()
        assert {r.claim_id for r in receipts} == {e.id for e in paid}
        assert all(r.received_date <= NOW for r in receipts)

        # Nothing changes when nothing new happened; receipts are posted once.
        assert update_generated_billing(db, clinic, NOW) == 0
        assert db.query(FinancialPaymentReceiptModel).filter(
            FinancialPaymentReceiptModel.clinic_id == clinic).count() == len(receipts)
        assert all(e.billing_status in FINAL_STATUSES or e.billing_status in ("Pending", "Billed") for e in visits)

        # The month's figures are calculated from these records.
        q = db.query(EncounterModel).filter(EncounterModel.clinic_id == clinic)
        month = rev.revenue_overview(q, "month", DEFAULT_FLOW_POLICY.local_date(NOW), get_deployment_currency())
        start = DEFAULT_FLOW_POLICY.local_date(NOW) - timedelta(days=29)  # the last 30 days
        in_month = [e for e in visits if start.isoformat() <= e.date and e.billing_status != NO_CHARGE]
        assert month["total_claims"] == len(in_month)
        assert month["cards"]["charges"]["value"] == round(sum(e.billing_amount or 0 for e in in_month), 2)
        expected_paid = sum(r.amount for r in receipts
                            if (r.received_date + timedelta(hours=4)).date() >= start)
        assert month["cards"]["paid_amount"]["value"] == round(expected_paid, 2) and expected_paid > 0
        assert len({p["charges"] for p in month["series"]}) > 5  # the chart has a shape, not a flat line
    finally:
        db.close()
