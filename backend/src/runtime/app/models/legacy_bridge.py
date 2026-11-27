"""
Legacy Claims Continuity Bridge storage (V3 candidate, read-only toward payers and ledgers).

A transition is one scoped move of a clinic's claims from an outgoing billing platform: deployment,
region, organization, clinic, facility, payee, payer and currency. Sources are bound to it by an
administrator (connector + role); only bound sources can deliver pages. Every received page, record
and completeness seal is kept as delivered (never edited or deleted), so an assessment can be
reproduced from the stored evidence. Sentinel never posts money, submits claims or ends a
subscription from these tables: assessments and work items are review material for clinic staff.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.db.database import Base


class LegacyTransitionModel(Base):
    __tablename__ = "legacy_transitions"
    __table_args__ = (
        UniqueConstraint("deployment", "org_id", "clinic_id", "facility", "payee", "payer", "currency",
                         name="uq_legacy_transition_scope"),
    )

    id = Column(String(40), primary_key=True)
    deployment = Column(String(96), nullable=False)
    region = Column(String(8), nullable=False)
    org_id = Column(String(96), nullable=False)
    clinic_id = Column(String(96), nullable=False, index=True)
    facility = Column(String(96), nullable=False)
    payee = Column(String(96), nullable=False)
    payer = Column(String(96), nullable=False)
    currency = Column(String(3), nullable=False)
    cutover = Column(DateTime, nullable=False)              # UTC
    new_route = Column(String(96), nullable=False)
    old_route = Column(String(96), nullable=False)
    fallback_route = Column(String(96), nullable=False)
    archive_route = Column(String(96), nullable=False)
    max_probe_age_days = Column(Integer, nullable=False)
    label = Column(String(160), nullable=True)
    origin = Column(String(16), nullable=False, default="clinic")  # "clinic" | "synthetic"
    # Clinic ownership policy for hold investigations: {task code: responsible team}; defaults apply
    # to codes it does not name (app.legacy_bridge.work_policy).
    ownership_json = Column(Text, nullable=True)
    # The published assessment that drives work items, ordered by evidence cutoff then revision.
    current_assessment_id = Column(String(40), nullable=True)
    # Bumped by every publication and work item acceptance as their first statement: the row lock
    # serializes concurrent writers of one transition's assessments and work items.
    lock_version = Column(Integer, nullable=False, default=0)
    created_by = Column(String(160), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LegacySourceBindingModel(Base):
    """A source allowed to deliver one role of evidence for a transition, for a validity window."""
    __tablename__ = "legacy_source_bindings"
    __table_args__ = (UniqueConstraint("transition_id", "connector", name="uq_legacy_binding_connector"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    connector = Column(String(96), nullable=False)
    role = Column(String(16), nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=False)
    created_by = Column(String(160), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LegacyStreamHeadModel(Base):
    """Last accepted page token per source stream; a page must name it as its predecessor."""
    __tablename__ = "legacy_stream_heads"
    __table_args__ = (UniqueConstraint("transition_id", "connector", "resource", name="uq_legacy_stream"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    connector = Column(String(96), nullable=False)
    resource = Column(String(96), nullable=False)
    head_token = Column(String(96), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LegacyPageModel(Base):
    __tablename__ = "legacy_pages"
    __table_args__ = (UniqueConstraint("transition_id", "connector", "resource", "token", name="uq_legacy_page"),)

    id = Column(String(40), primary_key=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    connector = Column(String(96), nullable=False)
    resource = Column(String(96), nullable=False)
    token = Column(String(96), nullable=False)
    previous_token = Column(String(96), nullable=True)
    digest = Column(String(64), nullable=False)
    record_count = Column(Integer, nullable=False)
    seal_count = Column(Integer, nullable=False)
    evidence_id = Column(String(64), nullable=False)        # evidence_repository custody row
    received_at = Column(DateTime, nullable=False)
    received_by = Column(String(160), nullable=False)


class LegacyRecordModel(Base):
    """One normalized business record exactly as delivered (record_json is the authority)."""
    __tablename__ = "legacy_records"
    __table_args__ = (
        UniqueConstraint("transition_id", "connector", "kind", "business_key", "revision", "content_digest",
                         name="uq_legacy_record_delivery"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    page_id = Column(String(40), ForeignKey("legacy_pages.id"), nullable=False)
    connector = Column(String(96), nullable=False)
    kind = Column(String(24), nullable=False)
    business_key = Column(String(96), nullable=False)
    revision = Column(Integer, nullable=False)
    content_digest = Column(String(64), nullable=False)
    evidence_ref = Column(String(96), nullable=False)
    effective_at = Column(DateTime, nullable=False)
    seen_at = Column(DateTime, nullable=False)
    received_at = Column(DateTime, nullable=False)
    record_json = Column(Text, nullable=False)


class LegacySealModel(Base):
    """A source's own completeness statement: through an instant it holds N unique rows with a digest."""
    __tablename__ = "legacy_seals"
    __table_args__ = (
        UniqueConstraint("transition_id", "connector", "through", "receipt", name="uq_legacy_seal"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    page_id = Column(String(40), ForeignKey("legacy_pages.id"), nullable=False)
    connector = Column(String(96), nullable=False)
    role = Column(String(16), nullable=False)
    through = Column(DateTime, nullable=False)
    unique_rows = Column(Integer, nullable=False)
    digest = Column(String(64), nullable=False)
    receipt = Column(String(96), nullable=False)
    received_at = Column(DateTime, nullable=False)
    # A later statement for the same source and instant with different contents replaces this one; the
    # replaced statement is kept for the record and no longer counts.
    superseded_by_id = Column(Integer, nullable=True)


class LegacyAssessmentModel(Base):
    """A published, immutable assessment; the same evidence and settings always map to the same row.
    Revision numbers successive publications for the same evidence cutoff (as_of)."""
    __tablename__ = "legacy_assessments"
    __table_args__ = (UniqueConstraint("transition_id", "input_digest", name="uq_legacy_assessment_input"),
                      Index("uq_legacy_assessment_revision", "transition_id", "as_of", "revision", unique=True))

    id = Column(String(40), primary_key=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    as_of = Column(DateTime, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    # True when every source's completeness seal is through as_of; only sealed assessments become current.
    sealed = Column(Boolean, nullable=False, default=False)
    # The exact inputs: {"records": [ids], "seals": [ids], "bindings": [ids]}; every historical figure is
    # rebuilt from these, so later deliveries never change a published assessment.
    manifest_json = Column(Text, nullable=True)
    snapshot_at = Column(DateTime, nullable=True)
    # The assessment this one replaced as current (a newer cutoff, new evidence, or new rules).
    supersedes_id = Column(String(40), nullable=True)
    status = Column(String(24), nullable=False)
    input_digest = Column(String(64), nullable=False)
    policy_version = Column(String(48), nullable=False)
    mapping_version = Column(String(48), nullable=False)
    record_count = Column(Integer, nullable=False)
    issues_json = Column(Text, nullable=False)
    follow_up_json = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=False)
    summary_json = Column(Text, nullable=False)
    review_fact_json = Column(Text, nullable=False)       # G2 observation + RESEARCH_ONLY scalar fact
    computed_at = Column(DateTime, nullable=False)
    computed_by = Column(String(160), nullable=False)


class LegacyWorkItemModel(Base):
    """Work from the current published assessment and who accepted it: continuing work (an open denial,
    a provider recovery...) and one investigation per hold area with its linked hold reasons."""
    __tablename__ = "legacy_work_items"
    __table_args__ = (UniqueConstraint("transition_id", "code", "item", name="uq_legacy_work_item"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="follow_up")  # "follow_up" | "hold"
    code = Column(String(48), nullable=False)
    item = Column(String(160), nullable=False)
    issues_json = Column(Text, nullable=True)                 # hold: linked [{"code", "item"}]
    owner = Column(String(96), nullable=True)                 # responsible team from evidence or policy
    assigned_to = Column(String(160), nullable=True)          # a person or team a reviewer assigned
    # How the work ended, kept separate (INTEGRATION.md slice 4): collected | written_off (both proven by
    # ledger evidence after the payer's decision) | risk_accepted (a named administrator's decision) |
    # resolved_by_evidence (later evidence cleared it). A CLOSED item is not reopened by publication.
    outcome = Column(String(24), nullable=True)
    outcome_by = Column(String(160), nullable=True)
    outcome_at = Column(DateTime, nullable=True)
    outcome_note = Column(Text, nullable=True)
    outcome_assessment_id = Column(String(40), nullable=True)   # the assessment the proof came from
    outcome_proof_json = Column(Text, nullable=True)            # proof records and applied amounts
    outcome_amount = Column(BigInteger, nullable=True)           # applied amount, minor units
    outcome_scope_json = Column(Text, nullable=True)            # risk_accepted: the accepted discrepancies
    state = Column(String(16), nullable=False, default="OPEN")  # OPEN | ACCEPTED | RESOLVED
    first_assessment_id = Column(String(40), nullable=False)
    last_assessment_id = Column(String(40), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    accepted_by = Column(String(160), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)


class LegacyWorkItemEventModel(Base):
    """Every state change of a work item (opened, accepted, resolved, reopened), never edited."""
    __tablename__ = "legacy_work_item_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    work_item_id = Column(Integer, ForeignKey("legacy_work_items.id"), nullable=False, index=True)
    transition_id = Column(String(40), ForeignKey("legacy_transitions.id"), nullable=False, index=True)
    # OPENED | ACCEPTED | RESOLVED | REOPENED | ASSIGNED | NOTE | RELEASED | OUTCOME
    event = Column(String(16), nullable=False)
    from_state = Column(String(16), nullable=True)
    to_state = Column(String(16), nullable=False)
    actor = Column(String(160), nullable=False)
    assessment_id = Column(String(40), nullable=True)
    note = Column(Text, nullable=True)
    at = Column(DateTime, nullable=False)


class LegacyExitSignoffModel(Base):
    """A sign-off given on the screen for one role of a subscription inventory, by a named user. It
    joins the inventory's own sign-offs in the exit review; the inventory itself is never edited."""
    __tablename__ = "legacy_exit_signoffs"
    __table_args__ = (UniqueConstraint("inventory_id", "role", name="uq_legacy_exit_signoff"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    inventory_id = Column(String(40), ForeignKey("legacy_exit_inventories.id"), nullable=False, index=True)
    role = Column(String(24), nullable=False)        # finance | revenue_cycle | records | contract_owner
    decision_ref = Column(String(96), nullable=False)
    signed_by = Column(String(160), nullable=False)
    signed_at = Column(DateTime, nullable=False)
    note = Column(Text, nullable=True)


class LegacyExitInventoryModel(Base):
    """A signed subscription dependency inventory delivered by the clinic's contract owner."""
    __tablename__ = "legacy_exit_inventories"
    __table_args__ = (UniqueConstraint("clinic_id", "subscription", "digest", name="uq_legacy_exit_inventory"),)

    id = Column(String(40), primary_key=True)
    org_id = Column(String(96), nullable=False)
    clinic_id = Column(String(96), nullable=False, index=True)
    subscription = Column(String(96), nullable=False)
    as_of = Column(DateTime, nullable=False)
    digest = Column(String(64), nullable=False)
    inventory_json = Column(Text, nullable=False)
    received_at = Column(DateTime, nullable=False)
    received_by = Column(String(160), nullable=False)
