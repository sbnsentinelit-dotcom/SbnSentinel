from typing import Dict, Any
from app.services.base_service import BaseService

SLOT_DEPENDENCY = "Depends on whether the slot was filled. Status unverified"


class RevenueIntelligenceEngine(BaseService):
    """
    MS-007 Compliant Revenue Intelligence Engine (RIE).
    Translates operational findings and context into estimated financial exposure.
    Does NOT process claims or perform billing. It is strictly an intelligence layer.
    """

    @property
    def service_name(self) -> str:
        return "RevenueIntelligenceEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def __init__(self):
        pass

    def _process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculates financial exposure based on rule finding and context.
        Payload expects: {"finding": Dict, "context": Dict}
        Returns: revenue_risk_category, estimated_financial_exposure, revenue_confidence, operational_dependency
        """
        rule_finding = payload.get("finding", {})
        if isinstance(rule_finding, list):
            rule_finding = rule_finding[0] if rule_finding else {}
        dce_context = payload.get("context", {})  # noqa

        rule_id = rule_finding.get("rule_id", "")

        from pydantic import BaseModel
        from typing import Literal

        class RevenueResult(BaseModel):
            revenue_risk_category: str | None = None
            estimated_financial_exposure: str | None = None
            revenue_confidence: Literal["HIGH", "MODERATE", "LOW", "UNAVAILABLE"] = "UNAVAILABLE"
            operational_dependency: str | None = None

        # CP-15: Operational intelligence != billing outcome.
        # Estimates require customer-specific financial evidence basis.
        # If no governed financial evidence basis is attached, represent exposure as None + UNAVAILABLE confidence.
        if rule_id == "RULE-SCH-001":  # No-show
            revenue = RevenueResult(
                revenue_risk_category="Operational Capacity Risk",
                estimated_financial_exposure=None,
                revenue_confidence="UNAVAILABLE",
                # Whether the freed slot was refilled is not known from the no-show record itself.
                operational_dependency=SLOT_DEPENDENCY
            )
        elif rule_id == "RULE-SCH-002":  # Wait time exceeded
            revenue = RevenueResult(
                revenue_risk_category="Operational Flow Risk",
                estimated_financial_exposure=None,
                revenue_confidence="UNAVAILABLE",
                operational_dependency="Queue congestion impacting daily clinic schedule."
            )
        elif rule_id == "RULE-OPS-001":  # Missed Call
            revenue = RevenueResult(
                revenue_risk_category="Registration Risk",
                estimated_financial_exposure=None,
                revenue_confidence="UNAVAILABLE",
                operational_dependency="Front-desk availability to return call within 15 minutes."
            )
        elif rule_id == "RULE-CLIN-001":  # Pending Lab Review
            revenue = RevenueResult(
                revenue_risk_category="Documentation Risk",
                estimated_financial_exposure=None,
                revenue_confidence="UNAVAILABLE",
                operational_dependency="Provider must sign the pending lab report in EHR."
            )
        elif rule_id.startswith("RULE-UAE-"):  # Payer claim denial
            # Exposure is the denied amount stated on the remittance; never estimated.
            facts = {f.get("key"): f.get("value") for f in payload.get("financial_facts") or []}
            exposure = None
            try:
                amount = float(facts.get("denied_amount"))
                exposure = f"{facts.get('currency') or 'AED'} {amount:,.2f}"
            except (TypeError, ValueError):
                exposure = None
            revenue = RevenueResult(
                revenue_risk_category="Claim Denial Revenue Risk",
                estimated_financial_exposure=exposure,
                revenue_confidence="HIGH" if exposure else "UNAVAILABLE",
                operational_dependency="Billing team must correct and resubmit or appeal the claim within the payer window."
            )
        else:
            revenue = RevenueResult(
                revenue_risk_category=None,
                estimated_financial_exposure=None,
                revenue_confidence="UNAVAILABLE",
                operational_dependency=None
            )

        return revenue.dict()


revenue_intelligence_engine = RevenueIntelligenceEngine()


def revenue_fields(rule_id, financial_facts=None) -> Dict[str, Any]:
    """Risk category, exposure, confidence and operational dependency for a governance rule (the
    values the engine stores on a signal). Facts are the source's typed facts, e.g. the denied amount."""
    return revenue_intelligence_engine._process({"finding": {"rule_id": rule_id or ""},
                                                 "financial_facts": list(financial_facts or [])})


def dependency_for_signal(kind, message=None):
    """Operational dependency of a signal whose stored value is missing, from its risk rule; the slot
    wording applies to missed appointments only. None when the signal's rule is unknown."""
    from app.services.risk_level_evaluator import CLAIM_DENIAL, NO_SHOW, WAIT_TIME
    rule = {CLAIM_DENIAL: "RULE-UAE-", NO_SHOW: "RULE-SCH-001", WAIT_TIME: "RULE-SCH-002"}.get(kind)
    if kind == NO_SHOW or (not kind and message and any(
            w in message.lower() for w in ("no-show", "no show", "appointment missed", "missed appointment"))):
        return SLOT_DEPENDENCY
    return revenue_fields(rule).get("operational_dependency") if rule else None
