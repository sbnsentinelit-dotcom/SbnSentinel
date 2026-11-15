"""g3_connector_authority

Revision ID: g3_connector_authority
Revises: cp08_recommendation_fields
Create Date: 2026-11-15 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'g3_connector_authority'
down_revision = 'cp08_recommendation_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    table_name = "connectors"
    existing_cols = {c['name'] for c in insp.get_columns(table_name)} if insp.has_table(table_name) else set()

    with op.batch_alter_table(table_name) as batch_op:
        if 'vendor_id' not in existing_cols:
            batch_op.add_column(sa.Column('vendor_id', sa.String(length=100), nullable=True))
        if 'clinic_id' not in existing_cols:
            batch_op.add_column(sa.Column('clinic_id', sa.String(length=100), nullable=True))
        if 'secret_ref' not in existing_cols:
            batch_op.add_column(sa.Column('secret_ref', sa.String(length=255), nullable=True))
        if 'permitted_resources' not in existing_cols:
            batch_op.add_column(sa.Column('permitted_resources', sa.JSON(), nullable=True))
        if 'state' not in existing_cols:
            batch_op.add_column(sa.Column('state', sa.String(length=50), nullable=True, server_default='CONFIGURED'))


def downgrade() -> None:
    with op.batch_alter_table("connectors") as batch_op:
        batch_op.drop_column('state')
        batch_op.drop_column('permitted_resources')
        batch_op.drop_column('secret_ref')
        batch_op.drop_column('clinic_id')
        batch_op.drop_column('vendor_id')
