import pytest
from datetime import datetime
from app.integrations.core.capabilities import (
    CapabilityGrant,
    capability_registry,
)
from app.integrations.core.source_authority import (
    connector_store,
    ConnectorBinding,
)
from app.services.connector_manager import connector_manager
from app.services.policy_engine import policy_engine
from app.services.governance_registry import governance_registry
from app.core.config import Settings


@pytest.fixture(autouse=True)
def clean_registry():
    capability_registry.clear()
    connector_store.clear()
    yield
    capability_registry.clear()
    connector_store.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Gate 4 Mandatory Requirement: Billing-Only Connector Capability Readiness
# ─────────────────────────────────────────────────────────────────────────────

def test_billing_only_synthetic_connector_ready_for_claim_read_without_patient():
    """
    REQUIRED PASS:
    A billing-only synthetic connector is ready for claim.read without a FHIR Patient
    endpoint; unrelated capabilities remain unavailable.
    """
    connector_id = "conn_synthetic_billing_01"

    # 1. Register authorized connector binding for billing only
    binding = ConnectorBinding(
        connector_instance_id=connector_id,
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_billing_main",
        state="AUTHORIZED_READY",
        permitted_resources={"Claim", "Remittance"},  # NO Patient, NO Encounter
    )
    connector_store.register(binding)

    # 2. Grant claim.read capability with verified transport, permission, and data contract
    claim_grant = CapabilityGrant(
        connector_instance_id=connector_id,
        capability="claim.read",
        transport_ready=True,
        permission_verified=True,
        data_contract_verified=True,
        last_checked_at=datetime.utcnow(),
    )
    capability_registry.register_grant(claim_grant)

    # 3. Query readiness per capability:
    # claim.read MUST be ready
    assert connector_manager.is_capability_ready(connector_id, "claim.read") is True

    # patient.read MUST remain unavailable (unsupported endpoint does not block billing)
    assert connector_manager.is_capability_ready(connector_id, "patient.read") is False

    # encounter.read MUST remain unavailable
    assert connector_manager.is_capability_ready(connector_id, "encounter.read") is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Separate Technical and Business States
# ─────────────────────────────────────────────────────────────────────────────

def test_capability_readiness_lifecycle_states():
    """
    Verifies capability readiness reflects technical states:
    - CONFIGURED: Not ready
    - AUTHORIZED_READY: Ready
    - REVOKED / SUSPENDED: Not ready
    """
    cid = "conn_lifecycle_test"

    # Setup grant
    grant = CapabilityGrant(
        connector_instance_id=cid,
        capability="claim.read",
        transport_ready=True,
        permission_verified=True,
        data_contract_verified=True,
    )
    capability_registry.register_grant(grant)

    # State: CONFIGURED -> NOT ready
    binding = ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_1",
        state="CONFIGURED",
        permitted_resources={"Claim"},
    )
    connector_store.register(binding)
    assert connector_manager.is_capability_ready(cid, "claim.read") is False

    # Transition to AUTHORIZED_READY -> Ready
    connector_store.authorize(cid)
    assert connector_manager.is_capability_ready(cid, "claim.read") is True

    # Transition to SUSPENDED -> NOT ready
    connector_store.suspend(cid)
    assert connector_manager.is_capability_ready(cid, "claim.read") is False

    # Transition to REVOKED -> NOT ready
    connector_store.revoke(cid)
    assert connector_manager.is_capability_ready(cid, "claim.read") is False


def test_capability_grant_incomplete_verification_blocks_evaluation():
    """If transport, permission, or data contract is unverified, can_evaluate is False."""
    cid = "conn_unverified_transport"
    binding = ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_1",
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    )
    connector_store.register(binding)

    # Transport not ready
    unverified_grant = CapabilityGrant(
        connector_instance_id=cid,
        capability="claim.read",
        transport_ready=False,  # Transport issue
        permission_verified=True,
        data_contract_verified=True,
    )
    capability_registry.register_grant(unverified_grant)
    assert connector_manager.is_capability_ready(cid, "claim.read") is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Policy Engine: POL-003 V1 vs V2 Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def test_policy_engine_pol003_v1_historical_reproducibility():
    """POL-003 V1 evaluates historical rules based on source string."""
    policy_v1 = governance_registry.get_policy_by_version("POL-003", "V1")
    assert policy_v1 is not None

    # V1 in development accepts Practice Fusion
    context = {"source": "Practice Fusion"}
    passed, reason = policy_engine._execute_policy_logic(policy_v1, context, {}, "EHR")
    assert passed is True
    assert "PASS" in reason

    # V1 rejects unknown source
    context_bad = {"source": "UnknownVendor"}
    passed_bad, reason_bad = policy_engine._execute_policy_logic(policy_v1, context_bad, {}, "EHR")
    assert passed_bad is False
    assert "BLOCKED" in reason_bad


def test_policy_engine_pol003_v2_capability_driven_admission():
    """
    POL-003 V2 evaluates connector ID + clinic permission + capability verification,
    permitting billing-only connector without Practice Fusion text check.
    """
    policy_v2 = governance_registry.get_policy_by_version("POL-003", "V2")
    assert policy_v2 is not None

    connector_id = "conn_billing_v2"
    clinic_id = "clinic_south"

    # Setup authorized binding
    connector_store.register(ConnectorBinding(
        connector_instance_id=connector_id,
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic_id,
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    ))

    # Register verified claim.read grant
    capability_registry.register_grant(CapabilityGrant(
        connector_instance_id=connector_id,
        capability="claim.read",
        transport_ready=True,
        permission_verified=True,
        data_contract_verified=True,
    ))

    # Case A: Permitted capability on matching clinic
    decision_context = {
        "source": connector_id,
        "clinic_id": clinic_id,
        "required_capability": "claim.read",
        "event_type": "Billing",
    }
    passed, reason = policy_engine._execute_policy_logic(policy_v2, decision_context, {}, "Billing")
    assert passed is True
    assert "PASS" in reason

    # Case B: Scope violation (wrong clinic)
    wrong_clinic_ctx = {
        "source": connector_id,
        "clinic_id": "clinic_north",  # Wrong clinic
        "required_capability": "claim.read",
        "event_type": "Billing",
    }
    passed_scope, reason_scope = policy_engine._execute_policy_logic(policy_v2, wrong_clinic_ctx, {}, "Billing")
    assert passed_scope is False
    assert "Scope violation" in reason_scope

    # Case C: Unrelated capability (e.g. encounter.read on billing connector)
    unrelated_cap_ctx = {
        "source": connector_id,
        "clinic_id": clinic_id,
        "required_capability": "encounter.read",
        "event_type": "EHR",
    }
    passed_unrelated, reason_unrelated = policy_engine._execute_policy_logic(policy_v2, unrelated_cap_ctx, {}, "EHR")
    assert passed_unrelated is False
    assert "not ready" in reason_unrelated


# ─────────────────────────────────────────────────────────────────────────────
# 4. Config Validation: Removal of PF-Specific Startup Requirements
# ─────────────────────────────────────────────────────────────────────────────

def test_non_pf_installation_does_not_require_jwt_startup_in_production():
    """
    Installations with ENABLE_PRACTICE_FUSION=False do NOT fail startup
    validation when JWT_PRIVATE_KEY is unset in production mode.
    """
    # Non-PF production settings (e.g. billing-only installation)
    settings = Settings(
        ENVIRONMENT="PRODUCTION",
        ENABLE_PRACTICE_FUSION=False,
        SECRET_KEY="A_Very_Long_Secure_Production_Secret_Key_32_Chars_Min",
        BOOTSTRAP_ADMIN_PASSWORD="CustomSecureAdminPassword2026!",
        SQLALCHEMY_DATABASE_URL="postgresql://user:pass@localhost:5432/sentinel",
        CLINIC_TIMEZONE="UTC",
        JWT_PRIVATE_KEY="",  # Unset
        JWT_KEY_ID="",       # Unset
    )
    # Does not raise error because ENABLE_PRACTICE_FUSION is False
    assert settings.ENABLE_PRACTICE_FUSION is False
