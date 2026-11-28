from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from pydantic import BaseModel

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


STALE_AFTER = timedelta(hours=24)     # a provider check older than this no longer proves the connection


def latest_probe(db: Session, connector_id: str):
    from app.models.integration_evidence import ConnectorProbeModel
    return db.query(ConnectorProbeModel).filter(ConnectorProbeModel.connector_id == connector_id) \
        .order_by(ConnectorProbeModel.probed_at.desc()).first()


def verification_of(db: Session, c: ConnectorModel) -> Dict[str, Any]:
    """What is actually known about a connection, from its newest provider check only (PR #8 A7):
    draft, registered (no check, or the check could not run), verified, failed, or stale."""
    if (c.status or "") == "Draft":
        return {"verification": "draft", "verified_at": None, "detail": None}
    probe = latest_probe(db, c.id)
    if probe is None:
        return {"verification": "registered_unverified", "verified_at": None, "detail": "No provider check yet"}
    when = probe.probed_at.isoformat() + "Z"
    if probe.outcome == "verified":
        if datetime.utcnow() - probe.probed_at > STALE_AFTER:
            return {"verification": "stale", "verified_at": when, "detail": "The last provider check is out of date"}
        return {"verification": "verified", "verified_at": when, "detail": None}
    if probe.outcome == "failed":
        return {"verification": "failed", "verified_at": None, "detail": probe.detail, "checked_at": when}
    return {"verification": "registered_unverified", "verified_at": None,
            "detail": probe.detail or "The provider check is not available", "checked_at": when}


def run_probe(c: ConnectorModel):
    """A server-side provider check: the provider adapter must exist and its authentication must
    return a token. Returns (outcome, detail); never "verified" without that result."""
    try:
        from app.integrations.core.registry import registry
        adapter = registry.create(c.name, config=dict(c.config or {}))
    except Exception as e:  # the adapter itself could not be built
        return "unavailable", f"Provider adapter could not start: {e}"
    if not adapter:
        return "unavailable", "No provider adapter for this connection"
    auth = getattr(adapter, "auth", None)
    if auth is None or not hasattr(auth, "authenticate"):
        return "unavailable", "This provider has no authentication check"
    try:
        import asyncio
        result = asyncio.run(auth.authenticate())
    except Exception as e:
        return "failed", f"Provider authentication failed: {e}"
    if not result or not getattr(result, "access_token", None):
        return "failed", "Provider authentication returned no token"
    return "verified", None


def probe_and_record(db: Session, c: ConnectorModel, actor=None, probe=None) -> Dict[str, Any]:
    """Run the provider check, store it (clinic, connector, time, outcome) and set the connection's
    state from it. Both integration screens read this same record."""
    from app.models.integration_evidence import ConnectorProbeModel
    outcome, detail = (probe or run_probe)(c)
    db.add(ConnectorProbeModel(connector_id=c.id, clinic_id=c.clinic_id, probed_at=datetime.utcnow(), outcome=outcome,
                               detail=detail, requested_by=getattr(actor, "email", None)))
    if outcome == "verified":
        c.state, c.status = "AUTHORIZED_READY", "Connected"
    else:
        c.state, c.status = "CONFIGURED", "Configured"
    db.flush()
    return verification_of(db, c)


def connector_view(db: Session, c: ConnectorModel) -> Dict[str, Any]:
    from app.models.integration_evidence import ConnectorSyncRunModel
    last_ok = db.query(ConnectorSyncRunModel).filter(ConnectorSyncRunModel.connector_id == c.id,
                                                     ConnectorSyncRunModel.status == "succeeded") \
        .order_by(ConnectorSyncRunModel.finished_at.desc()).first()
    v = verification_of(db, c)
    return {
        "id": c.id,
        "name": c.name,
        "type": c.type,
        "status": c.status,
        "state": c.state,
        "clinic_id": c.clinic_id,
        **v,
        "latency_ms": c.latency_ms if last_ok else None,
        "last_sync": last_ok.finished_at.isoformat() + "Z" if last_ok else None,
    }


@router.get("/health", response_model=List[Dict[str, Any]])
def check_connectors_health(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """
    Status of the connections in the caller's scope, as recorded: verified only by a provider check,
    synced only by a completed sync run.
    """
    return [connector_view(db, c) for c in _scope_query(db, _actor(current_user)).all()]


@router.post("/connect", status_code=status.HTTP_201_CREATED)
def connect_new_system(connector_in: ConnectorCreate, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    """
    Connect a new EHR or billing system integration.
    G3 Secure Onboarding:
    - Rejects plaintext credentials in ordinary config JSON.
    - Never assigns Connected / AUTHORIZED_READY without a completed server-side provider check.
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
            known = verification_of(db, existing)["verification"]
            state = "verified with the provider" if known == "verified" else "not verified with the provider"
            raise HTTPException(status_code=409,
                                detail=f"This connection is already registered for the clinic ({state})")
        raise HTTPException(
            status_code=400,
            detail=f"Connector with ID '{connector_in.id}' already exists.")

    config = connector_in.config.copy() if connector_in.config else {}
    # A verification claim cannot be supplied by the caller (PR #8 A7).
    config.pop("verified_live", None)

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
        state="CONFIGURED",
        status="Configured",
        latency_ms=None,
        last_sync=None,
        config=config
    )
    if existing is not None:      # a saved draft becomes the registered connection
        db.delete(existing)
        db.flush()
    db.add(new_connector)
    db.flush()
    v = probe_and_record(db, new_connector, actor)
    verified = v["verification"] == "verified"
    if actor is not None:
        from app.core.access import record
        record(db, actor, "CONNECTOR_VERIFIED" if verified else "CONNECTOR_REGISTERED", "Integration Center",
               clinic_id=clinic_id, target_type="connector", target_id=new_connector.id,
               detail=None if verified else f"registered; not verified with the provider ({v.get('detail')})")
    db.commit()
    db.refresh(new_connector)

    # Register in authoritative authority store
    connector_store.register(
        ConnectorBinding(
            connector_instance_id=new_connector.id,
            vendor_id=vendor_id,
            allowed_clinic_id=clinic_id,
            state=new_connector.state,
            permitted_resources=set(permitted),
            secret_ref=secret_ref,
        )
    )

    return {
        "message": "Connected and verified with the provider" if verified
        else "Registered; not verified with the provider",
        "connector_id": new_connector.id,
        "status": new_connector.status,
        "state": new_connector.state,
        "verification": v["verification"],
        "detail": v.get("detail"),
    }


@router.post("/verify/{connector_id}")
def verify_connection(connector_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Run the provider check again for a connection in the caller's scope and store its result."""
    actor = _actor(current_user)
    c = _in_scope(db, actor, connector_id)
    if (c.status or "") == "Draft":
        raise HTTPException(status_code=409, detail="A draft is not registered yet; activate it first")
    v = probe_and_record(db, c, actor)
    if actor is not None:
        from app.core.access import record
        record(db, actor, "CONNECTOR_CHECKED", "Integration Center", clinic_id=c.clinic_id, target_type="connector",
               target_id=c.id, detail=f"provider check: {v['verification']}")
    db.commit()
    return connector_view(db, c)


def _imported_count(result: Dict[str, Any]) -> int:
    for key in ("imported_count", "records_imported", "records_processed", "count"):
        if isinstance(result.get(key), int):
            return result[key]
    return 0


async def _run_sync(db: Session, c: ConnectorModel, actor) -> Dict[str, Any]:
    """One recorded sync run for a connection: started, finished, status, imported count or failure.
    A successful run also records the import it brought in."""
    from app.models.integration_evidence import ConnectorSyncRunModel, DataImportModel
    run = ConnectorSyncRunModel(connector_id=c.id, clinic_id=c.clinic_id, started_at=datetime.utcnow(),
                                status="running", requested_by=getattr(actor, "email", None))
    db.add(run)
    db.commit()
    result = await connector_manager.sync_connector(c.id)
    db.refresh(run)
    run.finished_at = datetime.utcnow()
    if result.get("status") == "Success":
        run.status, run.imported_count = "succeeded", _imported_count(result)
        db.add(DataImportModel(clinic_id=c.clinic_id, source_kind="connector", source_label=c.type or c.name,
                               connector_id=c.id, run_id=run.id, accepted_count=run.imported_count,
                               completed_at=run.finished_at, recorded_by=run.requested_by))
    else:
        run.status, run.error = "failed", str(result.get("error") or "The provider sync failed")
    if actor is not None:
        from app.core.access import record
        record(db, actor, "CONNECTOR_SYNC_" + run.status.upper(), "Integration Center", clinic_id=c.clinic_id,
               target_type="sync_run", target_id=run.id,
               detail=f"imported {run.imported_count}" if run.status == "succeeded" else run.error)
    db.commit()
    return _run_view(run)


def _run_view(run) -> Dict[str, Any]:
    if run is None:
        return None

    def iso(d):
        return d.isoformat() + "Z" if d else None
    return {"run_id": run.id, "connector_id": run.connector_id, "clinic_id": run.clinic_id, "status": run.status,
            "started_at": iso(run.started_at), "finished_at": iso(run.finished_at),
            "imported_count": run.imported_count, "error": run.error}


@router.post("/sync")
async def trigger_connector_sync(request: ConnectorSyncRequest, db: Session = Depends(get_db),
                                 current_user=Depends(get_current_user)):
    """
    Manually trigger data synchronization from a specific Reality Source.
    Uses the SES-005 canonical sync framework; every run is recorded.
    """
    actor = _actor(current_user)
    c = _in_scope(db, actor, request.connector_id)
    run = await _run_sync(db, c, actor)
    if run["status"] != "succeeded":
        raise HTTPException(status_code=400, detail=run["error"])
    return {"message": "Sync complete", "run": run}


class ClinicSyncRequest(BaseModel):
    clinic_id: Optional[str] = None


def _sync_status(db: Session, clinic_id: str) -> Dict[str, Any]:
    from app.models.integration_evidence import ConnectorSyncRunModel
    connectors = db.query(ConnectorModel).filter(ConnectorModel.clinic_id == clinic_id,
                                                 ConnectorModel.status != "Draft").all()
    runs = db.query(ConnectorSyncRunModel).filter(ConnectorSyncRunModel.clinic_id == clinic_id)
    last_run = runs.order_by(ConnectorSyncRunModel.started_at.desc()).first()
    last_ok = runs.filter(ConnectorSyncRunModel.status == "succeeded") \
        .order_by(ConnectorSyncRunModel.finished_at.desc()).first()
    return {"clinic_id": clinic_id, "configured": bool(connectors),
            "connections": [connector_view(db, c) for c in connectors],
            "last_run": _run_view(last_run), "last_success": _run_view(last_ok)}


@router.get("/sync-status")
def clinic_sync_status(clinic_id: Optional[str] = None, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    """The clinic's connections, its last sync run and its last successful one (never demo data)."""
    clinic = _clinic_for_connector(db, current_user, clinic_id)
    return _sync_status(db, clinic)


@router.post("/sync-clinic")
async def sync_clinic(request: ClinicSyncRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """"Sync Live Data" for one clinic: runs its verified connection and records the run. A clinic with
    no connection, or only an unverified one, is never shown as synced (PR #8 A6)."""
    actor = _actor(current_user)
    clinic = _clinic_for_connector(db, actor, request.clinic_id)
    connectors = db.query(ConnectorModel).filter(ConnectorModel.clinic_id == clinic,
                                                 ConnectorModel.status != "Draft").all()
    if not connectors:
        raise HTTPException(status_code=409, detail="No connection is configured for this clinic; nothing was synced.")
    ready = [c for c in connectors if verification_of(db, c)["verification"] == "verified"]
    if not ready:
        raise HTTPException(status_code=409,
                            detail="The clinic's connection is not verified with the provider; nothing was synced.")
    runs = [await _run_sync(db, c, actor) for c in ready]
    status_now = _sync_status(db, clinic)
    return {"runs": runs, **status_now}


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
