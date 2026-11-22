import sys
import os

# Add runtime directory to sys.path so the module can be run directly or via pytest
runtime_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "runtime"))
if runtime_path not in sys.path:
    sys.path.insert(0, runtime_path)

import pytest
from datetime import datetime, timezone, timedelta
from app.core.config import get_deployment_currency
from app.api.v1.endpoints.patients import _resolve_gender
from app.services.financial_reporting_service import financial_reporting_service
from app.models.financial_reporting import FinancialClaimModel
from app.db.database import SessionLocal


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

    # Fallback to name only when raw_gender is None or empty
    assert _resolve_gender("Mariam", None) == "F"
    assert _resolve_gender("Mariam", "") == "F"
    assert _resolve_gender("Khalid", None) == "M"
    assert _resolve_gender("Khalid", "") == "M"

    # Unknown when ambiguous/unrecognized
    assert _resolve_gender("RandomNonExistingName", None) == "Unknown"
    assert _resolve_gender("RandomNonExistingName", "") == "Unknown"
    assert _resolve_gender("RandomNonExistingName", "Unknown") == "Unknown"


def test_financial_reporting_isolated_by_currency():
    # 3. Financial reporting isolates totals by currency (no mixing AED 640 + USD 100 = 740)
    clinic = "clinic_mixed_curr_01"
    now = datetime.now(timezone.utc)
    p_start = now - timedelta(days=2)
    p_end = now + timedelta(days=1)

    with SessionLocal() as db:
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


if __name__ == "__main__":
    test_currency_mapping_no_db_sniffing()
    test_gender_authoritative_source_preservation()
    test_financial_reporting_isolated_by_currency()
    print("All QA verification fix tests passed successfully!")
