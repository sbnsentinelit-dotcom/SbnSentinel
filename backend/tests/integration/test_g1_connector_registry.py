import pytest
from app.integrations.core.registry import (
    instantiate,
    registry,
    SyntheticRestAdapter,
    UnsupportedConnector
)
from app.integrations.vendors.practice_fusion.adapter import PracticeFusionAdapter
from app.integrations.core.mapping_registry import (
    mapping_registry,
    SemanticMappingDefinition
)


@pytest.mark.integration
def test_g1_exact_vendor_registration_and_synthetic_connector():
    """
    G1 Gate: Consolidate integration ownership.
    REQUIRED PASS: A second synthetic registered connector loads by exact vendor ID
    with zero changes to Evidence, Context, Rules, Human Decision and D8.
    """
    # 1. Exact loading of second synthetic connector
    synth_config = {"id": "SYNTH-01", "base_url": "https://api.synthetic.local"}
    adapter_synth = instantiate("synthetic_rest", synth_config)
    assert adapter_synth is not None
    assert isinstance(adapter_synth, SyntheticRestAdapter)
    assert adapter_synth.base_url == "https://api.synthetic.local"

    # 2. Exact loading of primary vendor
    pf_config = {
        "id": "PF-01",
        "client_id": "test_client",
        "base_url": "https://fhir.practicefusion.com",
    }
    adapter_pf = instantiate("practice_fusion", pf_config)
    assert adapter_pf is not None
    assert isinstance(adapter_pf, PracticeFusionAdapter)

    # 3. Substring matching is strictly rejected (NEVER: if key in vendor_name)
    substring_keys = ["practice", "fusion", "synthetic", "rest", "fake_practice_fusion"]
    for sub in substring_keys:
        with pytest.raises(UnsupportedConnector):
            instantiate(sub, pf_config)

    # 4. Registry.create() compatibility wrapper returns None on unsupported
    assert registry.create("practice", pf_config) is None
    assert registry.create("unsupported_vendor", pf_config) is None
    assert registry.create("synthetic_rest", synth_config) is not None


@pytest.mark.integration
def test_g1_mapping_registry_governed_evaluations():
    """
    G1: Test SemanticMappingRegistry fail-closed behavior on missing or incomplete fields.
    """
    mapping_def = SemanticMappingDefinition(
        mapping_id="claim_status_example",
        version="1.0",
        source_field="claim.status",
        source_value="Rejected",
        canonical_field="claim_adjudication_state",
        canonical_value="DENIED",
        requires=["status_stage", "code_system", "code_system_version"],
        when_incomplete="STATUS_UNRESOLVED",
        when_unmapped="NOT_EVALUABLE"
    )
    mapping_registry.register_mapping(mapping_def)

    # Case A: Complete context -> SUCCESS
    status, val = mapping_registry.evaluate_mapping(
        mapping_id="claim_status_example",
        version="1.0",
        source_value="Rejected",
        context={
            "status_stage": "adjudicated",
            "code_system": "X12",
            "code_system_version": "5010"
        }
    )
    assert status == "SUCCESS"
    assert val == "DENIED"

    # Case B: Incomplete context -> STATUS_UNRESOLVED
    status, val = mapping_registry.evaluate_mapping(
        mapping_id="claim_status_example",
        version="1.0",
        source_value="Rejected",
        context={"status_stage": "adjudicated"}  # missing code_system
    )
    assert status == "STATUS_UNRESOLVED"
    assert val is None

    # Case C: Unmapped value -> NOT_EVALUABLE
    status, val = mapping_registry.evaluate_mapping(
        mapping_id="claim_status_example",
        version="1.0",
        source_value="PendingReview",
        context={}
    )
    assert status == "NOT_EVALUABLE"
    assert val is None
