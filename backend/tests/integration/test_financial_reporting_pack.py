import pytest
import json
from datetime import datetime, timezone, timedelta
from app.db.database import SessionLocal
from app.models.financial_reporting import (
    FinancialClaimModel,
    FinancialClaimLineModel,
    FinancialSubmissionAttemptModel,
    FinancialRemittanceModel,
    FinancialPaymentReceiptModel,
    FinancialAdjustmentModel,
    ReportSnapshotModel,
)
from app.core.config import settings
from app.services.financial_reporting_service import (
    financial_reporting_service,
    neutralize_formula,
)


@pytest.fixture(autouse=True)
def clean_financial_db(monkeypatch):
    monkeypatch.setattr(settings, "DEPLOYMENT_REGION", "UAE")
    with SessionLocal() as db:
        db.query(ReportSnapshotModel).delete()
        db.query(FinancialAdjustmentModel).delete()
        db.query(FinancialPaymentReceiptModel).delete()
        db.query(FinancialRemittanceModel).delete()
        db.query(FinancialSubmissionAttemptModel).delete()
        db.query(FinancialClaimLineModel).delete()
        db.query(FinancialClaimModel).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(ReportSnapshotModel).delete()
        db.query(FinancialAdjustmentModel).delete()
        db.query(FinancialPaymentReceiptModel).delete()
        db.query(FinancialRemittanceModel).delete()
        db.query(FinancialSubmissionAttemptModel).delete()
        db.query(FinancialClaimLineModel).delete()
        db.query(FinancialClaimModel).delete()
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# RA01: Summary Total Equals Detail Rows
# ─────────────────────────────────────────────────────────────────────────────

def test_ra01_summary_total_equals_detail_rows():
    """Every summary total equals its included detail rows and reconciles to source transactions."""
    clinic = "clinic_dubai_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=7)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        c1 = FinancialClaimModel(
            claim_id="clm_01",
            clinic_id=clinic,
            patient_id="pat_01",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="submitted",
            billed_amount=500.0,
            currency="AED",
            service_date=now - timedelta(days=2),
        )
        c2 = FinancialClaimModel(
            claim_id="clm_02",
            clinic_id=clinic,
            patient_id="pat_02",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="paid",
            billed_amount=350.0,
            paid_amount=350.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        db.add_all([c1, c2])
        db.commit()

    summary = financial_reporting_service.generate_owner_summary(clinic, p_start, p_end)
    assert summary["metrics"]["total_charges"] == 850.0
    assert summary["metrics"]["remittance_reported_payment"] == 350.0
    assert summary["metrics"]["total_claims_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# RA02: Technical Rejection + Correction + Payment = 1 Business Claim
# ─────────────────────────────────────────────────────────────────────────────

def test_ra02_technical_rejection_and_resubmission():
    """Technical rejection followed by correction and payment yields 1 business claim with attempts."""
    clinic = "clinic_dubai_01"
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        claim = FinancialClaimModel(
            claim_id="clm_resub_01",
            clinic_id=clinic,
            patient_id="pat_01",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="paid",
            billed_amount=1000.0,
            paid_amount=1000.0,
            currency="AED",
            service_date=now - timedelta(days=3),
        )
        att1 = FinancialSubmissionAttemptModel(
            attempt_id="att_01",
            claim_id="clm_resub_01",
            attempt_number=1,
            submission_date=now - timedelta(days=3),
            outcome="rejected_technical",
            rejection_reason="Missing DHA Member ID format",
        )
        att2 = FinancialSubmissionAttemptModel(
            attempt_id="att_02",
            claim_id="clm_resub_01",
            attempt_number=2,
            submission_date=now - timedelta(days=1),
            outcome="accepted",
        )
        db.add_all([claim, att1, att2])
        db.commit()

    reg = financial_reporting_service.generate_claim_register(clinic, now - timedelta(days=5), now + timedelta(days=1))
    assert reg["total_claims"] == 1
    row = reg["rows"][0]
    assert row["attempt_count"] == 2
    assert row["status"] == "paid"


# ─────────────────────────────────────────────────────────────────────────────
# RA03: Multi-line Claim Mixed Outcomes (Paid, Partial, Denied)
# ─────────────────────────────────────────────────────────────────────────────

def test_ra03_multiline_mixed_outcomes():
    """Multi-line claim with paid line, partially paid line, and denied line remains mixed without duplicating money."""
    clinic = "clinic_dubai_01"
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        claim = FinancialClaimModel(
            claim_id="clm_multi_01",
            clinic_id=clinic,
            patient_id="pat_01",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="adjudicated",
            billed_amount=1200.0,
            currency="AED",
            service_date=now - timedelta(days=2),
        )
        l1 = FinancialClaimLineModel(
            line_id="l_01", claim_id="clm_multi_01", procedure_code="99213",
            billed_amount=400.0, paid_amount=400.0, line_status="paid"
        )
        l2 = FinancialClaimLineModel(
            line_id="l_02", claim_id="clm_multi_01", procedure_code="71020",
            billed_amount=500.0, paid_amount=300.0, adjusted_amount=100.0, line_status="partially_paid"
        )
        l3 = FinancialClaimLineModel(
            line_id="l_03", claim_id="clm_multi_01", procedure_code="93000",
            billed_amount=300.0, paid_amount=0.0, line_status="denied",
            denial_code="DEN-012", denial_reason="Prior authorization required"
        )
        db.add_all([claim, l1, l2, l3])
        db.commit()

    report = financial_reporting_service.generate_unpaid_procedure_lines(clinic, now - timedelta(days=5), now + timedelta(days=1))
    # Lines 2 (partial) and 3 (denied) appear in unpaid lines report
    assert report["unpaid_lines_count"] == 2
    # Partial supported balance: 500 - 300 - 100 = 100. Denied supported balance: 300. Total = 400.
    assert report["total_unpaid_balance"] == 400.0


# ─────────────────────────────────────────────────────────────────────────────
# RA05: Settlement Reconciliation (Advice vs Clinic Posting vs Bank Receipt)
# ─────────────────────────────────────────────────────────────────────────────

def test_ra05_remittance_and_receipt_reconciliation_no_triple_count():
    """
    RA05: A remittance reporting AED 100, clinic payment posting of AED 100,
    and matching bank receipt of AED 100 shows AED 100 payer-advised and AED 100 received (NOT AED 300).
    """
    clinic = "clinic_dubai_01"
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        rem = FinancialRemittanceModel(
            remittance_id="rem_100",
            clinic_id=clinic,
            payer_id="payer_dha",
            payment_reference="EFT-998877",
            remittance_date=now - timedelta(days=1),
            total_paid=100.0,
            currency="AED",
        )
        receipt = FinancialPaymentReceiptModel(
            receipt_id="rcpt_100",
            clinic_id=clinic,
            receipt_type="payer_payment",
            amount=100.0,
            currency="AED",
            received_date=now - timedelta(days=1),
            remittance_id="rem_100",
            bank_confirmed=True,
        )
        db.add_all([rem, receipt])
        db.commit()

    recon = financial_reporting_service.generate_reconciliation_report(clinic, now - timedelta(days=3), now + timedelta(days=1))
    assert recon["total_payer_advised"] == 100.0
    assert recon["total_cash_received"] == 100.0
    assert recon["reconciled_items"][0]["reconciliation_status"] == "MATCHED"
    assert recon["reconciled_items"][0]["bank_confirmed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# RA08: Snapshot Immutability & Versioned Revisions
# ─────────────────────────────────────────────────────────────────────────────

def test_ra08_snapshot_immutability_and_revision():
    """Reopening an earlier snapshot preserves original rows, totals and basis; updates yield new revision."""
    clinic = "clinic_dubai_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=3)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        c1 = FinancialClaimModel(
            claim_id="clm_snap_01",
            clinic_id=clinic,
            patient_id="pat_01",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="submitted",
            billed_amount=400.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        db.add(c1)
        db.commit()

    # Initial Snapshot (Revision 1)
    s1 = financial_reporting_service.create_snapshot(clinic, "FR01", p_start, p_end)
    assert s1.revision == 1

    # Late source transaction arrives
    with SessionLocal() as db:
        c2 = FinancialClaimModel(
            claim_id="clm_snap_02",
            clinic_id=clinic,
            patient_id="pat_02",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="submitted",
            billed_amount=600.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        db.add(c2)
        db.commit()

    # Second Snapshot (Revision 2)
    s2 = financial_reporting_service.create_snapshot(clinic, "FR01", p_start, p_end)
    assert s2.revision == 2

    # Verification: Old snapshot s1 retains 400.0; new snapshot s2 has 1000.0
    with SessionLocal() as db:
        snap_1_loaded = db.query(ReportSnapshotModel).filter(ReportSnapshotModel.snapshot_id == s1.snapshot_id).first()
        snap_2_loaded = db.query(ReportSnapshotModel).filter(ReportSnapshotModel.snapshot_id == s2.snapshot_id).first()

        p1 = json.loads(snap_1_loaded.payload_json)
        p2 = json.loads(snap_2_loaded.payload_json)

        assert p1["metrics"]["total_charges"] == 400.0
        assert p2["metrics"]["total_charges"] == 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# RA09: Formula Injection Neutralization in CSV Exports
# ─────────────────────────────────────────────────────────────────────────────

def test_ra09_formula_injection_neutralization():
    """Spreadsheet formulas (=, +, -, @) are sanitized with leading quote."""
    assert neutralize_formula("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert neutralize_formula("+100") == "'+100"
    assert neutralize_formula("-50") == "'-50"
    assert neutralize_formula("@SUM") == "'@SUM"
    assert neutralize_formula("NormalText") == "NormalText"
    assert neutralize_formula(123) == "123"
