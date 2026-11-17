"""HSP01 source crosswalks, durable checkpoints and consumer effect receipts."""
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, UniqueConstraint
from app.db.database import Base
from app.models.hospital_dimensions import new_id


class FacilityCrosswalk(Base):
    __tablename__ = "hospital_facility_crosswalk"
    legacy_clinic_id = Column(String, ForeignKey("clinics.id"), primary_key=True)
    clinic_id = Column(String, ForeignKey("org_clinics.id"), nullable=True)
    basis_ref = Column(String, nullable=True)
    status = Column(String, nullable=False, default="UNRESOLVED")


class SourceIdentityCrosswalk(Base):
    __tablename__ = "hospital_source_crosswalk"
    entity_key = Column(String, primary_key=True)
    org_id = Column(String, nullable=False)
    clinic_id = Column(String, nullable=False, index=True)
    connector_id = Column(String, nullable=False)
    resource_kind = Column(String, nullable=False)
    raw_id = Column(String, nullable=False)
    parent_raw_id = Column(String, nullable=True)


class HospitalCheckpoint(Base):
    __tablename__ = "hospital_checkpoints"
    binding = Column(String, primary_key=True)
    connector_id = Column(String, nullable=False, index=True)
    resource_kind = Column(String, nullable=False)
    clinic_id = Column(String, nullable=False)
    checkpoint_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class HospitalQuarantine(Base):
    __tablename__ = "hospital_quarantine"
    id = Column(String, primary_key=True, default=new_id)
    binding = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False)
    row_index = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class HospitalProjectionReceipt(Base):
    __tablename__ = "hospital_projection_receipts"
    __table_args__ = (UniqueConstraint("ledger_id", "consumer_version", name="uq_hsp01_projection_effect"),)
    id = Column(String, primary_key=True, default=new_id)
    ledger_id = Column(String, ForeignKey("ingestion_ledger.id"), nullable=False)
    evidence_id = Column(String, ForeignKey("evidence_repository.evidence_id"), nullable=False)
    consumer_version = Column(String, nullable=False)
    result = Column(String, nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False)


class HospitalSettlementLink(Base):
    """Explicit reviewed crosswalk; equal names/amounts never create a link."""
    __tablename__ = "hospital_settlement_links"
    source_entity_key = Column(String, ForeignKey("hospital_source_crosswalk.entity_key"), primary_key=True)
    clinic_id = Column(String, ForeignKey("org_clinics.id"), nullable=False)
    economic_receipt_key = Column(String, nullable=False, index=True)
    basis_ref = Column(String, nullable=False)


class HospitalSettlementObservation(Base):
    __tablename__ = "hospital_settlement_observations"
    evidence_id = Column(String, ForeignKey("evidence_repository.evidence_id"), primary_key=True)
    receipt_id = Column(String, ForeignKey("financial_payment_receipts.receipt_id"), nullable=False, index=True)
    basis_ref = Column(String, nullable=False)


class HospitalSnapshotPage(Base):
    __tablename__ = "hospital_snapshot_pages"
    snapshot_id = Column(String, ForeignKey("report_snapshots.snapshot_id"), primary_key=True)
    page_number = Column(Integer, primary_key=True)
    rows_json = Column(Text, nullable=False)
