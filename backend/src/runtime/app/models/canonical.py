from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from app.integrations.core.source_contracts import (
    EncounterSemanticStatus,
    CoverageSemanticStatus,
    ClaimSemanticStatus,
    RemittanceSemanticStatus
)


class CanonicalAppointment(BaseModel):
    appointment_id: str
    patient_id: str
    provider_id: str
    status: str
    scheduled_time: Optional[datetime] = None
    semantic_status: Optional[EncounterSemanticStatus] = None


class CanonicalPatient(BaseModel):
    patient_id: str
    name: str
    dob: Optional[str] = None


class CanonicalCoverage(BaseModel):
    coverage_id: str
    patient_id: str
    status: str
    payer_id: Optional[str] = None
    semantic_status: Optional[CoverageSemanticStatus] = None
    effective_date: Optional[datetime] = None


class CanonicalClaim(BaseModel):
    claim_id: str
    patient_id: str
    status: str
    billed_amount: Optional[float] = None
    semantic_status: Optional[ClaimSemanticStatus] = None
    service_date: Optional[datetime] = None


class CanonicalRemittance(BaseModel):
    remittance_id: str
    claim_id: str
    status: str
    allowed_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    semantic_status: Optional[RemittanceSemanticStatus] = None
    adjudication_date: Optional[datetime] = None


class CanonicalEvent(BaseModel):
    """
    Standardized operational event that Sentinel Core understands.
    Vendor-specific raw payloads MUST be converted to this canonical model
    before entering the Evidence Engine.
    """
    event_id: str
    event_type: str
    source_system: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Standardized metadata extracted from vendor payload
    canonical_metadata: Dict[str, Any] = Field(default_factory=dict)

    # Specific canonical entities if applicable
    appointment: Optional[CanonicalAppointment] = None
    patient: Optional[CanonicalPatient] = None
    coverage: Optional[CanonicalCoverage] = None
    claim: Optional[CanonicalClaim] = None
    remittance: Optional[CanonicalRemittance] = None
