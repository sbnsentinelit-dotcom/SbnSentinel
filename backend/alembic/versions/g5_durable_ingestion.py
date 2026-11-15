"""g5_durable_ingestion

Revision ID: g5_durable_ingestion
Revises: g3_connector_authority
Create Date: 2026-11-15 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'g5_durable_ingestion'
down_revision = 'g3_connector_authority'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. Additive columns to evidence_repository
    ev_table = "evidence_repository"
    if insp.has_table(ev_table):
        ev_cols = {c['name'] for c in insp.get_columns(ev_table)}
        with op.batch_alter_table(ev_table) as batch_op:
            if 'tombstone' not in ev_cols:
                batch_op.add_column(sa.Column('tombstone', sa.Boolean(), nullable=True, server_default=sa.text('0')))
            if 'effective_time' not in ev_cols:
                batch_op.add_column(sa.Column('effective_time', sa.DateTime(), nullable=True))
            if 'ingestion_sequence' not in ev_cols:
                batch_op.add_column(sa.Column('ingestion_sequence', sa.Integer(), nullable=True))
            if 'clinic_id' not in ev_cols:
                batch_op.add_column(sa.Column('clinic_id', sa.String(length=100), nullable=True))
            if 'external_record_id' not in ev_cols:
                batch_op.add_column(sa.Column('external_record_id', sa.String(length=255), nullable=True))
            if 'resource_kind' not in ev_cols:
                batch_op.add_column(sa.Column('resource_kind', sa.String(length=100), nullable=True))
            if 'source_version' not in ev_cols:
                batch_op.add_column(sa.Column('source_version', sa.String(length=100), nullable=True))

    # 2. Additive columns to sync_cursors for partitioned checkpoints
    cur_table = "sync_cursors"
    if insp.has_table(cur_table):
        cur_cols = {c['name'] for c in insp.get_columns(cur_table)}
        with op.batch_alter_table(cur_table) as batch_op:
            if 'clinic_id' not in cur_cols:
                batch_op.add_column(sa.Column('clinic_id', sa.String(length=100), nullable=True, server_default='default'))
            if 'is_opaque' not in cur_cols:
                batch_op.add_column(sa.Column('is_opaque', sa.Boolean(), nullable=True, server_default=sa.text('0')))

    # 3. Create ingestion_ledger table if not exists
    if not insp.has_table("ingestion_ledger"):
        op.create_table(
            "ingestion_ledger",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("clinic_id", sa.String(length=100), nullable=False, index=True),
            sa.Column("connector_instance_id", sa.String(length=100), nullable=False, index=True),
            sa.Column("resource_kind", sa.String(length=100), nullable=False),
            sa.Column("external_record_id", sa.String(length=255), nullable=False),
            sa.Column("source_version", sa.String(length=100), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="COMMITTED"),
            sa.Column("evidence_id", sa.String(length=100), nullable=True),
            sa.Column("payload_hash", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("effective_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "clinic_id", "connector_instance_id", "resource_kind", "external_record_id", "source_version",
                name="uq_ingest_ledger_dedupe"
            )
        )


def downgrade() -> None:
    op.drop_table("ingestion_ledger")
