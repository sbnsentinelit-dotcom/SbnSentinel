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

# connect_args and pooling required by SQLite; not used for PostgreSQL
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=_connect_args, poolclass=NullPool)
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_schema_compatibility():
    try:
        from app.models import (  # noqa
            event, signal, user, otp, governance_storage, intelligence,
            organization, encounter, clinic, billing, insurance, connector,
            audit, telemetry, settings, rule, evidence, financial_reporting
        )
        from app.services.cursor_store import CursorModel  # noqa: F401
    except Exception:
        pass
    # Ensure all tables exist in database
    try:
        Base.metadata.create_all(bind=engine)
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
