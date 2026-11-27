"""Version 1 of normalized bridge input. Server bindings are a separate input."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")]
Minor = Annotated[int, Field(strict=True, ge=-(10**15), le=10**15)]
Positive = Annotated[int, Field(strict=True, ge=1)]
Role = Literal["control", "legacy", "destination", "payer", "bank", "ledger", "operations", "archive"]
Bucket = Literal["payer_ar", "patient_ar", "credit", "unapplied", "unbilled", "closed_watch"]
LegKind = Literal["claim_payment", "reversal", "provider_adjustment"]
Stage = Literal["old_retrieval", "old_claim_new_route", "new_claim_new_route", "archive_restore", "fallback_recovery", "denial_route"]


def aware(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("an explicit UTC offset is required")
    return value


Instant = Annotated[datetime, Field(strict=True), AfterValidator(aware)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Scope(Model):
    deployment: Identifier
    region: Literal["UAE", "US"]
    org: Identifier
    clinic: Identifier
    facility: Identifier
    payee: Identifier
    payer: Identifier
    currency: Literal["AED", "USD"]


class Line(Model):
    book: Literal["legacy", "new"]
    claim: Identifier
    line: Identifier


class Balance(Model):
    kind: Literal["control", "opening", "closing"]
    line: Line
    bucket: Bucket
    minor: Minor


class NewLine(Model):
    kind: Literal["new_line"]
    line: Line


class ArchiveDoc(Model):
    kind: Literal["archive_doc"]
    line: Line
    artifact: Literal["original_submission", "historical_advice"]
    content_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class Assignment(Model):
    kind: Literal["assignment"]
    line: Line
    owner: Identifier
    accepted_ref: Identifier
    next_action: Identifier


class AdjustmentOwner(Model):
    kind: Literal["adjustment_owner"]
    adjustment_ref: Identifier
    owner: Identifier
    accepted_ref: Identifier


class Part(Model):
    part: Identifier
    type: LegKind
    minor: Minor
    line: Line | None = None
    adjustment_ref: Identifier | None = None
    original_advice: Identifier | None = None
    original_part: Identifier | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type == "provider_adjustment":
            if self.line or not self.adjustment_ref or self.original_advice or self.original_part:
                raise ValueError("provider adjustment needs a separate adjustment reference")
        elif not self.line or self.adjustment_ref:
            raise ValueError("claim component needs a unique claim line")
        if self.type == "reversal":
            if self.minor >= 0 or not self.original_advice or not self.original_part:
                raise ValueError("reversal needs a negative amount and original payment")
        if self.type == "claim_payment" and (self.minor < 0 or self.original_advice or self.original_part):
            raise ValueError("invalid claim payment")
        return self


class Advice(Model):
    kind: Literal["advice"]
    trace: Identifier
    net: Minor
    settlement: Literal["cash", "noncash"]
    parts: Annotated[tuple[Part, ...], Field(min_length=1)]


class Deposit(Model):
    kind: Literal["deposit"]
    trace: Identifier
    minor: Minor


class Posting(Model):
    kind: Literal["posting"]
    advice: Identifier
    component: Part


class Movement(Model):
    """Observed ledger delta. Positive increases the signed balance of the bucket."""
    kind: Literal["movement"]
    line: Line
    bucket: Bucket
    minor: Minor
    reason: Literal["payer_cash", "reversal", "transfer", "approved_adjustment", "refund"]
    link: Identifier  # posting ID, paired transfer ID, or human approval receipt


class Approval(Model):
    kind: Literal["approval"]
    line: Line
    bucket: Bucket
    minor: Minor
    reason: Literal["approved_adjustment", "refund"]


class Denial(Model):
    kind: Literal["denial"]
    advice: Identifier | None = None
    line: Line
    notice: Identifier
    route: Identifier
    rule: Identifier | None
    due: Instant | None


class Appeal(Model):
    kind: Literal["appeal"]
    denial: Identifier
    stage: Literal["sent", "received", "decided"]
    receipt: Identifier


class Probe(Model):
    kind: Literal["probe"]
    stage: Stage
    line: Line
    originated_at: Instant
    route: Identifier
    source_doc: Identifier
    receipt: Identifier


Fact = Annotated[Balance | NewLine | ArchiveDoc | Assignment | AdjustmentOwner | Advice | Deposit | Posting | Movement | Approval | Denial | Appeal | Probe, Field(discriminator="kind")]


class Record(Model):
    scope: Scope
    connector: Identifier
    key: Identifier  # stable business document ID in the payer/payee namespace
    evidence: Identifier  # opaque archive receipt ID, resolved by the production custodian
    effective: Instant
    seen: Instant
    revision: Positive
    data: Fact

    @model_validator(mode="after")
    def ordering(self):
        if self.seen < self.effective:
            raise ValueError("observed record predates the event")
        return self


class Binding(Model):
    connector: Identifier
    role: Role
    scope: Scope
    valid_from: Instant
    valid_until: Instant


class Seal(Model):
    role: Role
    through: Instant
    unique_rows: Annotated[int, Field(strict=True, ge=0)]
    digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    receipt: Identifier


class Context(Model):
    scope: Scope
    cutover: Instant
    as_of: Instant
    snapshot_at: Instant  # accounting control cutoff
    new_route: Identifier
    archive_route: Identifier
    old_route: Identifier
    fallback_route: Identifier
    max_probe_age_days: Positive  # locally configured pilot assumption
    bindings: tuple[Binding, ...]
    seals: tuple[Seal, ...]

    @model_validator(mode="after")
    def validate_context(self):
        if not self.cutover <= self.snapshot_at <= self.as_of:
            raise ValueError("invalid cutoff or snapshot time")
        if len({b.connector for b in self.bindings}) != len(self.bindings):
            raise ValueError("conflicting connector bindings")
        if any(b.scope != self.scope or b.valid_from >= b.valid_until for b in self.bindings):
            raise ValueError("invalid scoped binding")
        return self


class Batch(Model):
    version: Literal["legacy-continuity.v1"]
    records: tuple[Record, ...]
