"""
UAE claim-denial governance used by the Dubai demo dataset.

Registers (idempotently) the UAE adjudication policy, one rule per denial code and one
recommendation mapping per rule, so that claim-denial events go through the normal pipeline:
evidence -> context -> policy -> rule -> recommendation -> human decision -> action.

The rule IDs, mapping IDs and versions match the five seeded Dubai signals
(RULE-UAE-<code> / MAP-UAE-<code>, version 2026.1), so those signals can also move on to actions.
"""
from datetime import datetime
from typing import Dict, Optional

UAE_POLICY_ID = "POL-UAE-CLAIMS-2026"
UAE_POLICY_VERSION = "2026.04"
UAE_RULE_VERSION = "2026.1"
UAE_SCOPE = "UAE"
UAE_CLAIMS_SOURCE = "UAE claims feed"  # generic; the clearinghouse name is developer-only
CLAIM_DENIAL_CONTEXT = "Financial Integrity"
UAE_POLICY_EFFECTIVE_FROM = datetime(2026, 1, 1)

# code -> (title, baseline level, recommendation, business impact, expected outcome)
# The baseline level is only the mapping default. The level a signal shows is evaluated from the
# payer remittance data (amount, deadline, correction time) by services/risk_level_evaluator.py.
UAE_DENIAL_RULES: Dict[str, tuple] = {
    "CO4": ("Procedure Modifier Conflict (Unbundling)", "Critical",
            "Append Modifier 25 to separate distinct professional services prior to submission.",
            "Automated payer claim denial under NCCI / UAE Insurance bundling rules.",
            "Claim resubmitted with the correct modifier and paid."),
    "CO15": ("Pre-bill Prior Authorization Omission", "High",
             "Attach pre-authorization approval code or request retrospective urgent review.",
             "High risk of immediate claim bounce under the DHA technical rejection rule TR-04.",
             "Claim resubmitted with a valid prior authorization reference."),
    "CO16": ("Missing Supporting Clinical Documentation", "Moderate",
             "Upload signed operative clinical notes directly via DHA unified portal integration.",
             "Payment delayed past 45-day payment cycle.",
             "Documentation supplied and claim released for payment."),
    "CO29": ("UAE Timely Filing Horizon Critical", "Critical",
             "Dispatch automated electronic dispute pack with proof of original timely submission.",
             "Unrecoverable bad debt write-off if the appeal is not lodged.",
             "Appeal lodged before the statutory dispute cutoff."),
    "CO50": ("Medical Necessity Discrepancy", "Moderate",
             "Update primary ICD-10 diagnostic coding to reflect clinical findings before billing.",
             "Risk of adjudication hold and demand for full medical records review.",
             "Diagnosis coding corrected and medical necessity established."),
}


# Evidence a payer asks for to accept a resubmission, per denial code. In live use this comes with the
# payer remittance (DHPO eClaimLink); the demo uses these synthetic requirements.
SYNTHETIC_REQUIRED_EVIDENCE: Dict[str, list] = {
    "CO4": ["Corrected claim with modifier 25", "Procedure note"],
    "CO15": ["Prior authorization approval code"],
    "CO16": ["Signed operative note", "Pathology report"],
    "CO29": ["Proof of original timely submission (claim submission receipt)"],
    "CO50": ["Updated ICD-10 diagnosis", "Clinical rationale note"],
}


def denial_context(code: str) -> str:
    """Secondary decision context for a claim denied with the given code."""
    return f"UAE Claim Denial {code}"


def rule_id_for(code: str) -> str:
    return f"RULE-UAE-{code}"


def code_for_rule(rule_id: str) -> Optional[str]:
    prefix = "RULE-UAE-"
    return rule_id[len(prefix):] if rule_id and rule_id.startswith(prefix) else None


def _repair_existing_policy_row() -> None:
    """Older seeds stored this policy with a timezone-aware date and an unknown scope."""
    from app.db.database import SessionLocal
    from app.models.governance_storage import GovernedPolicyVersionModel
    db = SessionLocal()
    try:
        row = db.query(GovernedPolicyVersionModel).filter_by(
            policy_id=UAE_POLICY_ID, version=UAE_POLICY_VERSION).first()
        if row and (row.scope != UAE_SCOPE or str(row.effective_from or "").endswith("Z")):
            row.scope = UAE_SCOPE
            row.effective_from = UAE_POLICY_EFFECTIVE_FROM.isoformat()
            row.approval_timestamp = UAE_POLICY_EFFECTIVE_FROM.isoformat()
            db.commit()
    finally:
        db.close()


def register_uae_claims_governance() -> None:
    from app.services.governance_registry import (
        governance_registry, PolicyVersion, RuleVersion, RuleInputDefinition,
        RecommendationMapping, LifecycleState, AuthorityRequirement,
    )

    _repair_existing_policy_row()
    # Load every stored policy first: the registry only reads the database while its cache is empty,
    # so adding this policy to an empty cache would hide all other policies.
    governance_registry._ensure_policies_loaded()
    policy = governance_registry.get_policy_by_version(UAE_POLICY_ID, UAE_POLICY_VERSION)
    if not policy:
        policy = PolicyVersion(
            policy_id=UAE_POLICY_ID,
            version=UAE_POLICY_VERSION,
            content="Dubai Health Authority Unified Insurance Adjudication Policy & Automated Claims Scrubber Rules.",
            lifecycle_state=LifecycleState.ACTIVE,
            effective_from=UAE_POLICY_EFFECTIVE_FROM,
            approval_state="APPROVED",
            approved_by="DHA Health Funding Department",
            approval_timestamp=UAE_POLICY_EFFECTIVE_FROM,
            scope=UAE_SCOPE,
        )
    # Keep the in-memory copy in step with the stored row.
    governance_registry._policies = [
        p for p in governance_registry._policies
        if not (p.policy_id == UAE_POLICY_ID and p.version == UAE_POLICY_VERSION)
    ]
    governance_registry.register_policy(policy)

    for code, (title, risk, action, impact, outcome) in UAE_DENIAL_RULES.items():
        rule_id = rule_id_for(code)
        if not governance_registry.get_rule_by_version(rule_id, UAE_RULE_VERSION):
            governance_registry.register_rule(RuleVersion(
                rule_id=rule_id,
                version=UAE_RULE_VERSION,
                logic_description=f"Flag claims denied by the payer with adjustment code {code} ({title}).",
                lifecycle_state=LifecycleState.ACTIVE,
                governing_policy_id=UAE_POLICY_ID,
                governing_policy_version=UAE_POLICY_VERSION,
                inputs=[
                    RuleInputDefinition("primary_context", "str", True, "DecisionContext"),
                    RuleInputDefinition("secondary_context", "str", True, "DecisionContext"),
                ],
                allowed_outputs=["CONDITION_MET", "CONDITION_NOT_MET", "NOT_EVALUABLE"],
                effective_from=UAE_POLICY_EFFECTIVE_FROM,
                approval_state="APPROVED",
                approved_by="DHA Health Funding Department",
            ))
        mapping_id = f"MAP-UAE-{code}"
        existing_mapping = governance_registry.get_recommendation_mapping_by_version(mapping_id, UAE_RULE_VERSION)
        # Resubmitting the corrected claim with its evidence is a permitted action; older rows only
        # allowed a follow-up task and are updated here.
        if not existing_mapping or "RESUBMIT_CLAIM" not in (existing_mapping.allowed_action_types or []):
            governance_registry.register_recommendation_mapping(RecommendationMapping(
                mapping_id=mapping_id,
                version=UAE_RULE_VERSION,
                applicable_rule_id=rule_id,
                eligible_result="CONDITION_MET",
                recommendation_template=action,
                authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED,
                priority=risk,
                business_impact_template=impact,
                expected_outcome_template=outcome,
                problem_template=f"[{code}] {title}",
                lifecycle_state=LifecycleState.ACTIVE,
                effective_from=UAE_POLICY_EFFECTIVE_FROM,
                allowed_action_types=["RESUBMIT_CLAIM", "CREATE_FOLLOWUP_TASK"],
            ))
