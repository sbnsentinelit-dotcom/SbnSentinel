import os
import sys

# Add runtime directory to sys.path so the module can run directly or via pytest
_runtime = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "runtime"))
if _runtime not in sys.path:
    sys.path.insert(0, _runtime)

from datetime import datetime, timezone, timedelta  # noqa: E402
from app.core.config import get_deployment_currency  # noqa: E402
from app.api.v1.endpoints.patients import _resolve_gender  # noqa: E402
from app.services.financial_reporting_service import financial_reporting_service  # noqa: E402
from app.models.financial_reporting import (  # noqa: E402
    FinancialClaimModel,
    FinancialRemittanceModel,
    FinancialPaymentReceiptModel,
)
from app.db.database import SessionLocal  # noqa: E402


def test_currency_mapping_no_db_sniffing():
    # 1. Strict DEPLOYMENT_REGION mapping without DB sniffing
    assert get_deployment_currency("UAE") == "AED"
    assert get_deployment_currency("US") == "USD"
    assert get_deployment_currency("other") == "USD"


def test_gender_authoritative_source_preservation():
    # 2. Authoritative source preservation: raw_gender overrides name match
    assert _resolve_gender("Mariam", "M") == "M"
    assert _resolve_gender("Mariam", "Male") == "M"
    assert _resolve_gender("Khalid", "F") == "F"
    assert _resolve_gender("Khalid", "Female") == "F"

    # Missing source data is NOT inferred from name; returns 'Unknown'
    assert _resolve_gender("Mariam", None) == "Unknown"
    assert _resolve_gender("Mariam", "") == "Unknown"
    assert _resolve_gender("Khalid", None) == "Unknown"
    assert _resolve_gender("Khalid", "") == "Unknown"

    # 'Other', 'Unknown', 'U' maps to 'Unknown'
    assert _resolve_gender("Mariam", "Other") == "Unknown"
    assert _resolve_gender("Khalid", "UNKNOWN") == "Unknown"
    assert _resolve_gender("RandomName", "U") == "Unknown"
    assert _resolve_gender("RandomNonExistingName", None) == "Unknown"


def test_financial_reporting_isolated_by_currency():
    # 3. Financial reporting isolates totals by currency (no mixing AED 640 + USD 100 = 740)
    clinic = "clinic_mixed_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()

        c_aed = FinancialClaimModel(
            claim_id="clm_mix_aed",
            clinic_id=clinic,
            patient_id="pat_01",
            provider_id="prv_01",
            payer_id="payer_dha",
            status="submitted",
            billed_amount=640.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        c_usd = FinancialClaimModel(
            claim_id="clm_mix_usd",
            clinic_id=clinic,
            patient_id="pat_02",
            provider_id="prv_01",
            payer_id="payer_us",
            status="submitted",
            billed_amount=100.0,
            currency="USD",
            service_date=now - timedelta(days=1),
        )
        db.add_all([c_aed, c_usd])
        db.commit()

    # Query for AED
    summary_aed = financial_reporting_service.generate_owner_summary(clinic, p_start, p_end, currency="AED")
    assert summary_aed["currency"] == "AED"
    assert summary_aed["metrics"]["total_charges"] == 640.0
    assert summary_aed["metrics"]["total_claims_count"] == 1

    # Query for USD
    summary_usd = financial_reporting_service.generate_owner_summary(clinic, p_start, p_end, currency="USD")
    assert summary_usd["currency"] == "USD"
    assert summary_usd["metrics"]["total_charges"] == 100.0
    assert summary_usd["metrics"]["total_claims_count"] == 1

    # Check currency_totals breakdown isolates each
    assert "AED" in summary_aed["currency_totals"]
    assert "USD" in summary_aed["currency_totals"]
    assert summary_aed["currency_totals"]["AED"]["total_charges"] == 640.0
    assert summary_aed["currency_totals"]["USD"]["total_charges"] == 100.0

    # Clean up test rows
    with SessionLocal() as db:
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()


def test_payer_summary_groups_by_currency():
    # FR02: Same payer with AED 640 and USD 100 must produce 2 distinct rows, not combine into AED 740
    clinic = "clinic_payer_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()

        c_aed = FinancialClaimModel(
            claim_id="clm_pyr_aed",
            clinic_id=clinic,
            patient_id="pat_p1",
            provider_id="prv_p1",
            payer_id="payer_allianz",
            status="submitted",
            billed_amount=640.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        c_usd = FinancialClaimModel(
            claim_id="clm_pyr_usd",
            clinic_id=clinic,
            patient_id="pat_p2",
            provider_id="prv_p1",
            payer_id="payer_allianz",
            status="submitted",
            billed_amount=100.0,
            currency="USD",
            service_date=now - timedelta(days=1),
        )
        db.add_all([c_aed, c_usd])
        db.commit()

    # Query without filter: returns 2 separate groups for payer_allianz
    res_all = financial_reporting_service.generate_payer_plan_summary(clinic, p_start, p_end)
    payers_all = [p for p in res_all["rows"] if p["payer_id"] == "payer_allianz"]
    assert len(payers_all) == 2, f"Expected 2 separate payer entries for different currencies, got {len(payers_all)}"
    aed_entry = next(p for p in payers_all if p["currency"] == "AED")
    usd_entry = next(p for p in payers_all if p["currency"] == "USD")
    assert aed_entry["billed_total"] == 640.0
    assert usd_entry["billed_total"] == 100.0

    # Query with currency="AED": returns only AED entry
    res_aed = financial_reporting_service.generate_payer_plan_summary(clinic, p_start, p_end, currency="AED")
    payers_aed = [p for p in res_aed["rows"] if p["payer_id"] == "payer_allianz"]
    assert len(payers_aed) == 1
    assert payers_aed[0]["currency"] == "AED"
    assert payers_aed[0]["billed_total"] == 640.0

    with SessionLocal() as db:
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()


def test_reconciliation_currency_mismatch():
    # FR06: Remittance with AED 500 and receipt with USD 500 must produce CURRENCY_MISMATCH
    clinic = "clinic_recon_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        db.query(FinancialPaymentReceiptModel).filter(
            FinancialPaymentReceiptModel.clinic_id == clinic
        ).delete()
        db.query(FinancialRemittanceModel).filter(
            FinancialRemittanceModel.clinic_id == clinic
        ).delete()
        db.commit()

        rem = FinancialRemittanceModel(
            remittance_id="rem_test_curr_01",
            clinic_id=clinic,
            payer_id="payer_test",
            payment_reference="REF_12345",
            remittance_date=now - timedelta(days=1),
            total_paid=500.0,
            currency="AED",
        )
        rcpt = FinancialPaymentReceiptModel(
            receipt_id="rcpt_test_curr_01",
            clinic_id=clinic,
            receipt_type="payer_payment",
            amount=500.0,
            currency="USD",
            received_date=now - timedelta(days=1),
            remittance_id="rem_test_curr_01",
            bank_confirmed=True,
        )
        db.add_all([rem, rcpt])
        db.commit()

    report = financial_reporting_service.generate_reconciliation_report(clinic, p_start, p_end)
    item = next(
        (i for i in report["reconciled_items"] if i["remittance_id"] == "rem_test_curr_01"),
        None,
    )
    assert item is not None
    assert item["reconciliation_status"] == "CURRENCY_MISMATCH"
    assert item["remittance_currency"] == "AED"
    assert item["receipt_currency"] == "USD"

    with SessionLocal() as db:
        db.query(FinancialPaymentReceiptModel).filter(
            FinancialPaymentReceiptModel.clinic_id == clinic
        ).delete()
        db.query(FinancialRemittanceModel).filter(
            FinancialRemittanceModel.clinic_id == clinic
        ).delete()
        db.commit()


def test_ageing_report_isolates_currencies():
    # FR07: Separate ageing buckets and totals by currency
    clinic = "clinic_ageing_curr_01"
    now = datetime.now(timezone.utc)
    as_of = now

    with SessionLocal() as db:
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()

        c_aed = FinancialClaimModel(
            claim_id="clm_age_aed",
            clinic_id=clinic,
            patient_id="pat_a1",
            provider_id="prv_a1",
            payer_id="payer_aed",
            status="submitted",
            billed_amount=400.0,
            currency="AED",
            service_date=now - timedelta(days=15),
        )
        c_usd = FinancialClaimModel(
            claim_id="clm_age_usd",
            clinic_id=clinic,
            patient_id="pat_u1",
            provider_id="prv_u1",
            payer_id="payer_usd",
            status="submitted",
            billed_amount=250.0,
            currency="USD",
            service_date=now - timedelta(days=45),
        )
        db.add_all([c_aed, c_usd])
        db.commit()

    report = financial_reporting_service.generate_ageing_report(clinic, as_of_cutoff=as_of)
    assert "currency_buckets" in report
    assert "currency_totals" in report
    assert "AED" in report["currency_buckets"]
    assert "USD" in report["currency_buckets"]
    assert report["currency_totals"]["AED"] == 400.0
    assert report["currency_totals"]["USD"] == 250.0

    with SessionLocal() as db:
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()


if __name__ == "__main__":
    test_currency_mapping_no_db_sniffing()
    test_gender_authoritative_source_preservation()
    test_financial_reporting_isolated_by_currency()
    test_payer_summary_groups_by_currency()
    test_reconciliation_currency_mismatch()
    test_ageing_report_isolates_currencies()
    print("All QA verification fix tests passed successfully!")
