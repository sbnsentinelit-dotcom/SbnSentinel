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
    FinancialClaimLineModel,
    FinancialSubmissionAttemptModel,
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


def test_unpaid_lines_currency_isolation():
    # FR04: Unpaid procedure lines must include currency on each row and isolate currency totals
    clinic = "clinic_unpaid_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        db.query(FinancialClaimLineModel).filter(
            FinancialClaimLineModel.claim_id.in_(["clm_unp_aed", "clm_unp_usd"])
        ).delete()
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()

        c_aed = FinancialClaimModel(
            claim_id="clm_unp_aed",
            clinic_id=clinic,
            patient_id="pat_u1",
            provider_id="prv_u1",
            payer_id="payer_dha",
            status="submitted",
            billed_amount=500.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        c_usd = FinancialClaimModel(
            claim_id="clm_unp_usd",
            clinic_id=clinic,
            patient_id="pat_u2",
            provider_id="prv_u1",
            payer_id="payer_us",
            status="submitted",
            billed_amount=150.0,
            currency="USD",
            service_date=now - timedelta(days=1),
        )
        db.add_all([c_aed, c_usd])
        db.flush()

        l_aed = FinancialClaimLineModel(
            line_id="line_unp_aed",
            claim_id="clm_unp_aed",
            procedure_code="99213",
            billed_amount=500.0,
            paid_amount=0.0,
            line_status="submitted",
        )
        l_usd = FinancialClaimLineModel(
            line_id="line_unp_usd",
            claim_id="clm_unp_usd",
            procedure_code="99214",
            billed_amount=150.0,
            paid_amount=0.0,
            line_status="submitted",
        )
        db.add_all([l_aed, l_usd])
        db.commit()

    report = financial_reporting_service.generate_unpaid_procedure_lines(clinic, p_start, p_end)
    assert "currency_totals" in report
    assert report["currency_totals"]["AED"] == 500.0
    assert report["currency_totals"]["USD"] == 150.0

    aed_row = next(r for r in report["rows"] if r["claim_id"] == "clm_unp_aed")
    usd_row = next(r for r in report["rows"] if r["claim_id"] == "clm_unp_usd")
    assert aed_row["currency"] == "AED"
    assert usd_row["currency"] == "USD"

    with SessionLocal() as db:
        db.query(FinancialClaimLineModel).filter(
            FinancialClaimLineModel.claim_id.in_(["clm_unp_aed", "clm_unp_usd"])
        ).delete()
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()


def test_denials_and_rejections_currency_isolation():
    # FR05: Denials report must group by currency and isolate currency totals
    clinic = "clinic_denials_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
        db.query(FinancialClaimLineModel).filter(
            FinancialClaimLineModel.claim_id.in_(["clm_den_aed", "clm_den_usd"])
        ).delete()
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()

        c_aed = FinancialClaimModel(
            claim_id="clm_den_aed",
            clinic_id=clinic,
            patient_id="pat_d1",
            provider_id="prv_d1",
            payer_id="payer_allianz",
            status="denied",
            billed_amount=300.0,
            currency="AED",
            service_date=now - timedelta(days=1),
        )
        c_usd = FinancialClaimModel(
            claim_id="clm_den_usd",
            clinic_id=clinic,
            patient_id="pat_d2",
            provider_id="prv_d1",
            payer_id="payer_allianz",
            status="denied",
            billed_amount=200.0,
            currency="USD",
            service_date=now - timedelta(days=1),
        )
        db.add_all([c_aed, c_usd])
        db.flush()

        l_aed = FinancialClaimLineModel(
            line_id="line_den_aed",
            claim_id="clm_den_aed",
            procedure_code="99213",
            billed_amount=300.0,
            line_status="denied",
            denial_code="CO-45",
            denial_reason="Charge exceeds fee schedule",
        )
        l_usd = FinancialClaimLineModel(
            line_id="line_den_usd",
            claim_id="clm_den_usd",
            procedure_code="99214",
            billed_amount=200.0,
            line_status="denied",
            denial_code="CO-45",
            denial_reason="Charge exceeds fee schedule",
        )
        db.add_all([l_aed, l_usd])
        db.commit()

    report = financial_reporting_service.generate_denials_and_rejections(clinic, p_start, p_end)
    assert "currency_totals" in report
    assert report["currency_totals"]["AED"] == 300.0
    assert report["currency_totals"]["USD"] == 200.0

    # Payer Allianz with code CO-45 has separate rows for AED and USD
    rows = [r for r in report["rows"] if r["payer_id"] == "payer_allianz" and r["code"] == "CO-45"]
    assert len(rows) == 2
    aed_r = next(r for r in rows if r["currency"] == "AED")
    usd_r = next(r for r in rows if r["currency"] == "USD")
    assert aed_r["total_denied_amount"] == 300.0
    assert usd_r["total_denied_amount"] == 200.0

    with SessionLocal() as db:
        db.query(FinancialClaimLineModel).filter(
            FinancialClaimLineModel.claim_id.in_(["clm_den_aed", "clm_den_usd"])
        ).delete()
        db.query(FinancialClaimModel).filter(FinancialClaimModel.clinic_id == clinic).delete()
        db.commit()


def test_reconciliation_separate_currency_totals():
    # FR06: Two independently valid settlements, AED 640 and USD 100, must NOT combine into 740
    clinic = "clinic_recon_sep_01"
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

        # Settlement 1: AED 640 advised, AED 640 received
        rem_aed = FinancialRemittanceModel(
            remittance_id="rem_sep_aed",
            clinic_id=clinic,
            payer_id="payer_aed",
            payment_reference="REF_AED",
            remittance_date=now - timedelta(days=1),
            total_paid=640.0,
            currency="AED",
        )
        rcpt_aed = FinancialPaymentReceiptModel(
            receipt_id="rcpt_sep_aed",
            clinic_id=clinic,
            receipt_type="payer_payment",
            amount=640.0,
            currency="AED",
            received_date=now - timedelta(days=1),
            remittance_id="rem_sep_aed",
            bank_confirmed=True,
        )

        # Settlement 2: USD 100 advised, USD 100 received
        rem_usd = FinancialRemittanceModel(
            remittance_id="rem_sep_usd",
            clinic_id=clinic,
            payer_id="payer_usd",
            payment_reference="REF_USD",
            remittance_date=now - timedelta(days=1),
            total_paid=100.0,
            currency="USD",
        )
        rcpt_usd = FinancialPaymentReceiptModel(
            receipt_id="rcpt_sep_usd",
            clinic_id=clinic,
            receipt_type="payer_payment",
            amount=100.0,
            currency="USD",
            received_date=now - timedelta(days=1),
            remittance_id="rem_sep_usd",
            bank_confirmed=True,
        )

        db.add_all([rem_aed, rcpt_aed, rem_usd, rcpt_usd])
        db.commit()

    report = financial_reporting_service.generate_reconciliation_report(clinic, p_start, p_end)
    assert "currency_totals" in report
    assert "AED" in report["currency_totals"]
    assert "USD" in report["currency_totals"]

    assert report["currency_totals"]["AED"]["total_payer_advised"] == 640.0
    assert report["currency_totals"]["AED"]["total_cash_received"] == 640.0

    assert report["currency_totals"]["USD"]["total_payer_advised"] == 100.0
    assert report["currency_totals"]["USD"]["total_cash_received"] == 100.0

    # Ensure total_payer_advised does NOT mix currencies into 740!
    assert report["total_payer_advised"] == 640.0 or report["total_payer_advised"] == 100.0
    assert report["total_payer_advised"] != 740.0

    with SessionLocal() as db:
        db.query(FinancialPaymentReceiptModel).filter(
            FinancialPaymentReceiptModel.clinic_id == clinic
        ).delete()
        db.query(FinancialRemittanceModel).filter(
            FinancialRemittanceModel.clinic_id == clinic
        ).delete()
        db.commit()


def test_technical_rejection_period_filter():
    # FR05: An October technical rejection must NOT appear on the November denials report
    clinic = "clinic_tech_rej_01"
    oct_date = datetime(2026, 10, 15, tzinfo=timezone.utc)
    nov_date = datetime(2026, 11, 15, tzinfo=timezone.utc)
    p_start_nov = datetime(2026, 11, 1, tzinfo=timezone.utc)
    p_end_nov = datetime(2026, 11, 30, tzinfo=timezone.utc)

    with SessionLocal() as db:
        db.query(FinancialSubmissionAttemptModel).filter(
            FinancialSubmissionAttemptModel.attempt_id.in_(["att_oct_01", "att_nov_01"])
        ).delete()
        db.query(FinancialClaimModel).filter(
            FinancialClaimModel.claim_id.in_(["clm_oct_01", "clm_nov_01"])
        ).delete()
        db.commit()

        c_oct = FinancialClaimModel(
            claim_id="clm_oct_01",
            clinic_id=clinic,
            patient_id="pat_oct",
            provider_id="prv_01",
            payer_id="payer_allianz",
            status="rejected_technical",
            billed_amount=300.0,
            currency="AED",
            service_date=oct_date,
        )
        c_nov = FinancialClaimModel(
            claim_id="clm_nov_01",
            clinic_id=clinic,
            patient_id="pat_nov",
            provider_id="prv_01",
            payer_id="payer_allianz",
            status="rejected_technical",
            billed_amount=150.0,
            currency="AED",
            service_date=nov_date,
        )
        db.add_all([c_oct, c_nov])
        db.flush()

        att_oct = FinancialSubmissionAttemptModel(
            attempt_id="att_oct_01",
            claim_id="clm_oct_01",
            attempt_number=1,
            submission_date=oct_date,
            response_date=oct_date,
            outcome="rejected_technical",
            rejection_reason="Format error in header",
        )
        att_nov = FinancialSubmissionAttemptModel(
            attempt_id="att_nov_01",
            claim_id="clm_nov_01",
            attempt_number=1,
            submission_date=nov_date,
            response_date=nov_date,
            outcome="rejected_technical",
            rejection_reason="Format error in header",
        )
        db.add_all([att_oct, att_nov])
        db.commit()

    report_nov = financial_reporting_service.generate_denials_and_rejections(
        clinic, p_start_nov, p_end_nov
    )
    # November report must ONLY contain November rejection (150.0), NOT October (300.0)
    tech_rows = [r for r in report_nov["rows"] if r["code"] == "TECH_REJECT"]
    assert len(tech_rows) == 1
    assert tech_rows[0]["affected_lines_count"] == 1
    assert tech_rows[0]["total_denied_amount"] == 150.0

    with SessionLocal() as db:
        db.query(FinancialSubmissionAttemptModel).filter(
            FinancialSubmissionAttemptModel.attempt_id.in_(["att_oct_01", "att_nov_01"])
        ).delete()
        db.query(FinancialClaimModel).filter(
            FinancialClaimModel.claim_id.in_(["clm_oct_01", "clm_nov_01"])
        ).delete()
        db.commit()


def test_encounter_journey_wait_time_preserves_none():
    from app.models.encounter import EncounterModel
    from app.api.v1.endpoints.encounters import get_encounter_journey
    from unittest.mock import MagicMock

    enc_id = "enc_test_journey_wait_none"
    with SessionLocal() as db:
        db.query(EncounterModel).filter(EncounterModel.id == enc_id).delete()
        db.commit()

        enc = EncounterModel(
            id=enc_id,
            clinic_id="DXB-CLINIC-01",
            patient_id="pat_wait_none",
            provider_id="prv_01",
            date="2026-11-22",
            type="Consultation",
            status="Waiting",
            priority="Normal",
            wait_time=None,  # Not recorded / unavailable
        )
        db.add(enc)
        db.commit()

        mock_user = MagicMock()
        mock_user.role = "System Administrator"
        mock_user.clinic_id = "DXB-CLINIC-01"

        res = get_encounter_journey(encounter_id=enc_id, db=db, current_user=mock_user)
        assert res["wait_time"] is None, f"Expected None for missing wait_time, got {res['wait_time']}"

        # Clean up
        db.query(EncounterModel).filter(EncounterModel.id == enc_id).delete()
        db.commit()


def test_owner_summary_default_currency_from_deployment():
    clinic = "clinic_default_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    # When no currency passed and claims exist, it should use get_deployment_currency()
    res = financial_reporting_service.generate_owner_summary(clinic, p_start, p_end, currency=None)
    assert res["currency"] == get_deployment_currency()


def test_reconciliation_report_default_currency():
    clinic = "clinic_recon_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    res = financial_reporting_service.generate_reconciliation_report(clinic, p_start, p_end, currency=None)
    assert res["currency"] == get_deployment_currency()


def test_signal_model_exposure_attributes():
    from app.models.signal import SignalModel

    sig = SignalModel(
        id="sig_test_exp_01",
        clinic_id="DXB-CLINIC-01",
        estimated_financial_exposure="AED 1,500.00",
        exposure_amount=1500.0,
        exposure_currency="AED",
    )
    assert sig.exposure_amount == 1500.0
    assert sig.exposure_currency == "AED"


if __name__ == "__main__":
    test_currency_mapping_no_db_sniffing()
    test_gender_authoritative_source_preservation()
    test_financial_reporting_isolated_by_currency()
    test_payer_summary_groups_by_currency()
    test_reconciliation_currency_mismatch()
    test_reconciliation_separate_currency_totals()
    test_ageing_report_isolates_currencies()
    test_unpaid_lines_currency_isolation()
    test_denials_and_rejections_currency_isolation()
    test_technical_rejection_period_filter()
    test_encounter_journey_wait_time_preserves_none()
    test_owner_summary_default_currency_from_deployment()
    test_reconciliation_report_default_currency()
    test_signal_model_exposure_attributes()
    print("All QA verification fix tests passed successfully!")
