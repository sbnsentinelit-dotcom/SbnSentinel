"""HSP01 schema completion: governed pipeline tables missing from the forward sequence.

HSP01 disabled implicit schema autocreate, so a fresh database is built only by
this Alembic sequence. Before this revision the sequence did not create the
decision-context (layer 4) tables or decision_records, which the running
ProcessingOrchestrator, decision-basis and history routes require. Every event
on a fresh install therefore failed at L4 with "no such table:
dce_context_evidence".

Only runtime-used tables are added here. Legacy/future-scope model modules that
no runtime code imports (governance, collaboration, explainability, evidence
evaluation, recommendation generation, integration) are intentionally left out.
checkfirst keeps the upgrade safe for databases that already have these tables
from the earlier create_all path.
"""
from alembic import op

revision = "hsp01_schema_completion"
down_revision = "hsp01_auditor_corrections"
branch_labels = None
depends_on = None

TABLES = (
    "dce_context_evidence",
    "dce_context_relationships",
    "dce_context_conflicts",
    "dce_context_missing_evidence",
    "dce_context_freshness",
    "dce_context_provenance",
    "decision_records",
)


def upgrade():
    from app.db.database import Base
    from app.models import event, decision_context_models, decision_record  # noqa: F401
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Governed decision context and decision history must be preserved")
