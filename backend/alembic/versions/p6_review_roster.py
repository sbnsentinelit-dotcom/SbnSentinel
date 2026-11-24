"""p6_review_roster

Claim review roster per clinic (reviewer, qualification, shift, assignment), read by the review-capacity
severity check.

Revision ID: p6_review_roster
Revises: p5_patient_flow_events
Create Date: 2026-11-24 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'p6_review_roster'
down_revision = 'p5_patient_flow_events'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("review_roster"):
        op.create_table(
            "review_roster",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("clinic_id", sa.String(64), nullable=False),
            sa.Column("reviewer", sa.String(160), nullable=False),
            sa.Column("role", sa.String(64), nullable=True),
            sa.Column("qualified", sa.Boolean(), nullable=False),
            sa.Column("shift_start", sa.String(5), nullable=False),
            sa.Column("shift_end", sa.String(5), nullable=False),
            sa.Column("assigned", sa.Boolean(), nullable=False),
            sa.Column("source", sa.String(64), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by", sa.String(160), nullable=True),
        )
        op.create_index("ix_review_roster_clinic_id", "review_roster", ["clinic_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("review_roster"):
        op.drop_table("review_roster")
