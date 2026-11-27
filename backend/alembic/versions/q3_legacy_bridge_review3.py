"""q3_legacy_bridge_review3

Legacy Claims Bridge, PR 07 third review: the exact input manifest of each assessment and its
supersession link (07.T1, 07.T3), replaced completeness statements (07.T4), and the proof, applied
amount and accepted scope of a work outcome (07.T2, 07.T5). Additive; existing rows are kept.

Revision ID: q3_legacy_bridge_review3
Revises: q2_legacy_bridge_review
Create Date: 2026-11-27 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'q3_legacy_bridge_review3'
down_revision = 'q2_legacy_bridge_review'
branch_labels = None
depends_on = None

COLUMNS = (
    ("legacy_assessments", "manifest_json", sa.Text()),
    ("legacy_assessments", "snapshot_at", sa.DateTime()),
    ("legacy_assessments", "supersedes_id", sa.String(40)),
    ("legacy_seals", "superseded_by_id", sa.Integer()),
    ("legacy_work_items", "outcome_assessment_id", sa.String(40)),
    ("legacy_work_items", "outcome_proof_json", sa.Text()),
    ("legacy_work_items", "outcome_amount", sa.BigInteger()),
    ("legacy_work_items", "outcome_scope_json", sa.Text()),
)


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table, name, kind in COLUMNS:
        if name not in _columns(insp, table):
            op.add_column(table, sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in ("legacy_work_items", "legacy_seals", "legacy_assessments"):
        present = [name for t, name, _k in COLUMNS if t == table and name in _columns(insp, table)]
        if present:
            with op.batch_alter_table(table) as batch:
                for name in present:
                    batch.drop_column(name)
