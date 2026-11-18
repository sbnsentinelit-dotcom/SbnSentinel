"""Explicit hospital grants. No implicit department-wide or unattributed access."""
from sqlalchemy import Column, String, Integer, Boolean, JSON, ForeignKey, UniqueConstraint
from app.db.database import Base
from app.models.hospital_dimensions import new_id


class HospitalMembership(Base):
    __tablename__ = "hospital_memberships"
    __table_args__ = (UniqueConstraint("actor_id", "clinic_id", name="uq_hsp01_actor_facility"),)
    id = Column(String, primary_key=True, default=new_id)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    clinic_id = Column(String, ForeignKey("org_clinics.id"), nullable=False, index=True)
    department_ids = Column(JSON, nullable=False, default=list)
    capabilities = Column(JSON, nullable=False, default=list)
    all_departments = Column(Boolean, nullable=False, default=False)
    allow_unattributed = Column(Boolean, nullable=False, default=False)
    full_record = Column(Boolean, nullable=False, default=False)
    grant_revision = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
