"""HSP01 auditor corrections: persistent enrollment and preserved source meaning."""
import json
from decimal import Decimal, InvalidOperation
from alembic import op
import sqlalchemy as sa

revision = "hsp01_auditor_corrections"
down_revision = "hsp01_hospital_extension"
branch_labels = None
depends_on = None


def upgrade():
    from app.db.database import Base
    from app.models import hospital_dimensions, hospital_processing, financial_reporting, event  # noqa: F401
    bind = op.get_bind()
    for name in ("hospital_facility_enrollments", "hospital_transport_observations"):
        Base.metadata.tables[name].create(bind, checkfirst=True)
    for name in ("financial_claim_lines", "hospital_quarantine", "pipeline_events"):
        existing = {column["name"] for column in sa.inspect(bind).get_columns(name)}
        for column in Base.metadata.tables[name].columns:
            if column.name not in existing:
                op.add_column(name, sa.Column(column.name, column.type, nullable=True))
    enrollment = Base.metadata.tables["hospital_facility_enrollments"]
    for row in bind.execute(sa.text("""SELECT clinic_id FROM hospital_departments
        UNION SELECT clinic_id FROM connectors WHERE vendor_id='hospital_read'""")):
        if row.clinic_id and not bind.execute(sa.select(enrollment.c.clinic_id).where(
                enrollment.c.clinic_id == row.clinic_id)).first():
            canonical = bind.execute(sa.text("SELECT id FROM org_clinics WHERE id=:id"), {"id": row.clinic_id}).first()
            if canonical:
                bind.execute(enrollment.insert().values(clinic_id=row.clinic_id, basis_ref="migration:existing-hospital-configuration"))
    # Never infer a historical line currency from its parent's CURRENT currency.
    for row in bind.execute(sa.text("""SELECT l.line_id, l.clinic_id, l.claim_id, l.billed_amount,
        e.fact_value_str, e.resource_kind FROM financial_claim_lines l
        JOIN evidence_repository e ON e.evidence_id=l.source_evidence_id
        WHERE l.currency IS NULL""")).yield_per(500):
        try:
            facts = json.loads(row.fact_value_str)
            currency = facts.get("currency")
        except (ValueError, TypeError):
            continue
        try:
            same_amount = Decimal(str(facts.get("billed_amount"))) == Decimal(str(row.billed_amount))
        except (InvalidOperation, ValueError):
            same_amount = False
        if (facts.get("identity_scheme") == "hospital-source-v1" and facts.get("quality_state") == "VALID"
                and row.resource_kind == "ClaimLine" and facts.get("id") == row.line_id
                and facts.get("clinic_id") == row.clinic_id and facts.get("parent_claim_key") == row.claim_id
                and same_amount and isinstance(currency, str) and len(currency) == 3
                and currency.isalpha() and currency.isupper()):
            bind.execute(sa.text("UPDATE financial_claim_lines SET currency=:currency WHERE line_id=:id"),
                         {"currency": currency, "id": row.line_id})
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("hospital_quarantine")}
    if "uq_hsp01_quarantine_key" not in indexes:
        op.create_index("uq_hsp01_quarantine_key", "hospital_quarantine", ["quarantine_key"], unique=True)


def downgrade():
    raise RuntimeError("Hospital security classification and financial history must be preserved")
