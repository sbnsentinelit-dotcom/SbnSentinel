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


def test_workbook_profile_isolation_and_no_practice_fusion():
    from app.core.config import settings
    from app.api.v1.endpoints.settings import get_integrations
    from unittest.mock import MagicMock

    assert settings.SOURCE_PROFILE == "workbook"
    assert settings.ENABLE_PRACTICE_FUSION is False
    assert settings.REQUIRED_CONNECTORS == []

    mock_user = MagicMock()
    mock_user.clinic_id = "DXB-CLINIC-01"
    mock_db = MagicMock()

    # In workbook profile, integrations must be empty
    integrations = get_integrations(current_user=mock_user, db=mock_db)
    assert integrations == [], f"Expected empty integrations in workbook profile, got {integrations}"


def test_workbook_clinics_endpoint_receipt_and_connectors():
    from app.api.v1.endpoints.clinics import get_clinics
    from app.models.clinic import ClinicModel
    from unittest.mock import MagicMock

    mock_user = MagicMock()
    mock_user.clinic_id = "DXB-CLINIC-01"
    mock_user.role = "System Administrator"

    with SessionLocal() as db:
        if not db.query(ClinicModel).filter(ClinicModel.id == "DXB-CLINIC-01").first():
            db.add(ClinicModel(id="DXB-CLINIC-01", name="Dubai Clinic 01", status="Active"))
            db.commit()
        clinics = get_clinics(db=db, current_user=mock_user)
        assert len(clinics) > 0
        clinic = clinics[0]
        assert clinic.get("external_connectors") == []
        assert isinstance(clinic.get("data_sources"), list)
        assert len(clinic["data_sources"]) > 0
        ds = clinic["data_sources"][0]
        assert ds.get("kind") == "workbook"
        assert ds.get("records_count") == 789
        assert "sha256" in ds


def test_signals_verifiable_origin_in_workbook_profile():
    from app.api.v1.endpoints.signals import get_historical_signals
    from unittest.mock import MagicMock

    mock_user = MagicMock()
    mock_user.clinic_id = "DXB-CLINIC-01"
    mock_user.org_id = "ORG-UAE-01"
    mock_user.role = "Clinic Manager"

    with SessionLocal() as db:
        signals = get_historical_signals(db=db, current_user=mock_user)
        # Any signal present must not have Practice Fusion as source
        for sig in signals:
            source = sig.get("source", "") if isinstance(sig, dict) else getattr(sig, "source", "")
            assert "practice" not in source.lower(), f"Unexpected practice source in signal: {source}"
            origin = sig.get("origin") if isinstance(sig, dict) else getattr(sig, "origin", None)
            if origin:
                assert origin.get("kind") in ("workbook", "rule_engine", "synthetic")
                if origin.get("kind") == "workbook":
                    assert origin.get("import_id") == "REC-WB-DXB-01"
                    assert origin.get("connector_instance_id") is None


def test_report_01_simulator_opt_in():
    from app.core.config import settings
    from app.services.simulation_engine import simulation_engine
    assert getattr(settings, "ENABLE_LIVE_SIMULATION", None) is False
    # Calling start() must not start simulation when ENABLE_LIVE_SIMULATION is False
    simulation_engine.start()
    assert simulation_engine.running is False


def test_report_01_check_2_provider_scope_and_date_cutoff():
    from app.models.signal import SignalModel
    from app.api.v1.endpoints.signals import get_signal_summary
    from unittest.mock import MagicMock

    clinic = "DXB-CLINIC-R01"
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    with SessionLocal() as db:
        try:
            db.query(SignalModel).filter(SignalModel.clinic_id == clinic).delete()
            db.commit()

            # Provider A: 1 yesterday, 1 today
            s1 = SignalModel(
                id="sig_r01_a1", clinic_id=clinic, org_id="ORG-TEST", source="Test", type="Claim",
                timestamp=yesterday,
                metadata_data={"provider": "Dr. Sarah Mitchell", "review_state": {"status": "active"}}
            )
            s2 = SignalModel(
                id="sig_r01_a2", clinic_id=clinic, org_id="ORG-TEST", source="Test", type="Claim",
                timestamp=now,
                metadata_data={"provider": "Dr. Sarah Mitchell", "review_state": {"status": "active"}}
            )
            # Provider B: 1 today
            s3 = SignalModel(
                id="sig_r01_b1", clinic_id=clinic, org_id="ORG-TEST", source="Test", type="Claim",
                timestamp=now,
                metadata_data={"provider": "Dr. James Okafor", "review_state": {"status": "active"}}
            )
            db.add_all([s1, s2, s3])
            db.commit()

            mock_user = MagicMock()
            mock_user.clinic_id = clinic
            mock_user.org_id = "ORG-TEST"
            mock_user.role = "Clinic Manager"

            # Check: All Providers = 3
            all_sum = get_signal_summary(provider="All Providers", db=db, current_user=mock_user)
            assert all_sum["active_signals"] == 3

            # Check: Provider A = 2
            prov_a_sum = get_signal_summary(provider="Dr. Sarah Mitchell", db=db, current_user=mock_user)
            assert prov_a_sum["active_signals"] == 2

            # Check: Provider B = 1
            prov_b_sum = get_signal_summary(provider="Dr. James Okafor", db=db, current_user=mock_user)
            assert prov_b_sum["active_signals"] == 1

            # Check: Unmatched Provider = 0
            prov_c_sum = get_signal_summary(provider="Dr. Emily Chen", db=db, current_user=mock_user)
            assert prov_c_sum["active_signals"] == 0

            # Check date cutoff: Cutoff right after yesterday's signal
            cutoff_past = (yesterday + timedelta(minutes=5)).isoformat()
            past_sum = get_signal_summary(cutoff=cutoff_past, db=db, current_user=mock_user)
            assert past_sum["active_signals"] == 1
        finally:
            db.query(SignalModel).filter(SignalModel.clinic_id == clinic).delete()
            db.commit()


def test_report_01_check_3_trend_and_freshness():
    from app.api.v1.endpoints.signals import get_signal_summary
    from app.models.signal import SignalModel
    from unittest.mock import MagicMock

    clinic = "DXB-CLINIC-R02"
    now = datetime.now(timezone.utc)
    yesterday_same_time = now - timedelta(days=1)

    with SessionLocal() as db:
        try:
            db.query(SignalModel).filter(SignalModel.clinic_id == clinic).delete()
            db.commit()

            # Create 100 open signals yesterday
            sigs_yesterday = [
                SignalModel(
                    id=f"sig_trend_{i}", clinic_id=clinic, org_id="ORG-TEST", source="Test", type="Claim",
                    timestamp=yesterday_same_time - timedelta(minutes=i),
                    metadata_data={"review_state": {"status": "active"}}
                )
                for i in range(100)
            ]
            # Create 1 new open signal today -> total 101 today
            sig_today = SignalModel(
                id="sig_trend_101", clinic_id=clinic, org_id="ORG-TEST", source="Test", type="Claim",
                timestamp=now,
                metadata_data={"review_state": {"status": "active"}}
            )
            db.add_all(sigs_yesterday + [sig_today])
            db.commit()

            mock_user = MagicMock()
            mock_user.clinic_id = clinic
            mock_user.org_id = "ORG-TEST"
            mock_user.role = "Clinic Manager"

            res = get_signal_summary(db=db, current_user=mock_user)
            assert res["active_signals"] == 101
            assert res["prior_active_signals"] == 100
            # 100 prior vs 101 current must produce +1%
            assert res["trend"] == "+1%"
            assert res["trend_up"] is True
            assert "GST" in res["clinic_timestamp"] or "UTC" in res["clinic_timestamp"]
        finally:
            db.query(SignalModel).filter(SignalModel.clinic_id == clinic).delete()
            db.commit()


def test_report_03_problem_reason_and_operational_dependency_defaults():
    from app.api.v1.endpoints.signals import get_historical_signals
    from app.models.signal import SignalModel
    from unittest.mock import MagicMock

    clinic = "DXB-CLINIC-R03"
    with SessionLocal() as db:
        try:
            db.query(SignalModel).filter(SignalModel.clinic_id == clinic).delete()
            db.commit()

            sig = SignalModel(
                id="sig_test_r03_01",
                clinic_id=clinic,
                org_id="ORG-TEST",
                source="Test",
                type="EHR",
                message="Patient appointment missed",
                problem=None,
                reason=None,
                operational_dependency=None,
                metadata_data={"review_state": {"status": "active"}}
            )
            db.add(sig)
            db.commit()

            mock_user = MagicMock()
            mock_user.clinic_id = clinic
            mock_user.org_id = "ORG-TEST"
            mock_user.role = "Clinic Manager"

            signals = get_historical_signals(db=db, current_user=mock_user)
            found = [s for s in signals if s.get("id") == "sig_test_r03_01"]
            assert len(found) == 1
            s = found[0]
            # Problem and reason must not be None or 'None' (Report 03 §03.3 & §03D.1)
            assert s.get("problem") == "Appointment marked as no-show"
            assert s.get("reason") == "Reason unavailable"
            assert s.get("operational_dependency") == "Depends on whether the slot was filled. Status unverified"
        finally:
            db.query(SignalModel).filter(SignalModel.clinic_id == clinic).delete()
            db.commit()


def test_report_03_organization_admin_authority_policy():
    from app.services.governance_registry import governance_registry, DecisionType
    config = governance_registry.get_authority_config("Organization Administrator")
    assert config is not None
    assert DecisionType.APPROVED in config.allowed_decisions
    assert DecisionType.RETURNED_FOR_REVIEW in config.allowed_decisions


def test_report_05_journey_mrn_crosswalk():
    """Report 05 §05.5: Encounter journey crosswalks patient to true workbook MRN (MRN-000001)."""
    from app.db.database import SessionLocal
    from app.models.encounter import EncounterModel
    from app.api.v1.endpoints.encounters import get_encounter_journey
    from unittest.mock import MagicMock

    db = SessionLocal()
    enc_id = "test_enc_r05_01"
    try:
        enc = EncounterModel(
            id=enc_id,
            patient_id="PAT20260001",
            provider_id="Dr. Omar Ali",
            clinic_id="DXB-CLINIC-01",
            date="2026-01-05",
            diagnosis="R10.9",
            type="Consultation",
            status="Completed",
            journey_stage="Completed"
        )
        db.add(enc)
        db.commit()

        mock_user = MagicMock()
        mock_user.role = "System Administrator"
        mock_user.clinic_id = "DXB-CLINIC-01"

        res = get_encounter_journey(encounter_id=enc_id, db=db, current_user=mock_user)
        assert res.get("mrn") == "MRN-000001", f"Expected MRN-000001 but got {res.get('mrn')}"
        assert res.get("encounter_id") == enc_id
        assert res.get("patient_id") == "PAT20260001"
    finally:
        db.query(EncounterModel).filter(EncounterModel.id == enc_id).delete()
        db.commit()
        db.close()


def test_report_05_workbook_encounters_readonly():
    """Report 05 §05.6: Encounters are read-only in workbook integration profile."""
    from app.db.database import SessionLocal
    from app.models.encounter import EncounterModel
    from app.api.v1.endpoints.encounters import update_encounter
    from app.schemas.encounter import EncounterUpdate
    from app.core.config import settings
    from fastapi import HTTPException
    from unittest.mock import MagicMock

    db = SessionLocal()
    enc_id = "test_enc_r05_ro"
    orig_profile = getattr(settings, "SOURCE_PROFILE", "")
    try:
        settings.SOURCE_PROFILE = "workbook"
        enc = EncounterModel(
            id=enc_id,
            patient_id="PAT20260002",
            provider_id="Dr. Omar Ali",
            clinic_id="DXB-CLINIC-01",
            date="2026-01-05",
            type="Consultation",
            status="Completed"
        )
        db.add(enc)
        db.commit()

        mock_user = MagicMock()
        mock_user.role = "Clinic Manager"
        mock_user.clinic_id = "DXB-CLINIC-01"

        update_in = EncounterUpdate(status="In Room")
        try:
            update_encounter(encounter_id=enc_id, encounter_in=update_in, db=db, current_user=mock_user)
            assert False, "Expected 403 HTTPException for workbook read-only mode"
        except HTTPException as exc:
            assert exc.status_code == 403
            assert "read-only" in exc.detail
    finally:
        settings.SOURCE_PROFILE = orig_profile
        db.query(EncounterModel).filter(EncounterModel.id == enc_id).delete()
        db.commit()
        db.close()


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
    test_workbook_profile_isolation_and_no_practice_fusion()
    test_workbook_clinics_endpoint_receipt_and_connectors()
    test_signals_verifiable_origin_in_workbook_profile()
    test_report_01_simulator_opt_in()
    test_report_01_check_2_provider_scope_and_date_cutoff()
    test_report_01_check_3_trend_and_freshness()
    test_report_03_problem_reason_and_operational_dependency_defaults()
    test_report_03_organization_admin_authority_policy()
    test_report_05_journey_mrn_crosswalk()
    test_report_05_workbook_encounters_readonly()
    print("All QA verification fix tests passed successfully!")
