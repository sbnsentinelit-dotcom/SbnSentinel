from sqlalchemy import Column, String, DateTime, Integer, Text, Boolean, UniqueConstraint
from app.db.database import Base
from datetime import datetime


class EvidenceModel(Base):
    __tablename__ = "evidence_repository"
    __table_args__ = (
        UniqueConstraint("source_connector", "fact_key", name="uq_evidence_source_fact"),
    )
    evidence_id = Column(String, primary_key=True)
    canonical_entity = Column(String, nullable=False)
    fact_key = Column(String, nullable=False)
    fact_value_str = Column(String, nullable=True)
    source_connector = Column(String, nullable=False)
    retrieval_timestamp = Column(DateTime, nullable=False)
    evidence_type = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)
    version = Column(Integer, default=1)
    previous_version_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # G5 Additive Provenance, Versioning & Tombstone
    tombstone = Column(Boolean, default=False, nullable=True)
    effective_time = Column(DateTime, nullable=True)
    ingestion_sequence = Column(Integer, nullable=True)
    clinic_id = Column(String, nullable=True)
    external_record_id = Column(String, nullable=True)
    resource_kind = Column(String, nullable=True)
    source_version = Column(String, nullable=True)


class IngestionLedgerModel(Base):
    """
    G5 Durable Ingestion Ledger.
    Tracks staged and committed source records, providing strict DB unique dedupe_key.
    dedupe_key = hash(clinic_id, connector_instance_id, resource_kind, external_record_id, source_version)
    """
    __tablename__ = "ingestion_ledger"
    __table_args__ = (
        UniqueConstraint(
            "clinic_id", "connector_instance_id", "resource_kind", "external_record_id", "source_version",
            name="uq_ingest_ledger_dedupe"
        ),
    )
    id = Column(String, primary_key=True)  # dedupe_key hash
    clinic_id = Column(String, nullable=False, index=True)
    connector_instance_id = Column(String, nullable=False, index=True)
    resource_kind = Column(String, nullable=False)
    external_record_id = Column(String, nullable=False)
    source_version = Column(String, nullable=False)
    status = Column(String, default="COMMITTED", nullable=False)  # STAGED, COMMITTED, PUBLISHED, TOMBSTONED
    evidence_id = Column(String, nullable=True, index=True)
    payload_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    effective_at = Column(DateTime, nullable=True)
