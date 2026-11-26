"""Plain-language explanation for every assessment, follow-up and exit-review code."""

MESSAGES = {
    # Evidence intake and coverage
    "SCOPE_MISMATCH": "A record belongs to a different clinic, payer, payee or currency than this transition.",
    "SOURCE_NOT_AUTHORIZED": "A record came from a source that is not bound for this kind of evidence at that time.",
    "REVISION_REQUIRES_MAPPING": "A source sent a revised record; revisions need a validated mapping before they count.",
    "CONFLICTING_BUSINESS_EVENT": "The same business record arrived with different content; both copies are kept and the item is held.",
    "COVERAGE_UNPROVEN": "This source has no single completeness statement matching its delivered records at the assessment time.",
    "CLOSING_SNAPSHOT_BEHIND_DECISION": "The closing balance snapshot is older than the assessment time.",
    # Opening and closing position
    "INVALID_INVENTORY_ROW": "A balance row is duplicated, outside the legacy book, or not dated at its snapshot.",
    "INVALID_BALANCE_SIGN": "A balance has the wrong sign for its bucket (credits are negative, receivables positive).",
    "NO_CONTROL_POPULATION": "No signed control balances were received, so the claim population is unknown.",
    "OPENING_VARIANCE": "The old platform's opening balance differs from the signed control report.",
    "INVALID_NEW_LINE": "A replacement-platform claim line is outside the new book or dated before the cutover.",
    "UNKNOWN_ASSIGNED_LINE": "An owner was assigned to a claim line that is not in the transition.",
    "OWNER_MISSING_OR_AMBIGUOUS": "A legacy claim line has no accepted owner, or more than one.",
    "CLOSING_VARIANCE": "Opening balances plus recorded movements do not equal the signed closing balance.",
    # Cash proof
    "UNAPPLIED_BANK_CASH": "Money arrived in the bank with no matching payment advice; hold it as unapplied cash.",
    "AMBIGUOUS_PAYMENT_REFERENCE": "More than one payment advice uses the same payment reference.",
    "ADVICE_DOES_NOT_BALANCE": "The payment advice components do not add up to its net amount.",
    "INVALID_NONCASH_ADVICE": "A non-cash advice has a net amount or a bank deposit.",
    "ZERO_COMPONENT_UNEXPLAINED": "A zero-amount component has no denial that explains it.",
    "BANK_PROOF_INCOMPLETE": "The payment advice has no single matching bank deposit for its net amount.",
    "DUPLICATE_COMPONENT": "A payment advice repeats the same component.",
    "UNRESOLVED_LINE": "A payment component refers to a claim line outside the transition.",
    "LEDGER_PROOF_INCOMPLETE": "A payment component has no single matching posting in the clinic ledger.",
    "PROVIDER_ADJUSTMENT_OWNER_UNPROVEN": "A provider-level recovery has no single accepted finance owner.",
    "ORPHAN_LEDGER_POSTING": "A ledger posting refers to a payment component that was never advised.",
    "REVERSAL_LINEAGE_UNPROVEN": "A reversal does not link to an earlier payment on the same claim line.",
    # Ledger roll-forward
    "PRE_CUTOFF_MOVEMENT_REQUIRES_REBASE": "A ledger movement is dated before the cutover; the opening position must be rebased.",
    "UNDECLARED_POSITION": "A ledger movement affects a balance that was not in the opening position.",
    "APPROVAL_UNPROVEN": "An adjustment or refund has no matching earlier human approval.",
    "UNBALANCED_TRANSFER": "A payer-to-patient transfer does not move the same amount out and in.",
    "CASH_MOVEMENT_MISMATCH": "A legacy payment posting has no exactly matching receivable movement.",
    "ORPHAN_CASH_MOVEMENT": "A receivable movement refers to a posting that does not exist.",
    # Denials and appeals
    "APPEAL_LINEAGE_UNPROVEN": "An appeal step does not link to a known denial, or repeats a stage.",
    "UNKNOWN_DENIAL_LINE": "A denial refers to a claim line outside the transition.",
    "DENIAL_OWNER_UNPROVEN": "A denied claim line has no single accepted owner.",
    "DEADLINE_UNVERIFIED": "A denial has no verified rule and due date; it needs urgent human review.",
    "APPEAL_STAGE_GAP": "An appeal stage was recorded before the stage that must come first.",
    "APPEAL_RECEIPT_LATE": "The payer received the appeal after the due date.",
    "DEADLINE_WITHOUT_RECEIPT": "The appeal due date has passed with no payer receipt.",
    # Route proofs
    "INVALID_ROUTE_PROBE": "A route test does not match its source document, route or time window.",
    "UNPROVEN_ROUTE": "This route has not been proven by a recent successful test.",
    # Follow-up work
    "REVIEW_PROVIDER_ADJUSTMENT": "Review the provider-level recovery with its finance owner.",
    "REVIEW_REOPENED_LINE": "A reversal reopened this claim line; review it with its owner.",
    "WORK_DENIAL": "Work the denial through the surviving payer route before its deadline.",
    "OBTAIN_RECEIPT": "The appeal was sent; obtain the payer's receipt.",
    "AWAIT_DECISION": "The payer received the appeal; await its decision.",
    "VERIFY_FINANCIAL_OUTCOME": "The payer decided; verify the financial outcome in the ledger.",
    # Subscription exit review
    "UNVERIFIED_SUBSCRIPTION_INVENTORY": "The subscription inventory is empty, repeats a scope, or was certified at another time.",
    "INVALID_SCOPE_PACKET": "An assessed scope is not in the inventory, is repeated, or has another assessment time.",
    "SCOPE_ON_HOLD": "This payer scope is on hold, so its claims still depend on the old platform.",
    "INCOMPLETE_SCOPE_COVERAGE": "Not every payer scope in the inventory has an assessment.",
    "DEPENDENCY_INVENTORY_INCOMPLETE": "The inventory does not cover access, bundled clinical, contract, records and fallback dependencies.",
    "DEPENDENCY_STILL_OPEN": "A dependency has no proof, no accepting owner, or its proof has expired.",
    "MISSING_SIGNOFF": "Finance, revenue cycle, records and contract owner sign-offs are not all present.",
    "NET_SAVINGS_UNPROVEN": "Avoided fees do not exceed the added costs, or the cost basis is missing.",
}


def message(code: str) -> str:
    return MESSAGES.get(code, code.replace("_", " ").capitalize() + ".")
