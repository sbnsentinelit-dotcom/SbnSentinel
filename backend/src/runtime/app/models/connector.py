from sqlalchemy import Column, Integer, String, DateTime  # type: ignore
from sqlalchemy.types import JSON  # type: ignore

from app.db.database import Base


class ConnectorModel(Base):
    __tablename__ = "connectors"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)

    # G3: Exact vendor and clinic binding; capability readiness state
    vendor_id = Column(String, nullable=True)
    clinic_id = Column(String, nullable=True)
    secret_ref = Column(String, nullable=True)
    permitted_resources = Column(JSON, nullable=True)
    state = Column(String, default="CONFIGURED", nullable=True)

    # Configured, Authenticated, Connected, Synchronizing, Healthy, Warning, Retrying, Disconnected, Recovered
    status = Column(String, default="Configured")

    # D7: Explicit failure classification from the connector/service owner
    failure_code = Column(String, nullable=True)

    latency_ms = Column(Integer, default=50)
    last_sync = Column(DateTime, nullable=True)
    config = Column(JSON, nullable=True)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
