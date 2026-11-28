import uuid
from sqlalchemy import Column, DateTime, String
from app.db.database import Base


class ClinicModel(Base):
    __tablename__ = "clinics"

    id = Column(String(36), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, index=True)
    address = Column(String)
    phone = Column(String)
    status = Column(String, default="Active")   # Active | Inactive
    # Owning organization (MS-001); every clinic belongs to exactly one organization.
    org_id = Column(String, index=True, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    deactivated_at = Column(DateTime, nullable=True)
