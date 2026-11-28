"""PR #8 A6-A8: the evidence behind every integration status on screen.

* A connection is "verified" only by a completed server-side provider probe (connector_probes).
* "Synced" is a recorded sync run for that clinic's connection (connector_sync_runs).
* "Imported" is a recorded import of records (data_imports); records typed in by hand are not imports.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


class ConnectorProbeModel(Base):
    __tablename__ = "connector_probes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connector_id = Column(String, index=True, nullable=False)
    clinic_id = Column(String, index=True, nullable=True)
    probed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    outcome = Column(String(16), nullable=False)        # verified | failed | unavailable
    detail = Column(String, nullable=True)
    requested_by = Column(String, nullable=True)


class ConnectorSyncRunModel(Base):
    __tablename__ = "connector_sync_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    connector_id = Column(String, index=True, nullable=False)
    clinic_id = Column(String, index=True, nullable=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False, default="running")   # running | succeeded | failed
    imported_count = Column(Integer, nullable=True)
    error = Column(String, nullable=True)
    requested_by = Column(String, nullable=True)


class DataImportModel(Base):
    __tablename__ = "data_imports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_id = Column(String, index=True, nullable=False)
    source_kind = Column(String(16), nullable=False)    # workbook | connector
    source_label = Column(String, nullable=False)
    connector_id = Column(String, nullable=True)
    run_id = Column(String(36), nullable=True)
    accepted_count = Column(Integer, nullable=False, default=0)
    completed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    recorded_by = Column(String, nullable=True)


class OwnershipReviewModel(Base):
    """Rows the r2 upgrade could not assign from evidence (an orphaned clinic, conflicting owners).
    Nothing is guessed: they wait here for a Platform Administrator (PR #8 A9)."""
    __tablename__ = "ownership_review"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_type = Column(String(16), nullable=False)      # clinic
    subject_id = Column(String, nullable=False)
    kind = Column(String(16), nullable=False)              # orphan | conflict
    detail = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
