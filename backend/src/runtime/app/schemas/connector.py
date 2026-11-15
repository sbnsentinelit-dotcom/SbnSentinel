from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime


class ConnectorCreate(BaseModel):
    id: str  # e.g., 'conn_epic' or 'conn_athena'
    name: str
    type: str
    status: Optional[str] = "Configured"
    vendor_id: Optional[str] = None
    clinic_id: Optional[str] = None
    secret_ref: Optional[str] = None
    permitted_resources: Optional[List[str]] = None
    config: Optional[Dict[str, Any]] = None


class ConnectorResponse(BaseModel):
    id: str
    name: str
    type: str
    status: str
    vendor_id: Optional[str] = None
    clinic_id: Optional[str] = None
    secret_ref: Optional[str] = None
    permitted_resources: Optional[List[str]] = None
    state: Optional[str] = "CONFIGURED"
    latency_ms: int
    last_sync: datetime
    config: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
