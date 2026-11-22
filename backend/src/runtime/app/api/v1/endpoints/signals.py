from app.models.user import UserRole
from app.api.deps import RoleChecker
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
import asyncio

from app.services.simulation_engine import simulation_engine
from app.db.database import get_db
from app.models.signal import SignalModel

router = APIRouter()


@router.get("")
@router.get("/")
def get_historical_signals(db: Session = Depends(get_db),
                           current_user=Depends(RoleChecker([UserRole.CLINIC_MANAGER.value,
                                                             UserRole.ORGANIZATION_ADMINISTRATOR.value,
                                                             UserRole.SYSTEM_ADMINISTRATOR.value]))):
    signals = db.query(SignalModel).order_by(SignalModel.timestamp.desc()).limit(100).all()

    # Authoritative resolution of recorded decisions
    from app.models.governance_storage import RecommendationModel, HumanDecisionModel
    decisions_by_rec = {}
    try:
        recorded_decisions = db.query(HumanDecisionModel).filter(HumanDecisionModel.status == "RECORDED").all()
        for d in recorded_decisions:
            decisions_by_rec[d.recommendation_id] = d

        rec_by_signal = {}
        recs = db.query(RecommendationModel).all()
        for r in recs:
            if r.intended_target_reference:
                rec_by_signal[r.intended_target_reference] = r
            if r.journey_id:
                rec_by_signal[r.journey_id] = r
            rec_by_signal[r.recommendation_id] = r
    except Exception:
        rec_by_signal = {}
        decisions_by_rec = {}

    result = []
    for s in signals:
        meta = s.metadata_data or {}
        jid = meta.get("correlation_id") or meta.get("pipeline_event_id")
        rec = rec_by_signal.get(s.id) or (rec_by_signal.get(jid) if jid else None) or rec_by_signal.get(f"REC-{s.id}")
        dec = decisions_by_rec.get(rec.recommendation_id) if rec else None
        decision_status = dec.decision_type if dec else None

        import re
        from app.core.config import get_deployment_currency
        curr = meta.get("currency") or get_deployment_currency()
        raw_exp = s.estimated_financial_exposure or ""
        m = re.search(r'([0-9]+(?:\.[0-9]+)?)', raw_exp)
        exp_amount = float(m.group(1)) if m else None

        result.append({
            "id": s.id,
            "source": s.source,
            "type": s.type,
            "message": s.message,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            "metadata": s.metadata_data,
            "risk_level": s.risk_level,
            "problem": s.problem,
            "reason": s.reason,
            "business_impact": s.business_impact,
            "recommended_action": s.recommended_action,
            "expected_outcome": s.expected_outcome,
            "primary_context": s.primary_context,
            "secondary_context": s.secondary_context,
            "context_confidence": s.context_confidence,
            "context_reason": s.context_reason,
            "revenue_risk_category": s.revenue_risk_category,
            "estimated_financial_exposure": s.estimated_financial_exposure,
            "exposure_amount": exp_amount,
            "currency": curr,
            "revenue_confidence": s.revenue_confidence,
            "operational_dependency": s.operational_dependency,
            "explainability_log": getattr(s, "explainability_log", None),
            "priority_score": getattr(s, "priority_score", None),
            "status": "APPROVED" if decision_status == "APPROVED" else ("REJECTED" if decision_status == "REJECTED" else "active"),
            "decision_status": decision_status,
            "decision_id": dec.decision_id if dec else None
        })
    return result


@router.on_event("startup")
async def startup_event():
    from app.core.config import settings
    if getattr(settings, "SYNTHETIC_TEST_ENABLED", False) and getattr(settings, "ENVIRONMENT", "").upper() != "PRODUCTION":
        simulation_engine.start()


@router.on_event("shutdown")
async def shutdown_event():
    simulation_engine.stop()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    from app.core.config import settings
    if not origin or origin not in settings.ALLOWED_WS_ORIGINS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q = asyncio.Queue()
    simulation_engine.add_client(q)
    try:
        while True:
            # Wait for new message from the queue
            message = await q.get()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        simulation_engine.remove_client(q)
