import pytest
import os
from fastapi.testclient import TestClient
from app.main import app
from app.db.database import SessionLocal
from app.models.evidence import EvidenceModel
from app.models.connector import ConnectorModel
from app.models.user import UserRole
from app.api.deps import get_current_user
from app.schemas.service_communication import ServiceRequest
from app.integrations.core.source_authority import (
    validate_source_authority,
    validate_no_plaintext_secrets,
    resolve_secret,
    set_mock_vault_secret,
    clear_mock_vault,
    validate_endpoint_safety,
    validate_payload_safety,
    connector_store,
    ConnectorBinding,
    SourceNotAuthorized,
    ScopeViolation,
    CapabilityUnavailable,
    InsecureCredentialError,
    SecretResolutionError,
    EndpointSecurityError,
    PayloadSecurityError,
)
from app.integrations.core.mapping_registry import (
    mapping_registry,
    SemanticMappingDefinition,
)
from app.services.ingress_service import canonical_ingress
from app.services.evidence_engine import evidence_engine

client = TestClient(app)


class MockAdmin:
    id = "admin_g3"
    role = UserRole.SYSTEM_ADMINISTRATOR.value
    email = "admin@sbnsentinel.com"
    is_active = True
    org_id = "org_1"
    clinic_id = "clinic_alpha"


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean state before each test."""
    app.dependency_overrides[get_current_user] = lambda: MockAdmin()
    connector_store.clear()
    clear_mock_vault()
    db = SessionLocal()
    db.query(EvidenceModel).delete()
    db.query(ConnectorModel).delete()
    db.commit()
    db.close()
    yield
    app.dependency_overrides.clear()
    connector_store.clear()
    clear_mock_vault()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Negative Gate: Unregistered Vendor
# ─────────────────────────────────────────────────────────────────────────────

def test_unregistered_vendor_fails_closed():
    """Unregistered connector instance fails closed with SourceNotAuthorized."""
    with pytest.raises(SourceNotAuthorized) as excinfo:
        validate_source_authority(
            instance_id="unregistered_ehr_99",
            verified_clinic_id="clinic_alpha",
            resource_kind="Encounter",
        )
    assert "not authorized" in str(excinfo.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Negative Gate: Wrong Clinic (Scope Violation)
# ─────────────────────────────────────────────────────────────────────────────

def test_wrong_clinic_scope_violation_fails_closed():
    """Connector bound to clinic_alpha cannot ingress data for clinic_beta."""
    binding = ConnectorBinding(
        connector_instance_id="conn_alpha",
        vendor_id="practice_fusion",
        allowed_clinic_id="clinic_alpha",
        state="AUTHORIZED_READY",
        permitted_resources={"Encounter", "Patient"},
    )
    connector_store.register(binding)

    with pytest.raises(ScopeViolation) as excinfo:
        validate_source_authority(
            instance_id="conn_alpha",
            verified_clinic_id="clinic_beta",
            resource_kind="Encounter",
        )
    assert "clinic" in str(excinfo.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Negative Gate: Revoked / Suspended / Unverified State
# ─────────────────────────────────────────────────────────────────────────────

def test_revoked_or_suspended_permission_fails_closed():
    """Connector in CONFIGURED, SUSPENDED, or REVOKED state fails closed."""
    binding = ConnectorBinding(
        connector_instance_id="conn_stale",
        vendor_id="practice_fusion",
        allowed_clinic_id="clinic_alpha",
        state="CONFIGURED",  # Not yet AUTHORIZED_READY
        permitted_resources={"Encounter"},
    )
    connector_store.register(binding)

    # 1. Configured (unverified) fails
    with pytest.raises(SourceNotAuthorized):
        validate_source_authority("conn_stale", "clinic_alpha", "Encounter")

    # 2. Suspended fails
    connector_store.suspend("conn_stale")
    with pytest.raises(SourceNotAuthorized):
        validate_source_authority("conn_stale", "clinic_alpha", "Encounter")

    # 3. Revoked fails
    connector_store.revoke("conn_stale")
    with pytest.raises(SourceNotAuthorized):
        validate_source_authority("conn_stale", "clinic_alpha", "Encounter")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Negative Gate: Unsupported Resource
# ─────────────────────────────────────────────────────────────────────────────

def test_unsupported_resource_fails_closed():
    """Connector authorized only for Encounter cannot ingress Claim or Coverage."""
    binding = ConnectorBinding(
        connector_instance_id="conn_ehr",
        vendor_id="practice_fusion",
        allowed_clinic_id="clinic_alpha",
        state="AUTHORIZED_READY",
        permitted_resources={"Encounter"},  # No Claim permitted
    )
    connector_store.register(binding)

    with pytest.raises(CapabilityUnavailable) as excinfo:
        validate_source_authority("conn_ehr", "clinic_alpha", "Claim")
    assert "not permitted" in str(excinfo.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Negative Gate: Ambiguous / Incomplete Code Fails Closed (No Decision)
# ─────────────────────────────────────────────────────────────────────────────

def test_ambiguous_and_unmapped_code_gated_at_ingress():
    """
    Ingress of ambiguous status code (when_incomplete: STATUS_UNRESOLVED,
    when_unmapped: NOT_EVALUABLE) is gated from decision emission.
    """
    mapping_def = SemanticMappingDefinition(
        mapping_id="claim_status_example",
        version="1.0",
        source_field="claim.status",
        source_value="Rejected",
        canonical_field="claim.status",
        canonical_value="DENIED",
        requires=["status_stage", "code_system", "code_system_version"],
        when_incomplete="STATUS_UNRESOLVED",
        when_unmapped="NOT_EVALUABLE",
    )
    mapping_registry.register_mapping(mapping_def)

    binding = ConnectorBinding(
        connector_instance_id="conn_billing",
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_alpha",
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    )
    connector_store.register(binding)

    # Case A: Incomplete context (missing requires) -> STATUS_UNRESOLVED
    incomplete_record = {
        "context_type": "Claim",
        "mapping_id": "claim_status_example",
        "mapping_version": "1.0",
        "detail": {
            "id": "claim_incomplete_01",
            "raw_status": "Rejected",
            # missing code_system and status_stage
        }
    }
    res_incomplete = canonical_ingress.submit_batch(
        "conn_billing", [incomplete_record], clinic_id="clinic_alpha"
    )
    assert res_incomplete["status"] == "Success"
    assert res_incomplete["gated"] == 1

    # Case B: Unmapped status -> NOT_EVALUABLE
    unmapped_record = {
        "context_type": "Claim",
        "mapping_id": "claim_status_example",
        "mapping_version": "1.0",
        "detail": {
            "id": "claim_unmapped_02",
            "raw_status": "UnknownVendorStatus",
        }
    }
    res_unmapped = canonical_ingress.submit_batch(
        "conn_billing", [unmapped_record], clinic_id="clinic_alpha"
    )
    assert res_unmapped["status"] == "Success"
    assert res_unmapped["gated"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6. Business Truth Invariant: Text says "no-show" but typed status does not
# ─────────────────────────────────────────────────────────────────────────────

def test_text_says_no_show_but_typed_status_does_not():
    """
    Never infer operational truth (no-show) from descriptive keywords
    when typed status is attended, scheduled, or absent.
    """
    # 1. Detail text contains "no-show", but typed status is ATTENDED
    event_with_typed_attended = {
        "event_id": "evt_att_01",
        "source_system": "practice_fusion",
        "event_type": "EHR",
        "typed_status": "ATTENDED",
        "canonical_metadata": {
            "detail": "Patient was marked as potential no-show previously but attended session",
        }
    }
    req1 = ServiceRequest(
        calling_module="test_g3",
        target_service="EvidenceEngine",
        payload=event_with_typed_attended,
        correlation_id="corr_01",
    )
    res = evidence_engine.invoke(req1)
    package = res.result_payload["eos_003_package"]
    assert package.processing_status == "Success"

    # Verify no NO_SHOW status was emitted in repository
    db = SessionLocal()
    ev_records = db.query(EvidenceModel).filter(EvidenceModel.evidence_id.in_(package.evidence_references)).all()
    status_facts = [r.fact_value_str for r in ev_records if "status" in r.fact_key and r.canonical_entity == "Appointment"]
    assert "ATTENDED" in status_facts
    assert "NO_SHOW" not in status_facts

    # 2. Detail text mentions "no-show" with NO typed status -> refusal to infer
    event_without_typed = {
        "event_id": "evt_untyped_02",
        "source_system": "practice_fusion",
        "event_type": "EHR",
        "canonical_metadata": {
            "detail": "Front desk notes: patient no-show rumored",
        }
    }
    req2 = ServiceRequest(
        calling_module="test_g3",
        target_service="EvidenceEngine",
        payload=event_without_typed,
        correlation_id="corr_02",
    )
    res_untyped = evidence_engine.invoke(req2)
    package_untyped = res_untyped.result_payload["eos_003_package"]
    untyped_records = db.query(EvidenceModel).filter(EvidenceModel.evidence_id.in_(package_untyped.evidence_references)).all()
    untyped_statuses = [r.fact_value_str for r in untyped_records if "status" in r.fact_key and r.canonical_entity == "Appointment"]
    assert "NO_SHOW" not in untyped_statuses
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 7. Credential Safety: Vault / Env secret_ref resolution & plaintext rejection
# ─────────────────────────────────────────────────────────────────────────────

def test_plaintext_credentials_rejected_in_config():
    """Plaintext passwords or tokens in config JSON are forbidden."""
    insecure_config = {
        "client_id": "client_123",
        "password": "my_secret_password",  # Plaintext password
    }
    with pytest.raises(InsecureCredentialError):
        validate_no_plaintext_secrets(insecure_config)

    # secret_ref is accepted
    secure_config = {
        "client_id": "client_123",
        "secret_ref": "vault://ehr/practice_fusion/auth",
    }
    validate_no_plaintext_secrets(secure_config)  # Does not raise


def test_secret_ref_resolution():
    """secret_ref resolves from environment or vault without plaintext in config."""
    # 1. Environment variable resolution
    os.environ["SENTINEL_TEST_KEY"] = "super_secret_token_123"
    assert resolve_secret("env://SENTINEL_TEST_KEY") == "super_secret_token_123"

    # 2. Vault mock resolution
    set_mock_vault_secret("clinics/alpha/key", "vault_secret_val_456")
    assert resolve_secret("vault://clinics/alpha/key") == "vault_secret_val_456"

    # 3. Missing secret fails closed
    with pytest.raises(SecretResolutionError):
        resolve_secret("vault://clinics/nonexistent")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Endpoint & Payload Safety
# ─────────────────────────────────────────────────────────────────────────────

def test_endpoint_safety_and_allowlisting():
    """Only HTTPS/SFTP and allowlisted hosts are permitted."""
    # Plain HTTP rejected
    with pytest.raises(EndpointSecurityError):
        validate_endpoint_safety("http://insecure.ehr-host.com/fhir")

    # HTTPS with allowlist check
    allowed = ["fhir.practicefusion.com", "api.stripe.com"]
    assert validate_endpoint_safety("https://fhir.practicefusion.com/r4", allowed_hosts=allowed)

    # Disallowed host rejected
    with pytest.raises(EndpointSecurityError):
        validate_endpoint_safety("https://malicious.evil-host.com/fhir", allowed_hosts=allowed)


def test_payload_safety_limits():
    """Oversized payloads are rejected."""
    small_payload = b"{\"valid\": true}"
    validate_payload_safety(small_payload, max_bytes=100)  # Passes

    oversized = b"X" * 150
    with pytest.raises(PayloadSecurityError):
        validate_payload_safety(oversized, max_bytes=100)


# ─────────────────────────────────────────────────────────────────────────────
# 9. API Secure Onboarding: Never assign Connected before verification
# ─────────────────────────────────────────────────────────────────────────────

def test_api_onboarding_never_assigns_connected_before_verification():
    """Connecting a system without verification initializes state as Configured/CONFIGURED."""
    from app.api.v1.endpoints.reality_connectors import connect_new_system
    from app.schemas.connector import ConnectorCreate

    conn_in = ConnectorCreate(
        id="conn_unverified_01",
        name="Generic EHR",
        type="EHR System",
        clinic_id="clinic_charlie",
        config={
            "base_url": "https://ehr.example.com",
            "secret_ref": "env://EHR_SECRET",
        }
    )
    with SessionLocal() as db:
        data = connect_new_system(conn_in, db=db)
    assert data["status"] == "Configured"
    assert data["state"] == "CONFIGURED"

    # Ingress attempt using this unverified connector fails closed
    with pytest.raises(SourceNotAuthorized):
        validate_source_authority("conn_unverified_01", "clinic_charlie", "Encounter")


def test_api_onboarding_rejects_plaintext_password():
    """Connecting a system with a plaintext password raises HTTP 400."""
    from app.api.v1.endpoints.reality_connectors import connect_new_system
    from app.schemas.connector import ConnectorCreate
    from fastapi import HTTPException

    conn_in = ConnectorCreate(
        id="conn_insecure_02",
        name="Generic EHR",
        type="EHR System",
        config={
            "username": "doctor",
            "password": "plaintext_password_in_body",
        }
    )
    with pytest.raises(HTTPException) as excinfo:
        with SessionLocal() as db:
            connect_new_system(conn_in, db=db)
    assert excinfo.value.status_code == 400
    assert "plaintext credential detected" in excinfo.value.detail.lower()
