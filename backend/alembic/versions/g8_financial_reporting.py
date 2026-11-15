"""g8_financial_reporting

Revision ID: g8_financial_reporting
Revises: g5_durable_ingestion
Create Date: 2026-11-15 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'g8_financial_reporting'
down_revision = 'g5_durable_ingestion'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1. financial_claims
    if not insp.has_table("financial_claims"):
        op.create_table(
            "financial_claims",
            sa.Column("claim_id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(64), nullable=False, index=True),
            sa.Column("patient_id", sa.String(64), nullable=False, index=True),
            sa.Column("provider_id", sa.String(64), nullable=False, index=True),
            sa.Column("payer_id", sa.String(64), nullable=False, index=True),
            sa.Column("tpa_id", sa.String(64), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("billed_amount", sa.Float(), nullable=False, default=0.0),
            sa.Column("allowed_amount", sa.Float(), nullable=True),
            sa.Column("paid_amount", sa.Float(), nullable=True),
            sa.Column("patient_share", sa.Float(), nullable=True, default=0.0),
            sa.Column("currency", sa.String(8), nullable=False, default="AED"),
            sa.Column("service_date", sa.DateTime(), nullable=False),
            sa.Column("submission_date", sa.DateTime(), nullable=True),
            sa.Column("adjudication_date", sa.DateTime(), nullable=True),
            sa.Column("rejection_reason", sa.String(255), nullable=True),
            sa.Column("denial_code", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), default=sa.func.now(), onupdate=sa.func.now()),
        )

    # 2. financial_claim_lines
    if not insp.has_table("financial_claim_lines"):
        op.create_table(
            "financial_claim_lines",
            sa.Column("line_id", sa.String(64), primary_key=True),
            sa.Column("claim_id", sa.String(64), sa.ForeignKey("financial_claims.claim_id"), nullable=False, index=True),
            sa.Column("procedure_code", sa.String(64), nullable=False),
            sa.Column("code_system", sa.String(32), nullable=False, default="CPT"),
            sa.Column("code_version", sa.String(16), nullable=True, default="2026"),
            sa.Column("units", sa.Integer(), nullable=False, default=1),
            sa.Column("billed_amount", sa.Float(), nullable=False, default=0.0),
            sa.Column("allowed_amount", sa.Float(), nullable=True),
            sa.Column("paid_amount", sa.Float(), nullable=True),
            sa.Column("adjusted_amount", sa.Float(), nullable=True, default=0.0),
            sa.Column("line_status", sa.String(32), nullable=False, default="submitted"),
            sa.Column("denial_code", sa.String(64), nullable=True),
            sa.Column("denial_reason", sa.String(255), nullable=True),
        )

    # 3. financial_submission_attempts
    if not insp.has_table("financial_submission_attempts"):
        op.create_table(
            "financial_submission_attempts",
            sa.Column("attempt_id", sa.String(64), primary_key=True),
            sa.Column("claim_id", sa.String(64), sa.ForeignKey("financial_claims.claim_id"), nullable=False, index=True),
            sa.Column("attempt_number", sa.Integer(), nullable=False, default=1),
            sa.Column("submission_date", sa.DateTime(), nullable=False),
            sa.Column("response_date", sa.DateTime(), nullable=True),
            sa.Column("outcome", sa.String(32), nullable=False),
            sa.Column("rejection_reason", sa.String(255), nullable=True),
        )

    # 4. financial_remittances
    if not insp.has_table("financial_remittances"):
        op.create_table(
            "financial_remittances",
            sa.Column("remittance_id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(64), nullable=False, index=True),
            sa.Column("payer_id", sa.String(64), nullable=False, index=True),
            sa.Column("payment_reference", sa.String(128), nullable=False),
            sa.Column("remittance_date", sa.DateTime(), nullable=False),
            sa.Column("total_paid", sa.Float(), nullable=False, default=0.0),
            sa.Column("currency", sa.String(8), nullable=False, default="AED"),
        )

    # 5. financial_payment_receipts
    if not insp.has_table("financial_payment_receipts"):
        op.create_table(
            "financial_payment_receipts",
            sa.Column("receipt_id", sa.String(64), primary_key=True),
            sa.Column("clinic_id", sa.String(64), nullable=False, index=True),
            sa.Column("receipt_type", sa.String(32), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False, default=0.0),
            sa.Column("currency", sa.String(8), nullable=False, default="AED"),
            sa.Column("received_date", sa.DateTime(), nullable=False),
            sa.Column("remittance_id", sa.String(64), nullable=True),
            sa.Column("claim_id", sa.String(64), nullable=True),
            sa.Column("bank_confirmed", sa.Boolean(), nullable=False, default=False),
        )

    # 6. financial_adjustments
    if not insp.has_table("financial_adjustments"):
        op.create_table(
            "financial_adjustments",
            sa.Column("adjustment_id", sa.String(64), primary_key=True),
            sa.Column("claim_id", sa.String(64), nullable=False, index=True),
            sa.Column("line_id", sa.String(64), nullable=True),
            sa.Column("adjustment_type", sa.String(32), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False, default=0.0),
            sa.Column("reason_code", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        )

    # 7. report_definitions
    if not insp.has_table("report_definitions"):
        op.create_table(
            "report_definitions",
            sa.Column("definition_id", sa.String(64), primary_key=True),
            sa.Column("code", sa.String(16), unique=True, index=True, nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(255), nullable=True),
            sa.Column("version", sa.String(16), nullable=False, default="1.0"),
        )

    # 8. report_runs
    if not insp.has_table("report_runs"):
        op.create_table(
            "report_runs",
            sa.Column("run_key", sa.String(128), primary_key=True),
            sa.Column("clinic_id", sa.String(64), nullable=False, index=True),
            sa.Column("definition_id", sa.String(64), nullable=False),
            sa.Column("definition_version", sa.String(16), nullable=False, default="1.0"),
            sa.Column("period_start", sa.DateTime(), nullable=False),
            sa.Column("period_end", sa.DateTime(), nullable=False),
            sa.Column("cutoff_time", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, default="complete"),
            sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        )

    # 9. report_snapshots
    if not insp.has_table("report_snapshots"):
        op.create_table(
            "report_snapshots",
            sa.Column("snapshot_id", sa.String(64), primary_key=True),
            sa.Column("run_key", sa.String(128), sa.ForeignKey("report_runs.run_key"), nullable=False, index=True),
            sa.Column("clinic_id", sa.String(64), nullable=False, index=True),
            sa.Column("report_code", sa.String(16), nullable=False, index=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("summary_metrics_json", sa.Text(), nullable=False),
            sa.Column("completeness_status", sa.String(32), nullable=False, default="COMPLETE"),
            sa.Column("revision", sa.Integer(), nullable=False, default=1),
            sa.Column("created_at", sa.DateTime(), default=sa.func.now()),
        )


def downgrade() -> None:
    pass
