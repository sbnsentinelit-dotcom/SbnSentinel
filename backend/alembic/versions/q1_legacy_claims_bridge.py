"""q1_legacy_claims_bridge

Legacy Claims Continuity Bridge (V3 candidate): transitions, bound sources, stream heads, received
pages, normalized records, completeness seals, versioned assessments, work items with their event history and subscription
exit inventories. Additive only; no existing table changes.

Revision ID: q1_legacy_claims_bridge
Revises: p7_waiting_room_evidence
Create Date: 2026-11-25 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'q1_legacy_claims_bridge'
down_revision = 'p7_waiting_room_evidence'
branch_labels = None
depends_on = None

S = sa.String
DT = sa.DateTime
T = "legacy_transitions.id"


def _tables():
    return [
        ("legacy_transitions", [
            sa.Column("id", S(40), primary_key=True),
            sa.Column("deployment", S(96), nullable=False),
            sa.Column("region", S(8), nullable=False),
            sa.Column("org_id", S(96), nullable=False),
            sa.Column("clinic_id", S(96), nullable=False),
            sa.Column("facility", S(96), nullable=False),
            sa.Column("payee", S(96), nullable=False),
            sa.Column("payer", S(96), nullable=False),
            sa.Column("currency", S(3), nullable=False),
            sa.Column("cutover", DT(), nullable=False),
            sa.Column("new_route", S(96), nullable=False),
            sa.Column("old_route", S(96), nullable=False),
            sa.Column("fallback_route", S(96), nullable=False),
            sa.Column("archive_route", S(96), nullable=False),
            sa.Column("max_probe_age_days", sa.Integer(), nullable=False),
            sa.Column("label", S(160), nullable=True),
            sa.Column("origin", S(16), nullable=False),
            sa.Column("ownership_json", sa.Text(), nullable=True),
            sa.Column("current_assessment_id", S(40), nullable=True),
            sa.Column("created_by", S(160), nullable=False),
            sa.Column("created_at", DT(), nullable=False),
            sa.UniqueConstraint("deployment", "org_id", "clinic_id", "facility", "payee", "payer", "currency",
                                name="uq_legacy_transition_scope"),
        ], ["clinic_id"]),
        ("legacy_source_bindings", [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("connector", S(96), nullable=False),
            sa.Column("role", S(16), nullable=False),
            sa.Column("valid_from", DT(), nullable=False),
            sa.Column("valid_until", DT(), nullable=False),
            sa.Column("created_by", S(160), nullable=False),
            sa.Column("created_at", DT(), nullable=False),
            sa.UniqueConstraint("transition_id", "connector", name="uq_legacy_binding_connector"),
        ], ["transition_id"]),
        ("legacy_stream_heads", [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("connector", S(96), nullable=False),
            sa.Column("resource", S(96), nullable=False),
            sa.Column("head_token", S(96), nullable=False),
            sa.Column("updated_at", DT(), nullable=False),
            sa.UniqueConstraint("transition_id", "connector", "resource", name="uq_legacy_stream"),
        ], ["transition_id"]),
        ("legacy_pages", [
            sa.Column("id", S(40), primary_key=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("connector", S(96), nullable=False),
            sa.Column("resource", S(96), nullable=False),
            sa.Column("token", S(96), nullable=False),
            sa.Column("previous_token", S(96), nullable=True),
            sa.Column("digest", S(64), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("seal_count", sa.Integer(), nullable=False),
            sa.Column("evidence_id", S(64), nullable=False),
            sa.Column("received_at", DT(), nullable=False),
            sa.Column("received_by", S(160), nullable=False),
            sa.UniqueConstraint("transition_id", "connector", "resource", "token", name="uq_legacy_page"),
        ], ["transition_id"]),
        ("legacy_records", [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("page_id", S(40), sa.ForeignKey("legacy_pages.id"), nullable=False),
            sa.Column("connector", S(96), nullable=False),
            sa.Column("kind", S(24), nullable=False),
            sa.Column("business_key", S(96), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("content_digest", S(64), nullable=False),
            sa.Column("evidence_ref", S(96), nullable=False),
            sa.Column("effective_at", DT(), nullable=False),
            sa.Column("seen_at", DT(), nullable=False),
            sa.Column("received_at", DT(), nullable=False),
            sa.Column("record_json", sa.Text(), nullable=False),
            sa.UniqueConstraint("transition_id", "connector", "kind", "business_key", "revision", "content_digest",
                                name="uq_legacy_record_delivery"),
        ], ["transition_id"]),
        ("legacy_seals", [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("page_id", S(40), sa.ForeignKey("legacy_pages.id"), nullable=False),
            sa.Column("connector", S(96), nullable=False),
            sa.Column("role", S(16), nullable=False),
            sa.Column("through", DT(), nullable=False),
            sa.Column("unique_rows", sa.Integer(), nullable=False),
            sa.Column("digest", S(64), nullable=False),
            sa.Column("receipt", S(96), nullable=False),
            sa.Column("received_at", DT(), nullable=False),
            sa.UniqueConstraint("transition_id", "connector", "through", "receipt", name="uq_legacy_seal"),
        ], ["transition_id"]),
        ("legacy_assessments", [
            sa.Column("id", S(40), primary_key=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("as_of", DT(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("status", S(24), nullable=False),
            sa.Column("input_digest", S(64), nullable=False),
            sa.Column("policy_version", S(48), nullable=False),
            sa.Column("mapping_version", S(48), nullable=False),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("issues_json", sa.Text(), nullable=False),
            sa.Column("follow_up_json", sa.Text(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("summary_json", sa.Text(), nullable=False),
            sa.Column("review_fact_json", sa.Text(), nullable=False),
            sa.Column("computed_at", DT(), nullable=False),
            sa.Column("computed_by", S(160), nullable=False),
            sa.UniqueConstraint("transition_id", "input_digest", name="uq_legacy_assessment_input"),
            sa.UniqueConstraint("transition_id", "as_of", "revision", name="uq_legacy_assessment_revision"),
        ], ["transition_id"]),
        ("legacy_work_items", [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("kind", S(16), nullable=False),
            sa.Column("code", S(48), nullable=False),
            sa.Column("item", S(160), nullable=False),
            sa.Column("issues_json", sa.Text(), nullable=True),
            sa.Column("owner", S(96), nullable=True),
            sa.Column("state", S(16), nullable=False),
            sa.Column("first_assessment_id", S(40), nullable=False),
            sa.Column("last_assessment_id", S(40), nullable=False),
            sa.Column("created_at", DT(), nullable=False),
            sa.Column("accepted_by", S(160), nullable=True),
            sa.Column("accepted_at", DT(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("resolved_at", DT(), nullable=True),
            sa.UniqueConstraint("transition_id", "code", "item", name="uq_legacy_work_item"),
        ], ["transition_id"]),
        ("legacy_work_item_events", [
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("work_item_id", sa.Integer(), sa.ForeignKey("legacy_work_items.id"), nullable=False),
            sa.Column("transition_id", S(40), sa.ForeignKey(T), nullable=False),
            sa.Column("event", S(16), nullable=False),
            sa.Column("from_state", S(16), nullable=True),
            sa.Column("to_state", S(16), nullable=False),
            sa.Column("actor", S(160), nullable=False),
            sa.Column("assessment_id", S(40), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("at", DT(), nullable=False),
        ], ["work_item_id", "transition_id"]),
        ("legacy_exit_inventories", [
            sa.Column("id", S(40), primary_key=True),
            sa.Column("org_id", S(96), nullable=False),
            sa.Column("clinic_id", S(96), nullable=False),
            sa.Column("subscription", S(96), nullable=False),
            sa.Column("as_of", DT(), nullable=False),
            sa.Column("digest", S(64), nullable=False),
            sa.Column("inventory_json", sa.Text(), nullable=False),
            sa.Column("received_at", DT(), nullable=False),
            sa.Column("received_by", S(160), nullable=False),
            sa.UniqueConstraint("clinic_id", "subscription", "digest", name="uq_legacy_exit_inventory"),
        ], ["clinic_id"]),
    ]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for name, columns, indexed in _tables():
        if insp.has_table(name):
            continue
        op.create_table(name, *columns)
        for col in indexed:
            op.create_index(f"ix_{name}_{col}", name, [col])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for name, _columns, _indexed in reversed(_tables()):
        if insp.has_table(name):
            op.drop_table(name)
