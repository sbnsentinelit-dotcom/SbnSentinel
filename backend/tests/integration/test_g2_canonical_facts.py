import pytest
from datetime import datetime
from app.integrations.core.source_contracts import (
    SourceObservation,
    normalize_source_observation,
    EncounterSemanticStatus,
    CoverageSemanticStatus,
    ClaimSemanticStatus,
    RemittanceSemanticStatus
)


@pytest.mark.integration
def test_g2_source_neutral_equivalence_across_formats():
    """
    G2 Gate: Equivalent synthetic facts from two source formats
    produce the same typed canonical meaning; missing data is explicitly unavailable.
    """
    # Source A: FHIR JSON observation
    obs_fhir = SourceObservation(
        connector_instance_id="CONN-FHIR-01",
        external_record_id="enc-987",
        resource_kind="Encounter",
        source_version="v4.0.1",
        schema_version="FHIR-R4",
        observed_at=datetime(2026, 11, 1, 10, 0, 0),
        raw_status_code="finished",
        payload_ref="vault://sources/conn-fhir-01/enc-987"
    )

    # Source B: REST Billing / HL7 observation
    obs_rest = SourceObservation(
        connector_instance_id="CONN-REST-02",
        external_record_id="rec-456",
        resource_kind="Encounter",
        source_version="v2.1",
        schema_version="REST-JSON",
        observed_at=datetime(2026, 11, 1, 10, 0, 0),
        raw_status_code="completed",
        payload_ref="vault://sources/conn-rest-02/rec-456"
    )

    fact_a = normalize_source_observation(
        obs_fhir,
        fact_key="encounter_status",
        fact_value=EncounterSemanticStatus.COMPLETED.value
    )
    fact_b = normalize_source_observation(
        obs_rest,
        fact_key="encounter_status",
        fact_value=EncounterSemanticStatus.COMPLETED.value
    )

    # Both formats produce identical typed canonical facts
    assert fact_a.canonical_type == fact_b.canonical_type == "Encounter"
    assert fact_a.fact_key == fact_b.fact_key == "encounter_status"
    assert fact_a.fact_value == fact_b.fact_value == "COMPLETED"
    assert fact_a.effective_at == fact_b.effective_at == datetime(2026, 11, 1, 10, 0, 0)

    # External IDs are strictly namespaced by connector instance
    assert fact_a.source_ref == "CONN-FHIR-01:Encounter:enc-987"
    assert fact_b.source_ref == "CONN-REST-02:Encounter:rec-456"


@pytest.mark.integration
def test_g2_minimum_semantic_separation_invariants():
    """
    G2 Non-negotiable invariant:
    - scheduled ≠ attended ≠ completed
    - missing ≠ inactive ≠ denied
    - submitted ≠ rejected ≠ adjudicated denial
    - charged ≠ allowed ≠ paid ≠ recoverable
    """
    # 1. Encounter / Appointment
    assert EncounterSemanticStatus.SCHEDULED != EncounterSemanticStatus.ATTENDED
    assert EncounterSemanticStatus.ATTENDED != EncounterSemanticStatus.COMPLETED

    # 2. Coverage
    assert CoverageSemanticStatus.MISSING != CoverageSemanticStatus.INACTIVE
    assert CoverageSemanticStatus.INACTIVE != CoverageSemanticStatus.DENIED

    # 3. Claims
    assert ClaimSemanticStatus.SUBMITTED != ClaimSemanticStatus.REJECTED
    assert ClaimSemanticStatus.REJECTED != ClaimSemanticStatus.ADJUDICATED_DENIAL

    # 4. Remittances
    assert RemittanceSemanticStatus.CHARGED != RemittanceSemanticStatus.ALLOWED
    assert RemittanceSemanticStatus.ALLOWED != RemittanceSemanticStatus.PAID
    assert RemittanceSemanticStatus.PAID != RemittanceSemanticStatus.RECOVERABLE


@pytest.mark.integration
def test_g2_absent_source_time_stays_null():
    """
    Rule 03: Preserve source event time, effective time and ingestion time separately;
    absent source time stays NULL (never fabricated with current time).
    """
    obs_no_time = SourceObservation(
        connector_instance_id="CONN-BATCH-03",
        external_record_id="claim-001",
        resource_kind="Claim",
        source_version="v1",
        schema_version="CSV",
        observed_at=None,  # Absent source timestamp
        raw_status_code="pending",
        payload_ref="vault://sources/claim-001"
    )

    fact = normalize_source_observation(
        obs_no_time,
        fact_key="claim_status",
        fact_value=ClaimSemanticStatus.SUBMITTED.value
    )

    assert fact.effective_at is None, "Absent source time MUST remain None/NULL, never fabricated."
    assert obs_no_time.imported_at is not None, "Ingestion time must be recorded separately."
