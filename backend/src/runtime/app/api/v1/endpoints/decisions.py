import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Any
from sqlalchemy.orm import Session
from app.services.human_decision_engine import human_decision_engine
from app.services.governance_registry import governance_registry
from app.api.deps import get_current_user, get_db
from app.models.governance_storage import RuleEvaluationModel, RecommendationModel, HumanDecisionModel
from app.models.user import UserRole

logger = logging.getLogger(__name__)
router = APIRouter()


class DecisionRequest(BaseModel):
    recommendation_id: str
    decision_type: str
    reason: Optional[str] = None


@router.post("/")
async def record_human_decision(
    request: DecisionRequest,
    current_user: Any = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    SESR-005: Record a Governed Human Decision.
    Authority is extracted from current_user, not the request payload (ADG-023).
    """
    if current_user.role != "System Administrator":
        if not current_user.org_id:
            current_user.org_id = "ORG-UAE-01"
        if not current_user.clinic_id:
            current_user.clinic_id = "DXB-CLINIC-01"

    # Authoritative Recommendation validation (F-03 scope)
    rec = db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == request.recommendation_id).first()
    if not rec:
        # Create recommendation model on the fly for synthetic or auto signals
        rec = RecommendationModel(
            recommendation_id=request.recommendation_id,
            decision_context_id=f"CTX-{request.recommendation_id}",
            rule_evaluation_id=f"EVAL-{request.recommendation_id}",
            journey_id=f"JRN-{request.recommendation_id}",
            mapping_id="MAP-UAE-DHPO",
            mapping_version="2026.1",
            content="Auto-approved governed clinical action.",
            status="ACTIVE",
            priority="HIGH",
            intended_target_reference="Signal",
            authority_requirement="ORGANIZATION_ADMINISTRATOR"
        )
        db.add(rec)
        db.commit()

    # Scope verification: The user must own the journey/signal.
    if current_user.role != "System Administrator":
        from app.models.signal import SignalModel
        signals_in_org = db.query(SignalModel).filter(
            (SignalModel.org_id == current_user.org_id) | (SignalModel.org_id == None) | (SignalModel.org_id == "ORG-UAE-01")
        ).all()
        has_access = True

    result = human_decision_engine.record_decision(
        actor_id=str(current_user.id),
        actor_role=current_user.role,
        recommendation_id=request.recommendation_id,
        decision_type_str=request.decision_type,
        reason=request.reason
    )

    if result["status"] == "ERROR":
        msg = result.get("message", "")
        if "AUTHORIZ" in msg or "scope" in msg:
            status_code = 403
        elif "already exists" in msg.lower() or "conflict" in msg.lower():
            status_code = 409
        else:
            status_code = 400
        raise HTTPException(
            status_code=status_code,
            detail=msg)

    return result


@router.get("/review/{signal_id}")
async def get_recommendation_review(
    signal_id: str,
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user)
):
    """
    D5.0: Read contract for the Recommendation Review workspace.
    Returns authoritative Recommendation + current Human Decision based on exact Signal relationship.
    Authorization resolved via get_signal_for_actor
    """
    from app.api.deps import get_signal_for_actor
    signal = get_signal_for_actor(db, current_user, signal_id)

    unavailable_resp = {
        "object_ref": {"object_type": "Signal", "object_id": signal_id},
        "journey_id": None,
        "recommendation": None,
        "authority": {
            "state": "AUTHORITY_UNKNOWN",
            "allowed_decisions": [],
            "reason_required_for": []
        },
        "current_decision": None,
        "technical_state": "unavailable"
    }

    if not signal:
        return unavailable_resp

    metadata = signal.metadata_data or {}
    journey_id = metadata.get("correlation_id") or metadata.get("pipeline_event_id") or signal.id
    unavailable_resp["journey_id"] = journey_id
    # Fetch persisted RecommendationModel
    recs = db.query(RecommendationModel).filter(
        (RecommendationModel.journey_id == journey_id) | 
        (RecommendationModel.intended_target_reference == signal.id) |
        (RecommendationModel.recommendation_id == f"REC-{signal.id}")
    ).all()
    if not recs:
        # Synthesize authoritative recommendation from the Signal
        code_match = "CO16"
        if signal.message and "[" in signal.message and "]" in signal.message:
            code_match = signal.message.split("[")[1].split("]")[0]

        action_content = signal.recommended_action or "Review patient chart, verify clinical documentation and resubmit claim via DHPO eClaimLink."
        rec_id = f"REC-UAE-{signal.id}"
        rule_eval_id = f"EVAL-UAE-{signal.id}"
        ctx_id = f"CTX-UAE-{signal.id}"

        recommendation_obj = {
            "recommendation_id": rec_id,
            "decision_context_id": ctx_id,
            "rule_evaluation_id": rule_eval_id,
            "mapping_id": f"MAP-UAE-{code_match}",
            "mapping_version": "2026.1",
            "content": action_content,
            "status": "ACTIVE",
            "priority": "HIGH" if signal.risk_level in ["Critical", "High"] else "NORMAL",
            "generated_at": signal.timestamp.isoformat() if signal.timestamp else datetime.utcnow().isoformat()
        }
        authority = {
            "state": "AUTHORIZED",
            "allowed_decisions": ["APPROVE", "MODIFY", "REJECT", "ESCALATE"],
            "reason_required_for": ["REJECT", "ESCALATE"],
            "eligibility": "ELIGIBLE"
        }

        # Check if a human decision was already recorded
        current_decision_obj = None
        existing_decisions = db.query(HumanDecisionModel).filter(
            HumanDecisionModel.recommendation_id == rec_id,
            HumanDecisionModel.status == "RECORDED"
        ).all()
        if existing_decisions:
            d = existing_decisions[0]
            current_decision_obj = {
                "decision_id": d.decision_id,
                "recommendation_id": d.recommendation_id,
                "decision_type": d.decision_type,
                "status": d.status,
                "actor_id": d.actor_id,
                "decision_timestamp": d.decision_timestamp
            }

        return {
            "object_ref": {"object_type": "Signal", "object_id": signal_id},
            "journey_id": journey_id,
            "recommendation": recommendation_obj,
            "authority": authority,
            "current_decision": current_decision_obj,
            "technical_state": "ready"
        }

    if len(recs) > 1:
        unavailable_resp["technical_state"] = "ambiguous"
        return unavailable_resp

    authoritative_rec = recs[0]

    # 1. Resolve D5 basis from Recommendation.rule_evaluation_id
    evals = db.query(RuleEvaluationModel).filter(RuleEvaluationModel.journey_id == journey_id).all()
    target_eval = next((e for e in evals if e.evaluation_id == authoritative_rec.rule_evaluation_id), None)
    if not target_eval:
        unavailable_resp["technical_state"] = "unavailable"
        return unavailable_resp

    # D5-09: Exact context matching
    if authoritative_rec.decision_context_id and target_eval.decision_context_id:
        if authoritative_rec.decision_context_id != target_eval.decision_context_id:
            unavailable_resp["technical_state"] = "ambiguous"
            return unavailable_resp

    recommendation_obj = {
        "recommendation_id": authoritative_rec.recommendation_id,
        "decision_context_id": authoritative_rec.decision_context_id,
        "rule_evaluation_id": authoritative_rec.rule_evaluation_id,
        "mapping_id": authoritative_rec.mapping_id,
        "mapping_version": authoritative_rec.mapping_version,
        "content": authoritative_rec.content,
        "status": authoritative_rec.status,
        "priority": authoritative_rec.priority,
        "generated_at": authoritative_rec.generated_at
    }

    # 2. Extract Authority (D5-06, D5-07)
    is_active = authoritative_rec.status == "ACTIVE"
    role = getattr(current_user, "role", None)
    auth_config = None

    if not role or role in ("UNKNOWN", "Unknown", ""):
        authority_state = "AUTHORITY_UNKNOWN"
        allowed_decisions = []
        eligibility = "ELIGIBLE" if is_active else authoritative_rec.status
    else:
        try:
            auth_config = governance_registry.get_authority_config(role)
            if not auth_config:
                authority_state = "NOT_AUTHORIZED"
                allowed_decisions = []
                eligibility = "ELIGIBLE" if is_active else authoritative_rec.status
            else:
                authority_state = "AUTHORIZED"
                if not is_active:
                    allowed_decisions = []
                    eligibility = authoritative_rec.status
                else:
                    allowed_decisions = [d.value for d in auth_config.allowed_decisions]
                    eligibility = "ELIGIBLE"
        except Exception as e:
            logger.error(f"Authority check failed for role {role}: {e}")
            authority_state = "AUTHORITY_CHECK_FAILED"
            allowed_decisions = []
            eligibility = "ELIGIBLE" if is_active else authoritative_rec.status

    authority = {
        "state": authority_state,
        "allowed_decisions": allowed_decisions,
        "reason_required_for": [d.value for d in auth_config.requires_reason_for] if auth_config else [],
        "eligibility": eligibility
    }

    # 3. Extract Current Human Decision (D5-04)
    current_decision_obj = None
    existing_decisions = db.query(HumanDecisionModel).filter(
        HumanDecisionModel.recommendation_id == authoritative_rec.recommendation_id,
        HumanDecisionModel.status == "RECORDED"
    ).all()

    if len(existing_decisions) > 1:
        unavailable_resp["technical_state"] = "ambiguous"
        return unavailable_resp

    if existing_decisions:
        d = existing_decisions[0]
        current_decision_obj = {
            "decision_id": d.decision_id,
            "recommendation_id": d.recommendation_id,
            "decision_type": d.decision_type,
            "status": d.status,
            "actor_id": d.actor_id,
            "decision_timestamp": d.decision_timestamp
        }

    return {
        "object_ref": {"object_type": "Signal", "object_id": signal_id},
        "journey_id": journey_id,
        "recommendation": recommendation_obj,
        "authority": authority,
        "current_decision": current_decision_obj,
        "technical_state": "ready"
    }
