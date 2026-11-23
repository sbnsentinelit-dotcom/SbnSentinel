"""p5_patient_flow_events

Patient Flow is calculated from visit events: visit_events, clinic_rooms, patient_flow_actions,
and the patient's stable identifier and MRN on encounters.

Revision ID: p5_patient_flow_events
Revises: g8_financial_reporting
Create Date: 2026-11-23 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'p5_patient_flow_events'
down_revision = 'g8_financial_reporting'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("visit_events"):
        op.create_table(
            "visit_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_key", sa.String(160), nullable=False),
            sa.Column("clinic_id", sa.String(64), nullable=False),
            sa.Column("visit_id", sa.String(64), nullable=True),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("event_time", sa.DateTime(), nullable=False),
            sa.Column("patient_ref", sa.String(64), nullable=True),
            sa.Column("patient_name", sa.String(160), nullable=True),
            sa.Column("provider", sa.String(160), nullable=True),
            sa.Column("department", sa.String(120), nullable=True),
            sa.Column("priority", sa.String(32), nullable=True),
            sa.Column("room", sa.String(64), nullable=True),
            sa.Column("detail", sa.String(255), nullable=True),
            sa.Column("source", sa.String(64), nullable=False),
            sa.Column("recorded_by", sa.String(160), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("event_key", name="uq_visit_events_event_key"),
        )
        op.create_index("ix_visit_events_event_key", "visit_events", ["event_key"])
        op.create_index("ix_visit_events_clinic_id", "visit_events", ["clinic_id"])
        op.create_index("ix_visit_events_visit_id", "visit_events", ["visit_id"])
        op.create_index("ix_visit_events_event_time", "visit_events", ["event_time"])

    if not insp.has_table("clinic_rooms"):
        op.create_table(
            "clinic_rooms",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("clinic_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("doctor", sa.String(160), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("clinic_id", "name", name="uq_clinic_rooms_clinic_name"),
        )
        op.create_index("ix_clinic_rooms_clinic_id", "clinic_rooms", ["clinic_id"])

    if not insp.has_table("patient_flow_actions"):
        op.create_table(
            "patient_flow_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("action_id", sa.String(40), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("clinic_id", sa.String(64), nullable=False),
            sa.Column("action_type", sa.String(40), nullable=False),
            sa.Column("visit_id", sa.String(64), nullable=True),
            sa.Column("suggestion_id", sa.String(160), nullable=True),
            sa.Column("recipient", sa.String(160), nullable=True),
            sa.Column("message", sa.String(500), nullable=True),
            sa.Column("basis", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("connector", sa.String(80), nullable=True),
            sa.Column("receipt_ref", sa.String(80), nullable=True),
            sa.Column("detail", sa.String(255), nullable=True),
            sa.Column("actor", sa.String(160), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_patient_flow_actions_key"),
        )
        op.create_index("ix_patient_flow_actions_action_id", "patient_flow_actions", ["action_id"])
        op.create_index("ix_patient_flow_actions_clinic_id", "patient_flow_actions", ["clinic_id"])
        op.create_index("ix_patient_flow_actions_visit_id", "patient_flow_actions", ["visit_id"])
        op.create_index("ix_patient_flow_actions_suggestion_id", "patient_flow_actions", ["suggestion_id"])

    if insp.has_table("encounters"):
        cols = {c["name"] for c in insp.get_columns("encounters")}
        if "patient_ref" not in cols:
            op.add_column("encounters", sa.Column("patient_ref", sa.String(), nullable=True))
            op.create_index("ix_encounters_patient_ref", "encounters", ["patient_ref"])
        if "mrn" not in cols:
            op.add_column("encounters", sa.Column("mrn", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ("patient_flow_actions", "clinic_rooms", "visit_events"):
        if insp.has_table(table):
            op.drop_table(table)
    if insp.has_table("encounters"):
        cols = {c["name"] for c in insp.get_columns("encounters")}
        if "mrn" in cols:
            op.drop_column("encounters", "mrn")
        if "patient_ref" in cols:
            op.drop_index("ix_encounters_patient_ref", table_name="encounters")
            op.drop_column("encounters", "patient_ref")
