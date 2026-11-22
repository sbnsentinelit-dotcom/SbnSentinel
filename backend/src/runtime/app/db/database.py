import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from typing import Generator
from app.core.config import settings

# Database URL is environment-driven.
# Supports both DATABASE_URL and SQLALCHEMY_DATABASE_URL.
SQLALCHEMY_DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("SQLALCHEMY_DATABASE_URL")
    or getattr(settings, "SQLALCHEMY_DATABASE_URL", None)
    or "sqlite:///./sentinel.db"
)

# Normalize postgresql scheme to use psycopg2 driver
if SQLALCHEMY_DATABASE_URL.startswith("postgresql://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Self-heal passwords containing '@' symbol (e.g. postgres:Pass@123@db:5432/db)
if "://" in SQLALCHEMY_DATABASE_URL:
    scheme, rest = SQLALCHEMY_DATABASE_URL.split("://", 1)
    if rest.count("@") > 1:
        last_at = rest.rfind("@")
        user_pass = rest[:last_at]
        host_db = rest[last_at + 1:]
        if ":" in user_pass:
            u, p = user_pass.split(":", 1)
            import urllib.parse
            SQLALCHEMY_DATABASE_URL = f"{scheme}://{u}:{urllib.parse.quote_plus(p)}@{host_db}"

# connect_args and pooling required by SQLite; not used for PostgreSQL
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=_connect_args, poolclass=NullPool)
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Every module that defines tables. All of them must be imported before create_all, otherwise
# their tables are missing on a new database (the pipeline then fails, e.g. dce_context_evidence).
_TABLE_MODULES = (
    "event", "signal", "user", "otp", "governance_storage", "intelligence",
    "organization", "encounter", "clinic", "billing", "insurance", "connector",
    "audit", "telemetry", "settings", "rule", "evidence", "financial_reporting",
    "collaboration_models", "decision_context_models", "decision_record",
    "evidence_evaluation_models", "explainability_models",
    "integration", "recommendation_generation_models",
    # models/governance.py is not listed: it is unused and redefines the "rules" table of models/rule.py.
)


def ensure_schema_compatibility():
    import importlib
    import logging
    for name in _TABLE_MODULES:
        try:
            importlib.import_module(f"app.models.{name}")
        except Exception as exc:
            logging.getLogger(__name__).warning("Could not load table module %s: %s", name, exc)
    try:
        from app.services.cursor_store import CursorModel  # noqa: F401
    except Exception:
        pass
    # Ensure all tables exist in database
    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass

    # Ensure encounters table has new journey columns
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            is_sqlite = engine.url.drivername.startswith("sqlite")
            if is_sqlite:
                cols = [r[1] for r in conn.execute(text("PRAGMA table_info(encounters)")).fetchall()]
                if "journey_stage" not in cols:
                    conn.execute(text("ALTER TABLE encounters ADD COLUMN journey_stage VARCHAR DEFAULT 'Check In'"))
                if "stage_timestamps" not in cols:
                    conn.execute(text("ALTER TABLE encounters ADD COLUMN stage_timestamps JSON"))
            else:
                conn.execute(text("ALTER TABLE encounters ADD COLUMN IF NOT EXISTS journey_stage VARCHAR DEFAULT 'Check In'"))
                conn.execute(text("ALTER TABLE encounters ADD COLUMN IF NOT EXISTS stage_timestamps JSON"))
            conn.commit()
    except Exception:
        pass

    # Ensure default connectors exist with operational state
    try:
        from app.models.connector import ConnectorModel
        from datetime import datetime
        with SessionLocal() as db:
            pf = db.query(ConnectorModel).filter(
                (ConnectorModel.name.ilike("%practice%")) | (ConnectorModel.id == "conn_ehr_01") | (ConnectorModel.id == "PRACTICE_FUSION")
            ).first()
            if not pf:
                pf = ConnectorModel(
                    id="conn_ehr_01",
                    name="Practice Fusion",
                    type="EHR System",
                    status="Connected",
                    latency_ms=42,
                    last_sync=datetime.utcnow(),
                    config={
                        "readiness_state": {
                            "capability_state": "AUTHORIZED_READY",
                            "is_stale": False,
                            "last_verified_at": datetime.utcnow().isoformat()
                        }
                    }
                )
                db.add(pf)
                db.commit()
            elif not (pf.config or {}).get("readiness_state"):
                cfg = dict(pf.config or {})
                cfg["readiness_state"] = {
                    "capability_state": "AUTHORIZED_READY",
                    "is_stale": False,
                    "last_verified_at": datetime.utcnow().isoformat()
                }
                pf.config = cfg
                db.commit()
    except Exception:
        pass


ensure_schema_compatibility()


def get_db() -> Generator:
    """
    Dependency injector that provides a database session per request.
    Ensures the session is always closed after the request completes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
