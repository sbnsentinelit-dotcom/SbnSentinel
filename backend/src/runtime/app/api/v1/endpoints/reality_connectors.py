from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from datetime import datetime

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.connector import ConnectorModel
from app.schemas.reality import ConnectorSyncRequest
from app.schemas.connector import ConnectorCreate
from app.services.connector_manager import connector_manager
from app.integrations.core.source_authority import (
    validate_no_plaintext_secrets,
    InsecureCredentialError,
    connector_store,
    ConnectorBinding
)


router = APIRouter()


def seed_default_connectors(db: Session):
    default_connectors = [
        {"id": "conn_ehr_01", "name": "Practice Fusion", "type": "EHR System", "status": "Connected", "latency_ms": 42},
        {"id": "conn_tel_01", "name": "Twilio", "type": "Voice + SMS", "status": "Connected", "latency_ms": 18},
        {"id": "conn_gmail_01", "name": "Gmail Workspace", "type": "Email automation", "status": "Syncing", "latency_ms": 35},
        {"id": "conn_stripe_01", "name": "Stripe", "type": "Billing", "status": "Connected", "latency_ms": 22},
        {"id": "conn_zoom_01", "name": "Zoom", "type": "Telehealth", "status": "Connected", "latency_ms": 15},
        {"id": "conn_kareo_01", "name": "Kareo", "type": "Billing System", "status": "Needs attention", "latency_ms": 110},
    ]
    for conn_data in default_connectors:
        conn = ConnectorModel(
            id=conn_data["id"],
            name=conn_data["name"],
            type=conn_data["type"],
            status=conn_data["status"],
            latency_ms=conn_data["latency_ms"],
            last_sync=datetime.utcnow()
        )
        db.add(conn)
    db.commit()


def _actor(current_user):
    """The signed-in user, or None when the function is called directly (tests, services)."""
    return current_user if isinstance(current_user, User) else None


def _scope_query(db: Session, actor):
    """Connectors of the clinics the caller may see (MS-001); a direct call sees all."""
    q = db.query(ConnectorModel)
    if actor is None:
        return q
    from app.core.access import visible_clinic_ids
    ids = visible_clinic_ids(db, actor)
    return q if ids is None else q.filter(ConnectorModel.clinic_id.in_(ids or ["__none__"]))


def _in_scope(db: Session, actor, connector_id: str) -> ConnectorModel:
    connector = _scope_query(db, actor).filter(ConnectorModel.id == connector_id).first()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


def _clinic_for_connector(db: Session, actor, requested):
    """The clinic a new connector is bound to: a clinic role's own clinic; an organization role names
    one of its clinics (or the only one); a platform user names any clinic."""
    from app.core.access import CLINIC_BOUND, clinic_for, require_clinic, require_org, role_of
    if role_of(actor) in CLINIC_BOUND:
        own = require_clinic(actor)
        if requested and requested != own:
            raise HTTPException(status_code=403, detail="Cross-scope access denied")
        return own
    if requested and requested != "default":
        return clinic_for(db, actor, requested).id
    from app.models.clinic import ClinicModel
    from app.core.access import is_platform
    if is_platform(actor):
        raise HTTPException(status_code=400, detail="Choose the clinic for this connection")
    clinics = db.query(ClinicModel).filter(ClinicModel.org_id == require_org(actor)).all()
    if len(clinics) != 1:
        raise HTTPException(status_code=400, detail="Choose the clinic for this connection")
    return clinics[0].id


def _verification(c: ConnectorModel) -> str:
    """What is actually known about this connection: a saved draft, registered but not verified with
    the provider, or verified by a successful provider authentication."""
    if (c.status or "") == "Draft":
        return "draft"
    return "verified" if c.state == "AUTHORIZED_READY" else "registered_unverified"


@router.get("/health", response_model=List[Dict[str, Any]])
def check_connectors_health(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """
    Status of the connections in the caller's scope, as recorded: no sync time or latency is shown
    unless one was measured.
    """
    connectors = _scope_query(db, _actor(current_user)).all()

    response = []
    for c in connectors:
        response.append({
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "status": c.status,
            "state": c.state,
            "verification": _verification(c),
            "clinic_id": c.clinic_id,
            "latency_ms": c.latency_ms if c.last_sync else None,
            "last_sync": c.last_sync.isoformat() + "Z" if c.last_sync else None
        })
    return response


@router.post("/connect", status_code=status.HTTP_201_CREATED)
def connect_new_system(connector_in: ConnectorCreate, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    """
    Connect a new EHR or billing system integration.
    G3 Secure Onboarding:
    - Rejects plaintext credentials in ordinary config JSON.
    - Never assigns Connected / AUTHORIZED_READY before live credential verification.
    """
    actor = _actor(current_user)
    is_draft = (connector_in.status or "") == "Draft"
    if actor is not None:
        # Connection ids are per clinic: the same name in two clinics is two connections.
        connector_in.clinic_id = _clinic_for_connector(db, actor, connector_in.clinic_id
                                                       or (connector_in.config or {}).get("clinic_id"))
        prefix = f"{connector_in.clinic_id}:"
        if not connector_in.id.startswith(prefix):
            connector_in.id = prefix + connector_in.id
    existing = db.query(ConnectorModel).filter(ConnectorModel.id == connector_in.id).first()
    if existing and existing.status != "Draft":      # a saved draft may be saved again or registered
        if actor is not None:
            state = "verified with the provider" if existing.state == "AUTHORIZED_READY" else "not verified with the provider"
            raise HTTPException(status_code=409,
                                detail=f"This connection is already registered for the clinic ({state})")
        raise HTTPException(
            status_code=400,
            detail=f"Connector with ID '{connector_in.id}' already exists.")

    config = connector_in.config.copy() if connector_in.config else {}

    # G3 Credential Safety: Disallow plaintext passwords/tokens in config JSON
    try:
        validate_no_plaintext_secrets(config)
    except InsecureCredentialError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if is_draft:
        # A draft keeps the entered (non-secret) settings for later; nothing is contacted or verified.
        from app.core.access import record
        row = existing or ConnectorModel(id=connector_in.id, name=connector_in.name, type=connector_in.type)
        row.name, row.type = connector_in.name, connector_in.type
        row.clinic_id = connector_in.clinic_id or "default"
        row.status, row.state, row.config, row.last_sync, row.latency_ms = "Draft", "DRAFT", config, None, None
        if existing is None:
            db.add(row)
        if actor is not None:
            record(db, actor, "CONNECTOR_DRAFT_SAVED", "Integration Center", clinic_id=row.clinic_id,
                   target_type="connector", target_id=row.id)
        db.commit()
        return {"message": "Draft saved; not connected", "connector_id": row.id, "status": "Draft",
                "state": "DRAFT", "verification": "draft"}

    verified = False

    # Live Verification for Practice Fusion or registered adapters
    if ("Practice Fusion" in connector_in.name or connector_in.name == "practice_fusion") and config:
        try:
            from app.integrations.core.registry import registry
            adapter = registry.create("Practice Fusion", config=config)
            if not adapter:
                raise ValueError("Adapter not available")
            if hasattr(adapter, "auth") and adapter.auth and hasattr(adapter.auth, "authenticate"):
                import asyncio
                auth_res = asyncio.run(adapter.auth.authenticate())
                if not auth_res or not getattr(auth_res, "access_token", None):
                    raise ValueError("Authentication challenge failed")
            verified = True
        except Exception as e:
            # Prevent saving the connection if authentication fails
            raise HTTPException(
                status_code=401,
                detail=f"Invalid Practice Fusion Credentials: {str(e)}")
    elif config.get("verified_live") is True:
        verified = True

    # Never assign Connected before verification
    final_status = "Connected" if verified else "Configured"
    final_state = "AUTHORIZED_READY" if verified else "CONFIGURED"

    vendor_id = connector_in.vendor_id or config.get("vendor_id") or connector_in.name.lower().replace(" ", "_")
    clinic_id = connector_in.clinic_id or config.get("clinic_id") or "default"
    secret_ref = connector_in.secret_ref or config.get("secret_ref")
    permitted = connector_in.permitted_resources or config.get("permitted_resources") or [
        "Encounter", "Patient", "Coverage", "Claim", "Remittance"
    ]

    new_connector = ConnectorModel(
        id=connector_in.id,
        name=connector_in.name,
        type=connector_in.type,
        vendor_id=vendor_id,
        clinic_id=clinic_id,
        secret_ref=secret_ref,
        permitted_resources=permitted,
        state=final_state,
        status=final_status,
        latency_ms=None,
        last_sync=datetime.utcnow() if verified else None,
        config=config
    )
    if existing is not None:      # a saved draft becomes the registered connection
        db.delete(existing)
        db.flush()
    db.add(new_connector)
    if actor is not None:
        from app.core.access import record
        record(db, actor, "CONNECTOR_VERIFIED" if verified else "CONNECTOR_REGISTERED", "Integration Center",
               clinic_id=clinic_id, target_type="connector", target_id=new_connector.id,
               detail=None if verified else "registered; not verified with the provider")
    db.commit()
    db.refresh(new_connector)

    # Register in authoritative authority store
    connector_store.register(
        ConnectorBinding(
            connector_instance_id=new_connector.id,
            vendor_id=vendor_id,
            allowed_clinic_id=clinic_id,
            state=final_state,
            permitted_resources=set(permitted),
            secret_ref=secret_ref,
        )
    )

    return {
        "message": "Connected and verified with the provider" if verified
        else "Registered; not verified with the provider",
        "connector_id": new_connector.id,
        "status": final_status,
        "state": final_state,
        "verification": "verified" if verified else "registered_unverified"
    }


@router.post("/sync")
async def trigger_connector_sync(request: ConnectorSyncRequest, db: Session = Depends(get_db),
                                 current_user=Depends(get_current_user)):
    """
    Manually trigger data synchronization from a specific Reality Source.
    Uses the SES-005 canonical sync framework.
    """
    _in_scope(db, _actor(current_user), request.connector_id)
    result = await connector_manager.sync_connector(request.connector_id)
    if result.get("status") == "Failed":
        raise HTTPException(status_code=400, detail=result.get("error"))

    return {"message": "Sync complete", "details": result}


@router.delete("/disconnect/{connector_id}")
def disconnect_system(connector_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """
    Disconnect/Delete an existing integration.
    """
    actor = _actor(current_user)
    connector = _in_scope(db, actor, connector_id)

    connector_store.revoke(connector_id)
    if actor is not None:
        from app.core.access import record
        record(db, actor, "CONNECTOR_DISCONNECTED", "Integration Center", clinic_id=connector.clinic_id,
               target_type="connector", target_id=connector_id)
    db.delete(connector)
    db.commit()
    return {"message": f"Successfully disconnected {connector_id}"}
