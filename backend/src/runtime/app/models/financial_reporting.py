from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, ForeignKey
from datetime import datetime
from app.db.database import Base


class FinancialClaimModel(Base):
    __tablename__ = "financial_claims"

    claim_id = Column(String(64), primary_key=True, index=True)
    clinic_id = Column(String(64), index=True, nullable=False)
    patient_id = Column(String(64), index=True, nullable=False)
    provider_id = Column(String(64), index=True, nullable=False)
    payer_id = Column(String(64), index=True, nullable=False)
    tpa_id = Column(String(64), nullable=True)  # Third-Party Administrator
    status = Column(String(32), nullable=False)  # draft, submitted, rejected_technical, adjudicated, paid, denied
    billed_amount = Column(Float, nullable=False, default=0.0)
    allowed_amount = Column(Float, nullable=True)
    paid_amount = Column(Float, nullable=True)
    patient_share = Column(Float, nullable=True, default=0.0)
    currency = Column(String(8), nullable=False, default="AED")
    service_date = Column(DateTime, nullable=False)
    submission_date = Column(DateTime, nullable=True)
    adjudication_date = Column(DateTime, nullable=True)
    rejection_reason = Column(String(255), nullable=True)
    denial_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinancialClaimLineModel(Base):
    __tablename__ = "financial_claim_lines"

    line_id = Column(String(64), primary_key=True, index=True)
    claim_id = Column(String(64), ForeignKey("financial_claims.claim_id"), index=True, nullable=False)
    procedure_code = Column(String(64), nullable=False)  # CPT / HCPCS / DHA
    code_system = Column(String(32), nullable=False, default="CPT")
    code_version = Column(String(16), nullable=True, default="2026")
    units = Column(Integer, nullable=False, default=1)
    billed_amount = Column(Float, nullable=False, default=0.0)
    allowed_amount = Column(Float, nullable=True)
    paid_amount = Column(Float, nullable=True)
    adjusted_amount = Column(Float, nullable=True, default=0.0)
    line_status = Column(String(32), nullable=False, default="submitted")  # submitted, paid, partially_paid, denied
    denial_code = Column(String(64), nullable=True)
    denial_reason = Column(String(255), nullable=True)


class FinancialSubmissionAttemptModel(Base):
    __tablename__ = "financial_submission_attempts"

    attempt_id = Column(String(64), primary_key=True, index=True)
    claim_id = Column(String(64), ForeignKey("financial_claims.claim_id"), index=True, nullable=False)
    attempt_number = Column(Integer, nullable=False, default=1)
    submission_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    response_date = Column(DateTime, nullable=True)
    outcome = Column(String(32), nullable=False)  # accepted, rejected_technical, disputed
    rejection_reason = Column(String(255), nullable=True)


class FinancialRemittanceModel(Base):
    __tablename__ = "financial_remittances"

    remittance_id = Column(String(64), primary_key=True, index=True)
    clinic_id = Column(String(64), index=True, nullable=False)
    payer_id = Column(String(64), index=True, nullable=False)
    payment_reference = Column(String(128), nullable=False)
    remittance_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    total_paid = Column(Float, nullable=False, default=0.0)
    currency = Column(String(8), nullable=False, default="AED")


class FinancialPaymentReceiptModel(Base):
    __tablename__ = "financial_payment_receipts"

    receipt_id = Column(String(64), primary_key=True, index=True)
    clinic_id = Column(String(64), index=True, nullable=False)
    receipt_type = Column(String(32), nullable=False)  # payer_payment, patient_copay, patient_selfpay
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(8), nullable=False, default="AED")
    received_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    remittance_id = Column(String(64), nullable=True)
    claim_id = Column(String(64), nullable=True)
    bank_confirmed = Column(Boolean, default=False, nullable=False)


class FinancialAdjustmentModel(Base):
    __tablename__ = "financial_adjustments"

    adjustment_id = Column(String(64), primary_key=True, index=True)
    claim_id = Column(String(64), index=True, nullable=False)
    line_id = Column(String(64), nullable=True)
    adjustment_type = Column(String(32), nullable=False)  # contractual, charity, write_off, reversal, refund
    amount = Column(Float, nullable=False, default=0.0)
    reason_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ReportDefinitionModel(Base):
    __tablename__ = "report_definitions"

    definition_id = Column(String(64), primary_key=True, index=True)
    code = Column(String(16), unique=True, index=True, nullable=False)  # FR01, FR02, ...
    name = Column(String(128), nullable=False)
    description = Column(String(255), nullable=True)
    version = Column(String(16), nullable=False, default="1.0")


class ReportRunModel(Base):
    __tablename__ = "report_runs"

    run_key = Column(String(128), primary_key=True, index=True)
    clinic_id = Column(String(64), index=True, nullable=False)
    definition_id = Column(String(64), nullable=False)
    definition_version = Column(String(16), nullable=False, default="1.0")
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    cutoff_time = Column(DateTime, nullable=False)
    status = Column(String(32), nullable=False, default="complete")  # pending, running, complete, partial, failed
    created_at = Column(DateTime, default=datetime.utcnow)


class ReportSnapshotModel(Base):
    __tablename__ = "report_snapshots"

    snapshot_id = Column(String(64), primary_key=True, index=True)
    run_key = Column(String(128), ForeignKey("report_runs.run_key"), index=True, nullable=False)
    clinic_id = Column(String(64), index=True, nullable=False)
    report_code = Column(String(16), index=True, nullable=False)
    payload_json = Column(Text, nullable=False)
    summary_metrics_json = Column(Text, nullable=False)
    completeness_status = Column(String(32), nullable=False, default="COMPLETE")  # COMPLETE, PARTIAL, STALE_FEED
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
