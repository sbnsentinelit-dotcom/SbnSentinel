from pydantic import BaseModel
from typing import Optional, Dict, Any


class EncounterCreate(BaseModel):
    id: str
    patient_name: str
    provider_name: str
    clinic_id: Optional[str] = None
    date: str
    diagnosis: Optional[str] = None
    type: str
    status: Optional[str] = "Waiting"
    journey_stage: Optional[str] = "Check In"
    stage_timestamps: Optional[Dict[str, Any]] = None
    billing_status: Optional[str] = "Pending"
    copay: Optional[float] = None
    priority: Optional[str] = "Normal"
    wait_time: Optional[str] = None
    department: Optional[str] = None
    avatar: Optional[str] = None
    clinical_notes: Optional[str] = None
    medications: Optional[str] = None
    payer_network: Optional[str] = None
    cpt_code: Optional[str] = None
    billing_amount: Optional[float] = None


class EncounterUpdate(BaseModel):
    clinic_id: Optional[str] = None
    status: Optional[str] = None
    journey_stage: Optional[str] = None
    stage_timestamps: Optional[Dict[str, Any]] = None
    billing_status: Optional[str] = None
    diagnosis: Optional[str] = None
    wait_time: Optional[str] = None
    priority: Optional[str] = None
    clinical_notes: Optional[str] = None
    medications: Optional[str] = None
    payer_network: Optional[str] = None
    cpt_code: Optional[str] = None
    billing_amount: Optional[float] = None


class EncounterResponse(BaseModel):
    id: str
    patient_name: Optional[str] = "Patient"
    provider_name: Optional[str] = "Provider"
    clinic_id: Optional[str] = None
    date: Optional[str] = "2026-01-01"
    diagnosis: Optional[str] = None
    type: Optional[str] = "Consultation"
    status: Optional[str] = "Completed"
    journey_stage: Optional[str] = "Check In"
    stage_timestamps: Optional[Dict[str, Any]] = None
    billing_status: Optional[str] = "Billed"
    copay: Optional[float] = 0.0
    priority: Optional[str] = "Normal"
    wait_time: Optional[str] = None
    department: Optional[str] = "General Practice"
    avatar: Optional[str] = None
    clinical_notes: Optional[str] = None
    medications: Optional[str] = None
    payer_network: Optional[str] = "Daman"
    cpt_code: Optional[str] = "99203"
    billing_amount: Optional[float] = 250.0
    denial_reason: Optional[str] = None
    currency: Optional[str] = "AED"

    class Config:
        from_attributes = True
