from app.models.user import UserRole
from app.api.deps import RoleChecker
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from sqlalchemy.orm import Session
import asyncio
import json

from app.services.simulation_engine import simulation_engine
from app.db.database import get_db
from app.models.signal import SignalModel
from app.models.governance_storage import RecommendationModel, HumanDecisionModel
from app.services.risk_level_evaluator import current_evaluation, current_level

router = APIRouter()


def _scoped_signal_query(db: Session, actor):
    """Signals visible to the actor: all for System Administrators, otherwise the
    actor's organization, narrowed to the actor's clinic for clinic-scoped roles."""
    from app.core.config import settings
    q = db.query(SignalModel)
    if getattr(settings, "SOURCE_PROFILE", "") == "workbook" or not getattr(settings, "ENABLE_PRACTICE_FUSION", False):
        q = q.filter(~SignalModel.source.ilike("%practice%"))
    if actor.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        return q
    org_id = getattr(actor, "org_id", None)
    if not org_id or org_id == "UNASSIGNED":
        raise HTTPException(status_code=403, detail="Missing required organization scope")
    q = q.filter(SignalModel.org_id == org_id)
    if actor.role == UserRole.CLINIC_MANAGER.value:
        clinic_id = getattr(actor, "clinic_id", None)
        if not clinic_id:
            raise HTTPException(status_code=403, detail="Missing required clinic scope")
        q = q.filter(SignalModel.clinic_id == clinic_id)
    return q


def _journey_id(signal: SignalModel):
    metadata = signal.metadata_data or {}
    return metadata.get("correlation_id") or metadata.get("pipeline_event_id")


def _recorded_decisions_by_journey(db: Session, signals):
    """Map journey_id -> latest recorded human decision for the signals' recommendations."""
    journey_ids = {j for j in (_journey_id(s) for s in signals) if j}
    if not journey_ids:
        return {}
    recs = db.query(RecommendationModel).filter(RecommendationModel.journey_id.in_(journey_ids)).all()
    rec_to_journey = {r.recommendation_id: r.journey_id for r in recs}
    if not rec_to_journey:
        return {}
    decisions = db.query(HumanDecisionModel).filter(
        HumanDecisionModel.recommendation_id.in_(list(rec_to_journey)),
        HumanDecisionModel.status == "RECORDED",
    ).all()
    result = {}
    for d in decisions:
        journey = rec_to_journey.get(d.recommendation_id)
        current = result.get(journey)
        if current is None or str(d.decision_timestamp or "") > str(current["decision_timestamp"] or ""):
            result[journey] = {
                "decision_id": d.decision_id,
                "recommendation_id": d.recommendation_id,
                "decision_type": d.decision_type,
                "decision_timestamp": d.decision_timestamp,
            }
    return result


RECENT_SIGNAL_LIMIT = 100
OPEN_SIGNAL_LIMIT = 500
_NOT_ACTIONABLE = ("None required.", "Monitor progress.", "NO_RECOMMENDATION", "")


def _review_status(signal: SignalModel) -> str:
    return ((signal.metadata_data or {}).get("review_state") or {}).get("status", "active")


def _is_open(signal: SignalModel, decisions) -> bool:
    """Open = no human decision recorded and not dismissed."""
    return decisions.get(_journey_id(signal)) is None and _review_status(signal) != "dismissed"


@router.get("/summary")
def get_signal_summary(db: Session = Depends(get_db),
                       current_user=Depends(RoleChecker([UserRole.CLINIC_MANAGER.value,
                                                         UserRole.ORGANIZATION_ADMINISTRATOR.value,
                                                         UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """Dashboard card counts over every signal in the user's scope (not only the listed ones)."""
    signals = _scoped_signal_query(db, current_user).all()
    decisions = _recorded_decisions_by_journey(db, signals)
    open_signals = [s for s in signals if _is_open(s, decisions)]
    return {
        "active_signals": len(open_signals),
        "patient_flow": sum(1 for s in signals if s.type in ("EHR", "Patient")),
        "critical_events": sum(1 for s in open_signals
                               if current_level(s.risk_level, s.metadata_data) in ("Critical", "High")),
        "actions_taken": sum(1 for s in signals if decisions.get(_journey_id(s)) is not None),
    }


@router.get("")
@router.get("/")
def get_historical_signals(db: Session = Depends(get_db),
                           current_user=Depends(RoleChecker([UserRole.CLINIC_MANAGER.value,
                                                             UserRole.ORGANIZATION_ADMINISTRATOR.value,
                                                             UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """The newest signals plus every open signal that carries a recommendation, so open
    items are never pushed out of the list by newer events."""
    scoped = _scoped_signal_query(db, current_user)
    recent = scoped.order_by(SignalModel.timestamp.desc()).limit(RECENT_SIGNAL_LIMIT).all()
    recent_ids = {s.id for s in recent}
    candidates = (scoped.filter(SignalModel.recommended_action.isnot(None),
                                SignalModel.recommended_action.notin_(_NOT_ACTIONABLE))
                  .order_by(SignalModel.priority_score.desc(), SignalModel.timestamp.desc())
                  .all())
    candidates = [s for s in candidates if s.id not in recent_ids]
    candidate_decisions = _recorded_decisions_by_journey(db, candidates)
    older_open = [s for s in candidates if _is_open(s, candidate_decisions)][:OPEN_SIGNAL_LIMIT]
    signals = recent + older_open
    decisions = _recorded_decisions_by_journey(db, signals)
    result = []
    for s in signals:
        meta = s.metadata_data or {}
        dec = decisions.get(_journey_id(s))
        decision_status = dec["decision_type"] if dec else None

        import re
        from app.core.config import get_deployment_currency
        curr = getattr(s, "exposure_currency", None) or meta.get("currency") or get_deployment_currency()
        raw_exp = s.estimated_financial_exposure or ""
        exp_amount = getattr(s, "exposure_amount", None)
        if exp_amount is None and raw_exp:
            m = re.search(r'([0-9]+(?:\.[0-9]+)?)', raw_exp)
            exp_amount = float(m.group(1)) if m else None
        # Re-evaluated now from the stored source inputs: level, time left, problem, reason and action.
        risk = current_evaluation(meta)

        origin = meta.get("origin")
        if not origin:
            if s.source in ("UAE Billing Workbook", "DHPO", "Sentinel UAE Governance Engine") or "WB" in str(s.id) or meta.get("claim_id"):
                origin = {
                    "kind": "workbook",
                    "import_id": "REC-WB-DXB-01",
                    "sheet": "Claims" if (meta.get("claim_id") or s.type == "Claim") else "Encounters",
                    "external_record_id": meta.get("claim_id") or meta.get("encounter_id") or s.id,
                    "connector_instance_id": None
                }
            elif "Practice Fusion" in str(s.source) or "Twilio" in str(s.source) or "Outlook" in str(s.source):
                origin = {
                    "kind": "simulator",
                    "import_id": None,
                    "sheet": None,
                    "external_record_id": s.id,
                    "connector_instance_id": None
                }
            else:
                origin = {
                    "kind": "unknown",
                    "import_id": None,
                    "sheet": None,
                    "external_record_id": s.id,
                    "connector_instance_id": None
                }

        result.append({
            "decision": dec,
            "id": s.id,
            "origin": origin,
            "source": s.source,
            "type": s.type,
            "message": s.message,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            "metadata": s.metadata_data,
            "risk_level": risk["level"] if risk else current_level(s.risk_level, meta),
            "risk_evaluation": risk,
            "problem": risk["problem"] if risk else s.problem,
            "reason": risk["reason"] if risk else s.reason,
            "business_impact": s.business_impact,
            "recommended_action": risk["recommended_action"] if risk else s.recommended_action,
            "expected_outcome": risk["expected_outcome"] if risk else s.expected_outcome,
            "primary_context": s.primary_context,
            "secondary_context": s.secondary_context,
            "context_confidence": s.context_confidence,
            "context_reason": s.context_reason,
            "revenue_risk_category": s.revenue_risk_category,
            "estimated_financial_exposure": s.estimated_financial_exposure,
            "exposure_amount": exp_amount,
            "exposure_currency": curr,
            "currency": curr,
            "revenue_confidence": s.revenue_confidence,
            "operational_dependency": s.operational_dependency,
            "explainability_log": getattr(s, "explainability_log", None),
            "priority_score": getattr(s, "priority_score", None),
            # Review state (active / dismissed) and the recorded human decision, kept separate.
            "status": _review_status(s),
            "decision_status": decision_status,
            "decision_id": dec["decision_id"] if dec else None,
        })
    return result


@router.get("/daily-stats")
def get_daily_signal_stats(days: int = 7, db: Session = Depends(get_db),
                           current_user=Depends(RoleChecker([UserRole.CLINIC_MANAGER.value,
                                                             UserRole.ORGANIZATION_ADMINISTRATOR.value,
                                                             UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """Per-day counts for the dashboard cards over the last `days` days (UTC, today last):
    signals received, EHR/patient signals, critical or high signals, and human decisions recorded."""
    from datetime import datetime, timedelta, timezone
    days = max(2, min(days, 31))
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=days - 1)
    buckets = {(first_day + timedelta(days=i)).isoformat(): {"signals": 0, "patient_flow": 0, "critical": 0, "decisions": 0}
               for i in range(days)}

    scoped = _scoped_signal_query(db, current_user)
    window_start = datetime.combine(first_day, datetime.min.time())
    for s in scoped.filter(SignalModel.timestamp >= window_start).all():
        key = s.timestamp.date().isoformat() if s.timestamp else None
        if key not in buckets:
            continue
        buckets[key]["signals"] += 1
        if s.type in ("EHR", "Patient"):
            buckets[key]["patient_flow"] += 1
        if current_level(s.risk_level, s.metadata_data) in ("Critical", "High"):
            buckets[key]["critical"] += 1

    journeys = {j for j in (_journey_id(s) for s in scoped.all()) if j}
    if journeys:
        rec_ids = [r.recommendation_id for r in
                   db.query(RecommendationModel).filter(RecommendationModel.journey_id.in_(journeys)).all()]
        if rec_ids:
            for d in db.query(HumanDecisionModel).filter(HumanDecisionModel.recommendation_id.in_(rec_ids),
                                                         HumanDecisionModel.status == "RECORDED").all():
                key = str(d.decision_timestamp or "")[:10]
                if key in buckets:
                    buckets[key]["decisions"] += 1

    return {"days": [{"date": k, **v} for k, v in buckets.items()]}


_REVIEW_ROLES = [UserRole.CLINIC_MANAGER.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
                 UserRole.SYSTEM_ADMINISTRATOR.value]


def _record_review_state(db: Session, actor, signal_id: str, status: str, label: str):
    """Store an acknowledge/dismiss on the signal (by whom, when) and write an audit entry
    under the signed-in user. This is not a governed human decision on the recommendation."""
    from datetime import datetime, timezone
    from app.services.data_audit_engine import data_audit_engine
    signal = _scoped_signal_query(db, actor).filter(SignalModel.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found or out of scope")
    metadata = dict(signal.metadata_data or {})
    metadata["review_state"] = {
        "status": status,
        "by": actor.email,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    signal.metadata_data = metadata  # reassign so the JSON change is persisted
    data_audit_engine._log_internal(
        db, user_system=actor.email, action=f"{label} signal {signal_id}",
        module="SignalReview", correlation_id=signal_id, should_commit=False)
    db.commit()
    return {"id": signal_id, "status": status, "by": actor.email, "at": metadata["review_state"]["at"]}


@router.post("/{signal_id}/acknowledge")
def acknowledge_signal(signal_id: str, db: Session = Depends(get_db),
                       current_user=Depends(RoleChecker(_REVIEW_ROLES))):
    return _record_review_state(db, current_user, signal_id, "acknowledged", "Acknowledged")


@router.post("/{signal_id}/dismiss")
def dismiss_signal(signal_id: str, db: Session = Depends(get_db),
                   current_user=Depends(RoleChecker(_REVIEW_ROLES))):
    return _record_review_state(db, current_user, signal_id, "dismissed", "Dismissed")


@router.post("/{signal_id}/evidence-retrieval")
def retrieve_signal_evidence(signal_id: str, db: Session = Depends(get_db),
                             current_user=Depends(RoleChecker(_REVIEW_ROLES))):
    """Collects the evidence the payer needs for this signal's denied claim from the connected
    systems (EHR first, then the other sources) and stores the result on the signal. Anything that
    cannot be found raises a bell notification."""
    from app.services.evidence_retrieval import retrieve_claim_evidence
    signal = _scoped_signal_query(db, current_user).filter(SignalModel.id == signal_id).first()
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    retrieval = retrieve_claim_evidence(db, signal, requested_by=current_user.email)
    return {"signal_id": signal_id, "retrieval": retrieval,
            "risk_evaluation": current_evaluation(signal.metadata_data or {})}


@router.on_event("startup")
async def startup_event():
    from app.core.config import settings
    if (getattr(settings, "SYNTHETIC_TEST_ENABLED", False)
            and getattr(settings, "ENABLE_PRACTICE_FUSION", False)
            and getattr(settings, "SOURCE_PROFILE", "") != "workbook"
            and getattr(settings, "ENVIRONMENT", "").upper() != "PRODUCTION"):
        simulation_engine.start()


@router.on_event("shutdown")
async def shutdown_event():
    simulation_engine.stop()


# Mounted without the router-level bearer-header dependency: browsers cannot send an
# Authorization header on a WebSocket, so the session token arrives as a query parameter
# and is validated here with the same rules as get_current_user.
ws_router = APIRouter()

_STREAM_ROLES = {UserRole.CLINIC_MANAGER.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
                 UserRole.SYSTEM_ADMINISTRATOR.value}


def _stream_visible_to(actor, message: str) -> bool:
    """Same scope rule as the signals list: the actor's organization (and clinic for clinic-scoped roles)."""
    from app.core.config import settings
    if getattr(settings, "SOURCE_PROFILE", "") == "workbook" or not getattr(settings, "ENABLE_PRACTICE_FUSION", False):
        if "practice fusion" in message.lower():
            return False
    if actor["role"] == UserRole.SYSTEM_ADMINISTRATOR.value:
        return True
    try:
        metadata = (json.loads(message) or {}).get("metadata") or {}
    except (ValueError, AttributeError):
        return False
    if not actor["org_id"] or metadata.get("org_id") != actor["org_id"]:
        return False
    if actor["role"] == UserRole.CLINIC_MANAGER.value:
        return bool(actor["clinic_id"]) and metadata.get("clinic_id") == actor["clinic_id"]
    return True


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = ""):
    origin = websocket.headers.get("origin")
    from app.core.config import settings
    from app.api.deps import get_current_user
    from app.db.database import SessionLocal
    if not origin or origin not in settings.ALLOWED_WS_ORIGINS:
        await websocket.close(code=1008)
        return
    db = SessionLocal()
    try:
        user = get_current_user(db=db, token=token)
        actor = {"role": user.role, "org_id": getattr(user, "org_id", None),
                 "clinic_id": getattr(user, "clinic_id", None)}
    except HTTPException:
        await websocket.close(code=1008)
        return
    finally:
        db.close()
    if actor["role"] not in _STREAM_ROLES:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q = asyncio.Queue()
    simulation_engine.add_client(q)
    try:
        while True:
            # Wait for new message from the queue
            message = await q.get()
            if _stream_visible_to(actor, message):
                await websocket.send_text(message)
    except WebSocketDisconnect:
        pass
    finally:
        simulation_engine.remove_client(q)
