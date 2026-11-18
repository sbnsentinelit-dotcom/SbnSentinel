from pydantic import BaseModel
from typing import Optional


class EncounterCreate(BaseModel):
    id: str
    patient_name: str
    provider_name: str
    date: str
    diagnosis: Optional[str] = None
    type: str
    status: Optional[str] = "Waiting"
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
    status: Optional[str] = None
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
    date: Optional[str] = "2026-01-01"
    diagnosis: Optional[str] = None
    type: Optional[str] = "Consultation"
    status: Optional[str] = "Completed"
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

    class Config:
        from_attributes = True
