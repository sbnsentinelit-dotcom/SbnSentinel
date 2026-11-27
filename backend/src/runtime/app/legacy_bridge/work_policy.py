"""Who investigates each blocking discrepancy.

Every hold reason of a published assessment belongs to exactly one investigation task. Related hold
reasons that are one investigation (a missing bank feed shows up both as BANK_PROOF_INCOMPLETE on the
advice and COVERAGE_UNPROVEN on the bank source) share one task. A task's stable key is
(transition, task code, area); each linked hold reason keeps its own stable key (issue code, item).
The responsible team comes from the transition's ownership policy, falling back to DEFAULT_OWNERS.
"""
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

# task code -> (area, default responsible team)
TASKS = {
    "RECONCILE_BANK_EVIDENCE": ("bank", "finance-reconciliation"),
    "RECONCILE_LEDGER": ("ledger", "finance-reconciliation"),
    "RECONCILE_BALANCES": ("balances", "finance-reconciliation"),
    "RESOLVE_PAYMENT_ADVICE": ("payer", "revenue-cycle"),
    "CONFIRM_LINE_OWNERSHIP": ("ownership", "revenue-cycle"),
    "VERIFY_DENIAL_DEADLINES": ("denials", "revenue-cycle"),
    "PROVE_ROUTES": ("routes", "operations"),
    "RESOLVE_SOURCE_EVIDENCE": ("sources", "bridge-administrator"),
}
DEFAULT_OWNERS = {code: owner for code, (_area, owner) in TASKS.items()}

BY_ISSUE = {
    # bank settlement
    "BANK_PROOF_INCOMPLETE": "RECONCILE_BANK_EVIDENCE",
    "AMBIGUOUS_PAYMENT_REFERENCE": "RECONCILE_BANK_EVIDENCE",
    "UNAPPLIED_BANK_CASH": "RECONCILE_BANK_EVIDENCE",
    # ledger
    "LEDGER_PROOF_INCOMPLETE": "RECONCILE_LEDGER",
    "ORPHAN_LEDGER_POSTING": "RECONCILE_LEDGER",
    "CASH_MOVEMENT_MISMATCH": "RECONCILE_LEDGER",
    "ORPHAN_CASH_MOVEMENT": "RECONCILE_LEDGER",
    # balances
    "NO_CONTROL_POPULATION": "RECONCILE_BALANCES",
    "OPENING_VARIANCE": "RECONCILE_BALANCES",
    "CLOSING_VARIANCE": "RECONCILE_BALANCES",
    "CLOSING_SNAPSHOT_OVERLAP": "RECONCILE_BALANCES",
    "CLOSING_SNAPSHOT_BEHIND_DECISION": "RECONCILE_BALANCES",
    "INVALID_INVENTORY_ROW": "RECONCILE_BALANCES",
    "INVALID_BALANCE_SIGN": "RECONCILE_BALANCES",
    "PRE_CUTOFF_MOVEMENT_REQUIRES_REBASE": "RECONCILE_BALANCES",
    "UNDECLARED_POSITION": "RECONCILE_BALANCES",
    "APPROVAL_UNPROVEN": "RECONCILE_BALANCES",
    "UNBALANCED_TRANSFER": "RECONCILE_BALANCES",
    # payer advices
    "ADVICE_DOES_NOT_BALANCE": "RESOLVE_PAYMENT_ADVICE",
    "INVALID_NONCASH_ADVICE": "RESOLVE_PAYMENT_ADVICE",
    "ZERO_COMPONENT_UNEXPLAINED": "RESOLVE_PAYMENT_ADVICE",
    "DUPLICATE_COMPONENT": "RESOLVE_PAYMENT_ADVICE",
    "UNRESOLVED_LINE": "RESOLVE_PAYMENT_ADVICE",
    "REVERSAL_LINEAGE_UNPROVEN": "RESOLVE_PAYMENT_ADVICE",
    # ownership of lines and recoveries
    "OWNER_MISSING_OR_AMBIGUOUS": "CONFIRM_LINE_OWNERSHIP",
    "UNKNOWN_ASSIGNED_LINE": "CONFIRM_LINE_OWNERSHIP",
    "INVALID_NEW_LINE": "CONFIRM_LINE_OWNERSHIP",
    "DENIAL_OWNER_UNPROVEN": "CONFIRM_LINE_OWNERSHIP",
    "PROVIDER_ADJUSTMENT_OWNER_UNPROVEN": "CONFIRM_LINE_OWNERSHIP",
    # denials
    "UNKNOWN_DENIAL_LINE": "VERIFY_DENIAL_DEADLINES",
    "DEADLINE_UNVERIFIED": "VERIFY_DENIAL_DEADLINES",
    "APPEAL_LINEAGE_UNPROVEN": "VERIFY_DENIAL_DEADLINES",
    "APPEAL_STAGE_GAP": "VERIFY_DENIAL_DEADLINES",
    "APPEAL_RECEIPT_LATE": "VERIFY_DENIAL_DEADLINES",
    "DEADLINE_WITHOUT_RECEIPT": "VERIFY_DENIAL_DEADLINES",
    # routes
    "UNPROVEN_ROUTE": "PROVE_ROUTES",
    "INVALID_ROUTE_PROBE": "PROVE_ROUTES",
}
# A source whose completeness is unproven belongs to the investigation of what that source proves.
BY_COVERAGE_ROLE = {
    "bank": "RECONCILE_BANK_EVIDENCE",
    "ledger": "RECONCILE_LEDGER",
    "control": "RECONCILE_BALANCES",
    "legacy": "RECONCILE_BALANCES",
}
FALLBACK = "RESOLVE_SOURCE_EVIDENCE"
# A failed route or an unknown deadline is priority work (INTEGRATION.md, slice 4).
PRIORITY = {"VERIFY_DENIAL_DEADLINES", "PROVE_ROUTES"}


def task_for(code: str, item: str) -> str:
    if code == "COVERAGE_UNPROVEN":
        return BY_COVERAGE_ROLE.get(item, FALLBACK)
    return BY_ISSUE.get(code, FALLBACK)


def hold_tasks(issues: Iterable[dict], owners: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str, list]]:
    """[(task code, area, responsible team, [linked {"code", "item"}])] for the hold reasons."""
    owners = {**DEFAULT_OWNERS, **(owners or {})}
    linked = defaultdict(list)
    for i in issues:
        linked[task_for(i["code"], i["item"])].append({"code": i["code"], "item": i["item"]})
    return [(code, TASKS[code][0], owners[code], sorted(links, key=lambda x: (x["code"], x["item"])))
            for code, links in sorted(linked.items())]
