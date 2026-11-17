from sqlalchemy import Column, String, Float
from app.db.database import Base


class EncounterModel(Base):
    __tablename__ = "encounters"

    id = Column(String, primary_key=True, index=True)
    patient_id = Column(String(36), index=True, nullable=False)
    provider_id = Column(String(36), index=True, nullable=False)
    clinic_id = Column(String(36), index=True, nullable=True)
    date = Column(String, nullable=False)
    diagnosis = Column(String, nullable=True)
    type = Column(String, nullable=False)  # Consultation, Urgent Care, Checkup, etc.
    status = Column(String, default="Completed")  # Waiting, In Room, Completed, Delayed
    billing_status = Column(String, default="Pending")  # Pending, Billed, Paid, Claim Denied
    copay = Column(Float, nullable=True)
    priority = Column(String, default="Normal")  # High, Normal, Urgent
    wait_time = Column(String, nullable=True)
    department = Column(String, nullable=True)
    avatar = Column(String, nullable=True)
    clinical_notes = Column(String, nullable=True)
    medications = Column(String, nullable=True)
    payer_network = Column(String, nullable=True)
    cpt_code = Column(String, nullable=True)
    billing_amount = Column(Float, nullable=True)
    denial_reason = Column(String, nullable=True)  # e.g. "Coverage Expired", "Invalid CPT Code"

    @property
    def patient_name(self) -> str:
        return self.patient_id

    @patient_name.setter
    def patient_name(self, value: str):
        self.patient_id = value

    @property
    def provider_name(self) -> str:
        return self.provider_id

    @provider_name.setter
    def provider_name(self, value: str):
        self.provider_id = value
