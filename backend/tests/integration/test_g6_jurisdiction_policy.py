import pytest
from datetime import datetime, timedelta
from app.core.config import settings, Settings
from app.integrations.core.source_authority import (
    connector_store,
    ConnectorBinding,
    validate_source_authority,
    ScopeViolation,
)
from app.services.governance_registry import (
    governance_registry,
    PolicyVersion,
    LifecycleState,
)
from app.services.policy_engine import policy_engine


@pytest.fixture(autouse=True)
def clean_registry():
    connector_store.clear()
    from app.models.governance_storage import GovernedPolicyVersionModel
    from app.db.database import SessionLocal
    with SessionLocal() as db:
        db.query(GovernedPolicyVersionModel).filter(
            GovernedPolicyVersionModel.policy_id.like("POL-UAE-%")
        ).delete()
        db.commit()
    governance_registry._policies = [
        p for p in governance_registry._policies
        if not p.policy_id.startswith("POL-UAE-")
    ]
    yield
    connector_store.clear()
    with SessionLocal() as db:
        db.query(GovernedPolicyVersionModel).filter(
            GovernedPolicyVersionModel.policy_id.like("POL-UAE-%")
        ).delete()
        db.commit()
    governance_registry._policies = [
        p for p in governance_registry._policies
        if not p.policy_id.startswith("POL-UAE-")
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Gate 6 Mandatory Requirement: Consistency Across Regions for Identical Policies
# ─────────────────────────────────────────────────────────────────────────────

def test_same_canonical_synthetic_case_evaluates_consistently_across_regions():
    """
    REQUIRED PASS:
    Same canonical synthetic case evaluates consistently across regions
    when identical approved policies apply.
    """
    # Canonical synthetic context: sufficient evidence, verified status
    canonical_case = {
        "source": "conn_universal_01",
        "clinic_id": "clinic_100",
        "event_type": "EHR",
        "sufficiency_status": "SUFFICIENT",
        "is_decision_eligible": True,
        "evidence_package": {
            "evidence_items": ["evd_001"],
            "evidence_references": ["evd_001"],
        },
    }

    # Evaluate in US scope
    us_context = dict(canonical_case, jurisdiction="US")
    us_result = policy_engine.evaluate(us_context)

    # Evaluate in UAE scope
    uae_context = dict(canonical_case, jurisdiction="UAE")
    uae_result = policy_engine.evaluate(uae_context)

    # Identical policies (POL-001, POL-002) produce identical outcomes
    assert us_result.is_permitted == uae_result.is_permitted
    assert us_result.failed_policies == uae_result.failed_policies


# ─────────────────────────────────────────────────────────────────────────────
# 2. Region-Specific Policy Differences Are Explicit and Versioned
# ─────────────────────────────────────────────────────────────────────────────

def test_region_specific_policy_differences_are_explicit_and_versioned():
    """
    Region-specific rule differences apply strictly by jurisdiction scope,
    never inferred from vendor names.
    """
    now = datetime.utcnow()

    # Register an explicit UAE-specific policy
    uae_policy = PolicyVersion(
        policy_id="POL-UAE-PREAUTH-001",
        version="V1",
        content="UAE Ministry of Health pre-authorization required for high-cost claims.",
        lifecycle_state=LifecycleState.ACTIVE,
        scope="UAE",
        effective_from=now - timedelta(days=1),
        approval_state="APPROVED",
    )
    governance_registry.register_policy(uae_policy)

    # In UAE scope, the UAE policy is resolved
    uae_policies = governance_registry.get_applicable_policies(eval_time=now, scope="UAE")
    uae_policy_ids = [p.policy_id for p in uae_policies]
    assert "POL-UAE-PREAUTH-001" in uae_policy_ids

    # In US scope, the UAE policy is NOT resolved (regional isolation)
    us_policies = governance_registry.get_applicable_policies(eval_time=now, scope="US")
    us_policy_ids = [p.policy_id for p in us_policies]
    assert "POL-UAE-PREAUTH-001" not in us_policy_ids


# ─────────────────────────────────────────────────────────────────────────────
# 3. Vendor and Jurisdiction Independence (NEVER: if vendor == "vendor_b")
# ─────────────────────────────────────────────────────────────────────────────

def test_vendor_and_jurisdiction_independence():
    """
    A single vendor adapter can operate in both US and UAE;
    jurisdiction rules depend on deployment/clinic scope, not vendor key.
    """
    now = datetime.utcnow()
    vendor_key = "vendor_b_rest"

    # Clinic in US deployment using vendor_b
    us_context = {
        "source": vendor_key,
        "clinic_id": "clinic_us_ny",
        "jurisdiction": "US",
        "event_type": "Billing",
    }
    # Clinic in UAE deployment using identical vendor_b
    uae_context = {
        "source": vendor_key,
        "clinic_id": "clinic_uae_dxb",
        "jurisdiction": "UAE",
        "event_type": "Billing",
    }

    # Resolution is based on jurisdiction parameter, NEVER vendor string
    policies_us = governance_registry.get_applicable_policies(eval_time=now, scope=us_context["jurisdiction"])
    policies_uae = governance_registry.get_applicable_policies(eval_time=now, scope=uae_context["jurisdiction"])

    assert all(p.scope in ("Global", "US") for p in policies_us)
    assert all(p.scope in ("Global", "UAE") for p in policies_uae)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Regional Boundary: Prohibit Cross-Region Ingress
# ─────────────────────────────────────────────────────────────────────────────

def test_cross_region_ingress_boundary_enforcement():
    """
    A connector bound to UAE cannot ingress data into a US deployment.
    A connector bound to US cannot ingress data into a UAE deployment.
    """
    # 1. UAE-registered connector
    connector_store.register(ConnectorBinding(
        connector_instance_id="conn_uae_ehr_01",
        vendor_id="synthetic_rest",
        allowed_clinic_id="clinic_dxb_01",
        jurisdiction="UAE",
        state="AUTHORIZED_READY",
        permitted_resources={"Patient"},
    ))

    # Attempting to validate in US deployment raises ScopeViolation
    with pytest.raises(ScopeViolation) as excinfo:
        validate_source_authority(
            instance_id="conn_uae_ehr_01",
            verified_clinic_id="clinic_dxb_01",
            resource_kind="Patient",
            target_region="US",
        )
    assert "regional boundary violation" in str(excinfo.value).lower()

    # Validating within UAE deployment succeeds
    binding = validate_source_authority(
        instance_id="conn_uae_ehr_01",
        verified_clinic_id="clinic_dxb_01",
        resource_kind="Patient",
        target_region="UAE",
    )
    assert binding.jurisdiction == "UAE"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Configuration Integrity: DEPLOYMENT_REGION Validation
# ─────────────────────────────────────────────────────────────────────────────

def test_deployment_region_config_validation():
    """Settings validate DEPLOYMENT_REGION must be 'US' or 'UAE'."""
    assert settings.DEPLOYMENT_REGION in ("US", "UAE")

    # Invalid deployment region raises ValueError
    with pytest.raises(ValueError) as excinfo:
        Settings(
            DEPLOYMENT_REGION="INVALID_REGION",
            CLINIC_TIMEZONE="UTC",
        )
    assert "invalid deployment_region" in str(excinfo.value).lower()
