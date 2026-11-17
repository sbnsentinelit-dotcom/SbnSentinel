"""H01 dimensions; shared registration and forward migrations are external."""
import uuid
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint
from app.db.database import Base


def new_id():
    return str(uuid.uuid4())


class HospitalFacilityEnrollment(Base):
    """Persistent security classification, independent of departments or feeds."""
    __tablename__ = "hospital_facility_enrollments"
    clinic_id = Column(String, ForeignKey("org_clinics.id"), primary_key=True)
    basis_ref = Column(String, nullable=False)


class HospitalDepartment(Base):
    __tablename__ = "hospital_departments"
    __table_args__ = (
        UniqueConstraint("clinic_id", "code", name="uq_h01_department_code"),
        UniqueConstraint("id", "clinic_id", name="uq_h01_department_facility"),
    )
    id = Column(String, primary_key=True, default=new_id)
    clinic_id = Column(String, ForeignKey("org_clinics.id"), nullable=False, index=True)
    code = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class HospitalDepartmentVersion(Base):
    __tablename__ = "hospital_department_versions"
    __table_args__ = (CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_h01_department_period"),)
    id = Column(String, primary_key=True, default=new_id)
    department_id = Column(String, ForeignKey("hospital_departments.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    basis_ref = Column(String, nullable=False)


class HospitalLocation(Base):
    __tablename__ = "hospital_locations"
    __table_args__ = (
        UniqueConstraint("clinic_id", "code", name="uq_h01_location_code"),
        UniqueConstraint("id", "clinic_id", name="uq_h01_location_facility"),
    )
    id = Column(String, primary_key=True, default=new_id)
    clinic_id = Column(String, ForeignKey("org_clinics.id"), nullable=False, index=True)
    code = Column(String, nullable=False)
    name = Column(String, nullable=False)


class HospitalLocationMembership(Base):
    __tablename__ = "hospital_location_memberships"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_h01_location_period"),
        ForeignKeyConstraint(["location_id", "clinic_id"], ["hospital_locations.id", "hospital_locations.clinic_id"], name="fk_h01_membership_location"),
        ForeignKeyConstraint(["department_id", "clinic_id"], ["hospital_departments.id", "hospital_departments.clinic_id"], name="fk_h01_membership_department"),
    )
    id = Column(String, primary_key=True, default=new_id)
    clinic_id = Column(String, nullable=False, index=True)
    location_id = Column(String, nullable=False, index=True)
    department_id = Column(String, nullable=False, index=True)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    basis_ref = Column(String, nullable=False)


class HospitalProviderMembership(Base):
    __tablename__ = "hospital_provider_memberships"
    __table_args__ = (CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_h01_provider_period"),)
    id = Column(String, primary_key=True, default=new_id)
    # Opaque qualified reference supplied by the shared source identity contract.
    provider_ref = Column(String, nullable=False, index=True)
    department_id = Column(String, ForeignKey("hospital_departments.id"), nullable=False, index=True)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    basis_ref = Column(String, nullable=False)
