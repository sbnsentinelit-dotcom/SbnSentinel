"""HSP01 additive hospital schema and audited exact-money conversion.

No facility is matched by name or ID coincidence. Legacy facilities enter an
UNRESOLVED crosswalk for explicit reconciliation. Downgrade is intentionally
blocked: application rollback must preserve new history and exact amounts.
"""
from decimal import Decimal, InvalidOperation
from alembic import op
import sqlalchemy as sa

revision = "hsp01_hospital_extension"
down_revision = "g8_financial_reporting"
branch_labels = None
depends_on = None


def upgrade():
    from app.db.database import Base
    from app.models import hospital_dimensions, hospital_membership, hospital_processing, work_assignment
    from app.models import organization, clinic, user, encounter, financial_reporting, event, evidence, governance_storage
    bind = op.get_bind()
    new_tables = {
        "hospital_departments", "hospital_department_versions", "hospital_locations",
        "hospital_location_memberships", "hospital_provider_memberships", "hospital_memberships",
        "hospital_facility_crosswalk", "hospital_source_crosswalk", "hospital_checkpoints",
        "hospital_quarantine", "hospital_projection_receipts", "hospital_work_assignments",
        "hospital_assignment_history",
        "hospital_settlement_links", "hospital_settlement_observations",
        "hospital_snapshot_pages",
    }
    # Metadata order ensures references exist; baseline/import registration owns
    # the shared tables. This revision does not reset or recreate clinic data.
    for table in Base.metadata.sorted_tables:
        if table.name in new_tables:
            table.create(bind, checkfirst=True)
    audit = sa.Table("hospital_legacy_money_audit", sa.MetaData(),
        sa.Column("table_name", sa.String, primary_key=True),
        sa.Column("row_key", sa.String, primary_key=True),
        sa.Column("column_name", sa.String, primary_key=True),
        sa.Column("legacy_value_text", sa.Text, nullable=False),
        sa.Column("decimal_value_text", sa.Text, nullable=False))
    audit.create(bind, checkfirst=True)
    targets = ["encounters", "pipeline_events", "financial_claims", "financial_claim_lines",
        "financial_submission_attempts", "financial_remittances", "financial_payment_receipts", "financial_adjustments",
        "report_snapshots", "report_runs", "report_definitions", "ingestion_ledger", "audit_logs"]
    for table_name in targets:
        table = Base.metadata.tables[table_name]
        columns = {column["name"]: column for column in sa.inspect(bind).get_columns(table_name)}
        for column in table.columns:
            if column.name not in columns:
                default = None
                if not column.nullable:
                    if isinstance(column.type, sa.Boolean): default = sa.false()
                    elif isinstance(column.type, sa.Integer): default = sa.text("0")
                    else: raise RuntimeError(f"Required backfill missing for {table_name}.{column.name}")
                op.add_column(table_name, sa.Column(column.name, column.type,
                    nullable=column.nullable, server_default=default))
            elif isinstance(column.type, sa.Numeric) and isinstance(columns[column.name]["type"], sa.Float):
                primary = list(table.primary_key.columns)[0]
                # Preserve the original round-trip decimal representation. Fail
                # rather than silently round out-of-scale/out-of-range values.
                legacy_table = sa.Table(table_name, sa.MetaData(), autoload_with=bind)
                raw_column = legacy_table.c[column.name]
                result = bind.execute(sa.select(legacy_table.c[primary.name], raw_column)
                    .where(raw_column.is_not(None))).yield_per(500)
                for key, value in result:
                    original = str(value)
                    try:
                        exact = Decimal(original)
                        quantized = exact.quantize(Decimal("0.00000001"))
                    except (InvalidOperation, ValueError):
                        raise RuntimeError("Legacy money requires explicit reconciliation") from None
                    if not exact.is_finite() or exact != quantized or exact.adjusted() >= 20:
                        raise RuntimeError("Legacy money requires explicit reconciliation")
                    exists = bind.execute(sa.select(audit.c.row_key).where(
                        audit.c.table_name == table_name, audit.c.row_key == str(key),
                        audit.c.column_name == column.name)).first()
                    if not exists:
                        bind.execute(audit.insert().values(table_name=table_name, row_key=str(key),
                            column_name=column.name, legacy_value_text=original, decimal_value_text=format(exact, "f")))
                if bind.dialect.name == "postgresql":
                    quoted = bind.dialect.identifier_preparer.quote(column.name)
                    op.alter_column(table_name, column.name, type_=column.type,
                        postgresql_using=f"{quoted}::text::numeric(28,8)")
                else:
                    with op.batch_alter_table(table_name) as batch:
                        batch.alter_column(column.name, type_=column.type)
            elif (bind.dialect.name == "postgresql" and isinstance(column.type, sa.String)
                  and column.type.length is None and getattr(columns[column.name]["type"], "length", None)):
                op.alter_column(table_name, column.name, type_=sa.String())
        known_indexes = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
        for index in table.indexes:
            if index.name not in known_indexes:
                index.create(bind)
        known_foreign_keys = {(tuple(item["constrained_columns"]), item["referred_table"],
            tuple(item["referred_columns"])) for item in sa.inspect(bind).get_foreign_keys(table_name)}
        for constraint in table.foreign_key_constraints:
            local = [element.parent.name for element in constraint.elements]
            referred = [element.column.name for element in constraint.elements]
            referred_table = constraint.elements[0].column.table.name
            identity = (tuple(local), referred_table, tuple(referred))
            if identity not in known_foreign_keys:
                name = constraint.name or "fk_hsp01_" + table_name + "_" + "_".join(local)
                with op.batch_alter_table(table_name) as batch:
                    batch.create_foreign_key(name, referred_table, local, referred)
    queue_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("pipeline_events")}
    if "uq_hsp01_delivery_key" not in queue_indexes:
        op.create_index("uq_hsp01_delivery_key", "pipeline_events", ["delivery_key"], unique=True)
    crosswalk = Base.metadata.tables["hospital_facility_crosswalk"]
    legacy = Base.metadata.tables["clinics"]
    for row in bind.execute(sa.select(legacy.c.id).where(
            ~legacy.c.id.in_(sa.select(crosswalk.c.legacy_clinic_id)))).yield_per(500):
        bind.execute(crosswalk.insert().values(legacy_clinic_id=row.id, status="UNRESOLVED"))


def downgrade():
    raise RuntimeError("HSP01 history must be preserved; use a compatible application rollback")
