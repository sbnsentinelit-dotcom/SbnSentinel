from datetime import datetime
from typing import Optional, Union, Any
from enum import Enum
from pydantic import BaseModel, Field, field_serializer


class VerifiedHospitalScope(BaseModel):
    """Server result only. Browser selections can narrow but never grant access."""
    model_config = {"frozen": True}
    org_id: str
    clinic_id: str
    allowed_department_ids: frozenset[str]
    all_departments: bool = False
    capabilities: frozenset[str]
    grant_revision: int
    allow_unattributed: bool = False
    full_record: bool = False

    @field_serializer("allowed_department_ids", "capabilities")
    def serialize_sets(self, value):
        return sorted(value)


class EncounterSemanticStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    ATTENDED = "ATTENDED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    UNAVAILABLE = "UNAVAILABLE"


class CoverageSemanticStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DENIED = "DENIED"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"


class ClaimSemanticStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    ADJUDICATED_DENIAL = "ADJUDICATED_DENIAL"
    PAID = "PAID"
    UNAVAILABLE = "UNAVAILABLE"


class RemittanceSemanticStatus(str, Enum):
    CHARGED = "CHARGED"
    ALLOWED = "ALLOWED"
    PAID = "PAID"
    RECOVERABLE = "RECOVERABLE"
    UNAVAILABLE = "UNAVAILABLE"


class SourceObservation(BaseModel):
    """
    G2: Raw observation envelope preserving external provenance without leaking PHI in logs.
    """
    connector_instance_id: str
    external_record_id: str
    resource_kind: str
    source_version: str
    schema_version: str
    observed_at: Optional[datetime] = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)
    raw_status_code: Optional[str] = None
    payload_ref: str  # secured pointer, not raw PHI in logs
    org_id: Optional[str] = None
    clinic_id: Optional[str] = None
    entity_key: Optional[str] = None
    identity_scheme: Optional[str] = None
    parent_identity: Optional[str] = None
    source_digest: Optional[str] = None
    mapping_id: Optional[str] = None
    mapping_version: Optional[str] = None
    extraction_profile_revision: Optional[str] = None
    source_time: Optional[datetime] = None
    effective_at: Optional[datetime] = None
    quality_state: Optional[str] = None

    @property
    def namespaced_id(self) -> str:
        """Rule 02: Namespace external IDs by connector instance, resource type and source ID."""
        return f"{self.connector_instance_id}:{self.resource_kind}:{self.external_record_id}"


class CanonicalFact(BaseModel):
    """
    G2: Standardized atomic fact extracted and normalized from SourceObservation.
    """
    source_ref: str
    canonical_type: str
    fact_key: str
    fact_value: Optional[Union[str, int, float, bool]] = None
    quality_state: str = "CONFIRMED"
    mapping_version: str = "1.0"
    effective_at: Optional[datetime] = None


def normalize_source_observation(
    observation: SourceObservation,
    fact_key: str,
    fact_value: Any,
    quality_state: str = "CONFIRMED",
    mapping_version: str = "1.0",
    effective_at: Optional[datetime] = None
) -> CanonicalFact:
    """
    Produce a typed canonical fact from a source observation.
    Maintains provenance and preserves absent source timestamps as NULL.
    """
    return CanonicalFact(
        source_ref=observation.namespaced_id,
        canonical_type=observation.resource_kind,
        fact_key=fact_key,
        fact_value=fact_value,
        quality_state=quality_state,
        mapping_version=mapping_version,
        effective_at=effective_at or observation.observed_at
    )
