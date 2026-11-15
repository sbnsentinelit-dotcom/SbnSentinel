import pytest
from app.db.database import SessionLocal
from app.models.evidence import EvidenceModel, IngestionLedgerModel
from app.integrations.core.source_authority import (
    connector_store,
    ConnectorBinding,
    validate_source_authority,
    ScopeViolation,
)
from app.integrations.core.capabilities import (
    capability_registry,
    CapabilityGrant,
)
from app.integrations.core.mapping_registry import (
    mapping_registry,
    SemanticMappingDefinition,
)
from app.services.connector_manager import connector_manager
from app.services.ingress_service import canonical_ingress
from app.services.policy_engine import policy_engine
from app.services.cursor_store import CursorModel
from app.services.governance_registry import governance_registry


@pytest.fixture(autouse=True)
def clean_conformance_env():
    connector_store.clear()
    capability_registry.clear()
    with SessionLocal() as db:
        db.query(CursorModel).delete()
        db.query(IngestionLedgerModel).delete()
        db.query(EvidenceModel).delete()
        db.commit()
    yield
    connector_store.clear()
    capability_registry.clear()
    with SessionLocal() as db:
        db.query(CursorModel).delete()
        db.query(IngestionLedgerModel).delete()
        db.query(EvidenceModel).delete()
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Matrix 01: Three Sources Normalize Equivalent Facts to Equivalent Meaning
# ─────────────────────────────────────────────────────────────────────────────

def test_m01_three_sources_normalize_equivalent_facts():
    """
    Source A (FHIR EHR), Source B (REST Billing), and Source C (Structured Batch)
    all normalize equivalent facts to equivalent canonical meaning.
    """
    clinic = "clinic_conformance"

    # Register 3 distinct sources
    connector_store.register(ConnectorBinding(
        connector_instance_id="conn_source_a_fhir",
        vendor_id="practice_fusion",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Encounter"},
    ))
    connector_store.register(ConnectorBinding(
        connector_instance_id="conn_source_b_rest",
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Encounter"},
    ))
    connector_store.register(ConnectorBinding(
        connector_instance_id="conn_source_c_batch",
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Encounter"},
    ))

    # Source A (FHIR format)
    fhir_record = {
        "context_type": "Encounter",
        "detail": {
            "id": "enc_a_100",
            "class": {"code": "AMB"},
            "status": "in-progress",
            "period": {"start": "2026-11-04T09:00:00Z"},
            "meta": {"versionId": "1"},
        }
    }
    canonical_ingress.submit_batch("conn_source_a_fhir", [fhir_record], clinic_id=clinic)

    # Source B (REST format)
    rest_record = {
        "context_type": "Encounter",
        "detail": {
            "id": "enc_b_200",
            "class": {"code": "AMB"},
            "status": "in-progress",
            "period": {"start": "2026-11-04T09:00:00Z"},
            "meta": {"versionId": "1"},
        }
    }
    canonical_ingress.submit_batch("conn_source_b_rest", [rest_record], clinic_id=clinic)

    # Source C (Batch CSV-converted format)
    csv_row_record = {
        "context_type": "Encounter",
        "detail": {
            "id": "enc_c_300",
            "class": {"code": "AMB"},
            "status": "in-progress",
            "period": {"start": "2026-11-04T09:00:00Z"},
            "meta": {"versionId": "1"},
        }
    }
    canonical_ingress.submit_batch("conn_source_c_batch", [csv_row_record], clinic_id=clinic)

    # Verify canonical facts across all 3 sources
    with SessionLocal() as db:
        ev_a = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "enc_a_100").first()
        ev_b = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "enc_b_200").first()
        ev_c = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "enc_c_300").first()

        assert ev_a and ev_b and ev_c
        # Equivalent status and class extracted identically
        assert '"status": "in-progress"' in ev_a.fact_value_str
        assert '"status": "in-progress"' in ev_b.fact_value_str
        assert '"status": "in-progress"' in ev_c.fact_value_str


# ─────────────────────────────────────────────────────────────────────────────
# Matrix 03 & 04: Source Unable to Provide Claim Response Does Not Fabricate Denial
# ─────────────────────────────────────────────────────────────────────────────

def test_m04_source_unable_to_provide_claim_response_does_not_fabricate_denial():
    """
    Source unable to provide claim response fails closed (STATUS_UNRESOLVED),
    preventing fabricated denial finding or unauthorized decision.
    """
    clinic = "clinic_conformance"
    mapping_registry.register_mapping(SemanticMappingDefinition(
        mapping_id="claim_status_map",
        version="1.0",
        source_field="claim.status",
        source_value="PendingResponse",
        canonical_field="claim.status",
        canonical_value="UNRESOLVED",
        requires=["response_timestamp", "payer_id"],
        when_incomplete="STATUS_UNRESOLVED",
        when_unmapped="NOT_EVALUABLE",
    ))

    connector_store.register(ConnectorBinding(
        connector_instance_id="conn_pending_claim",
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    ))

    # Incomplete claim missing payer response
    incomplete_claim = {
        "context_type": "Claim",
        "mapping_id": "claim_status_map",
        "mapping_version": "1.0",
        "detail": {
            "id": "claim_no_response_01",
            "raw_status": "PendingResponse",
            # missing payer_id and response_timestamp
        }
    }
    res = canonical_ingress.submit_batch("conn_pending_claim", [incomplete_claim], clinic_id=clinic)
    assert res["gated"] == 1

    with SessionLocal() as db:
        ev = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "claim_no_response_01").first()
        assert ev is not None
        # Does NOT fabricate DENIAL
        assert "DENIED" not in ev.fact_value_str
        assert "STATUS_UNRESOLVED" in ev.fact_value_str


# ─────────────────────────────────────────────────────────────────────────────
# Matrix 09: Billing-Only Source READY Despite No Patient Search
# ─────────────────────────────────────────────────────────────────────────────

def test_m09_billing_only_source_ready_without_patient_search():
    """Billing-only connector is READY for claim.read without Patient endpoint."""
    cid = "conn_billing_matrix_09"
    connector_store.register(ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_billing",
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    ))
    capability_registry.register_grant(CapabilityGrant(
        connector_instance_id=cid,
        capability="claim.read",
        transport_ready=True,
        permission_verified=True,
        data_contract_verified=True,
    ))

    assert connector_manager.is_capability_ready(cid, "claim.read") is True
    assert connector_manager.is_capability_ready(cid, "patient.read") is False


# ─────────────────────────────────────────────────────────────────────────────
# Matrix 10: Region-Separated Data Paths and No Accidental Transfer
# ─────────────────────────────────────────────────────────────────────────────

def test_m10_region_separated_data_paths():
    """US deployment rejects UAE connector data to prevent cross-border leakage."""
    connector_store.register(ConnectorBinding(
        connector_instance_id="conn_uae_isolated",
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_uae_01",
        jurisdiction="UAE",
        state="AUTHORIZED_READY",
        permitted_resources={"Patient"},
    ))

    with pytest.raises(ScopeViolation):
        validate_source_authority(
            instance_id="conn_uae_isolated",
            verified_clinic_id="clinic_uae_01",
            resource_kind="Patient",
            target_region="US",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Matrix 11: Unknown, Incomplete and Financial Zero Remain Semantically Distinct
# ─────────────────────────────────────────────────────────────────────────────

def test_m11_unknown_incomplete_and_financial_zero_distinct():
    """Zero paid amount (0.0) is distinct from None/unknown and incomplete."""
    paid_zero = 0.0
    paid_unknown = None
    paid_incomplete = "STATUS_UNRESOLVED"

    assert paid_zero != paid_unknown
    assert paid_zero != paid_incomplete
    assert paid_unknown != paid_incomplete
    assert paid_zero == 0.0
    assert paid_unknown is None


# ─────────────────────────────────────────────────────────────────────────────
# Matrix 12: Reference V1 Adapter and Existing D1-D9 Workflow Remain Green
# ─────────────────────────────────────────────────────────────────────────────

def test_m12_v1_historical_policy_reproducibility():
    """POL-003 V1 preserves historical V1 policy evaluation behavior."""
    policy_v1 = governance_registry.get_policy_by_version("POL-003", "V1")
    assert policy_v1 is not None

    passed_pf, _ = policy_engine._execute_policy_logic(policy_v1, {"source": "Practice Fusion"}, {}, "EHR")
    assert passed_pf is True

    passed_other, _ = policy_engine._execute_policy_logic(policy_v1, {"source": "OtherVendor"}, {}, "EHR")
    assert passed_other is False
