"""Human review preparation for one contracted billing subscription.

The signed dependency inventory and exit proof references must be provided by
the clinic-side custodian. A positive result does not terminate a license.
"""
from typing import Literal
from .contracts import Model, Identifier, Instant, Scope, Batch, Context, Minor
from .assessment import assess, Issue


class Dependency(Model):
    key: Identifier
    kind: Literal["access", "bundled_clinical", "contract", "records", "fallback"]
    proof_ref: Identifier | None
    accepted_by: Identifier | None
    valid_through: Instant


class ExitInventory(Model):
    subscription: Identifier
    inventory_receipt: Identifier
    certified_at: Instant
    as_of: Instant
    required_scopes: tuple[Scope, ...]
    dependencies: tuple[Dependency, ...]
    signoffs: dict[Literal["finance", "revenue_cycle", "records", "contract_owner"], Identifier]
    currency: Literal["AED", "USD"]
    avoided_minor: Minor
    incremental_minor: Minor
    cost_basis_ref: Identifier | None


def prepare_exit(inventory: ExitInventory, packets: list[tuple[Context, Batch]]):
    issues = []
    required = set(inventory.required_scopes)
    if not required or len(required) != len(inventory.required_scopes) or inventory.certified_at != inventory.as_of:
        issues.append(Issue("UNVERIFIED_SUBSCRIPTION_INVENTORY", inventory.subscription))
    observed = set()
    for ctx, batch in packets:
        if ctx.scope not in required or ctx.scope in observed or ctx.as_of != inventory.as_of:
            issues.append(Issue("INVALID_SCOPE_PACKET", inventory.subscription))
        observed.add(ctx.scope)
        if assess(ctx, batch).status != "SAMPLE_REVIEWABLE":
            issues.append(Issue("SCOPE_ON_HOLD", ctx.scope.clinic + "/" + ctx.scope.payer))
    if observed != required:
        issues.append(Issue("INCOMPLETE_SCOPE_COVERAGE", inventory.subscription))
    # One documented applicability or release decision for every dependency class.
    required_kinds = {"access", "bundled_clinical", "contract", "records", "fallback"}
    if {d.kind for d in inventory.dependencies} != required_kinds or len({d.key for d in inventory.dependencies}) != len(inventory.dependencies):
        issues.append(Issue("DEPENDENCY_INVENTORY_INCOMPLETE", inventory.subscription))
    for d in inventory.dependencies:
        if not d.proof_ref or not d.accepted_by or d.valid_through < inventory.as_of:
            issues.append(Issue("DEPENDENCY_STILL_OPEN", d.key))
    if set(inventory.signoffs) != {"finance", "revenue_cycle", "records", "contract_owner"}:
        issues.append(Issue("MISSING_SIGNOFF", inventory.subscription))
    if (not inventory.cost_basis_ref or inventory.avoided_minor <= inventory.incremental_minor
            or min(inventory.avoided_minor, inventory.incremental_minor) < 0):
        issues.append(Issue("NET_SAVINGS_UNPROVEN", inventory.subscription))
    return ("HOLD" if issues else "HUMAN_EXIT_REVIEW", tuple(sorted(set(issues))))
