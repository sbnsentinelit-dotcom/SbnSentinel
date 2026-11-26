"""Delivery contracts for the bridge API. The server binds scope and source; callers cannot."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from .contracts import Fact, Identifier, Instant, Model, Positive, Role, Scope

Token = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")]
HoldTask = Literal["RECONCILE_BANK_EVIDENCE", "RECONCILE_LEDGER", "RECONCILE_BALANCES", "RESOLVE_PAYMENT_ADVICE",
                   "CONFIRM_LINE_OWNERSHIP", "VERIFY_DENIAL_DEADLINES", "PROVE_ROUTES", "RESOLVE_SOURCE_EVIDENCE"]

# Which source role may deliver each record kind (the assessment re-checks this).
KIND_ROLE = {
    "control": "control", "closing": "control", "opening": "legacy", "new_line": "destination",
    "archive_doc": "archive", "assignment": "operations", "advice": "payer", "deposit": "bank",
    "posting": "ledger", "movement": "ledger", "approval": "operations", "adjustment_owner": "operations",
    "denial": "payer", "appeal": "operations",
}


def role_for(fact) -> str:
    if fact.kind == "probe":
        return "archive" if fact.stage == "archive_restore" else "operations"
    if fact.kind == "appeal" and fact.stage in {"received", "decided"}:
        return "payer"
    return KIND_ROLE[fact.kind]


class IncomingRecord(Model):
    """A normalized record. Scope and connector are filled by the server from the transition and the
    bound source; when a caller sends them they must match exactly."""
    scope: Scope | None = None
    connector: Identifier | None = None
    key: Identifier
    evidence: Identifier
    effective: Instant
    seen: Instant
    revision: Positive
    data: Fact


class IncomingSeal(Model):
    """The delivering source's completeness statement for its role."""
    through: Instant
    unique_rows: Annotated[int, Field(strict=True, ge=0)]
    digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    receipt: Identifier


class PageIn(Model):
    """One page of a source stream. `previous` must be the stream's current head (None for the first
    page). Without a token the server uses the page digest, so a re-sent file is recognised."""
    version: Literal["legacy-continuity.v1"] = "legacy-continuity.v1"
    binding_id: Annotated[int, Field(strict=True, ge=1)]
    resource: Identifier = "export"
    previous: Token | None = None
    token: Token | None = None
    records: tuple[IncomingRecord, ...] = ()
    seal: IncomingSeal | None = None


class BindingIn(Model):
    connector: Identifier
    role: Role
    valid_from: Instant
    valid_until: Instant


class TransitionIn(Model):
    facility: Identifier
    payee: Identifier
    payer: Identifier
    currency: Literal["AED", "USD"] | None = None
    cutover: Instant
    new_route: Identifier
    old_route: Identifier
    fallback_route: Identifier
    archive_route: Identifier
    max_probe_age_days: Positive = 14
    label: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    clinic_id: Identifier | None = None  # System Administrators only
    # Clinic ownership policy for hold investigations: {task code: responsible team}.
    hold_owners: dict[HoldTask, Identifier] | None = None
