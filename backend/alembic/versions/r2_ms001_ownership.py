"""r2_ms001_ownership

PR #8 corrective revision (A9, A10, settings follow-up, A6-A8 evidence tables):

* Clinic ownership is reconciled from evidence, explicit mappings first. A clinic listed in the earlier
  org_clinics table gets that organization when nothing contradicts it; a clinic that exists only in
  org_clinics is brought into clinics; activity (users, signals) is used only when it names exactly one
  organization. Orphaned and conflicting clinics are written to ownership_review, never guessed.
* Historical audit rows keep only scope recorded when the event happened. Rows without event-time
  evidence become "unresolved" (visible to the platform only), including any scope an earlier run of
  r1 copied from the actor's current account.
* Settings get one row per organization and one per clinic (duplicates merged, unique indexes).
* Evidence tables for provider checks, sync runs and recorded imports.

Revision ID: r2_ms001_ownership
Revises: r1_ms001_foundation
Create Date: 2026-11-28 18:00:00.000000

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = 'r2_ms001_ownership'
down_revision = 'r1_ms001_foundation'
branch_labels = None
depends_on = None

_SETTINGS_INDEXES = ("uq_settings_org_scope", "uq_settings_clinic_scope")


def _create_tables(insp):
    if not insp.has_table("connector_probes"):
        op.create_table(
            "connector_probes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("connector_id", sa.String(), nullable=False, index=True),
            sa.Column("clinic_id", sa.String(), nullable=True, index=True),
            sa.Column("probed_at", sa.DateTime(), nullable=False),
            sa.Column("outcome", sa.String(16), nullable=False),
            sa.Column("detail", sa.String(), nullable=True),
            sa.Column("requested_by", sa.String(), nullable=True),
        )
    if not insp.has_table("connector_sync_runs"):
        op.create_table(
            "connector_sync_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("connector_id", sa.String(), nullable=False, index=True),
            sa.Column("clinic_id", sa.String(), nullable=True, index=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("imported_count", sa.Integer(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("requested_by", sa.String(), nullable=True),
        )
    if not insp.has_table("data_imports"):
        op.create_table(
            "data_imports",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("clinic_id", sa.String(), nullable=False, index=True),
            sa.Column("source_kind", sa.String(16), nullable=False),
            sa.Column("source_label", sa.String(), nullable=False),
            sa.Column("connector_id", sa.String(), nullable=True),
            sa.Column("run_id", sa.String(36), nullable=True),
            sa.Column("accepted_count", sa.Integer(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=False),
            sa.Column("recorded_by", sa.String(), nullable=True),
        )
    if not insp.has_table("ownership_review"):
        op.create_table(
            "ownership_review",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("subject_type", sa.String(16), nullable=False),
            sa.Column("subject_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("detail", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )


def _review(bind, clinic_id, kind, detail, now):
    seen = bind.execute(sa.text("SELECT COUNT(*) FROM ownership_review WHERE subject_type = 'clinic' "
                                "AND subject_id = :c AND kind = :k AND resolved_at IS NULL"),
                        {"c": clinic_id, "k": kind}).scalar()
    if not seen:
        bind.execute(sa.text("INSERT INTO ownership_review (subject_type, subject_id, kind, detail, created_at) "
                             "VALUES ('clinic', :c, :k, :d, :now)"), {"c": clinic_id, "k": kind, "d": detail, "now": now})


def _reconcile_clinics(bind, insp, now):
    if not insp.has_table("clinics"):
        return
    explicit = {}
    if insp.has_table("org_clinics"):
        for cid, oid, name, location, active in bind.execute(sa.text(
                "SELECT id, organization_id, name, location, is_active FROM org_clinics")).fetchall():
            explicit.setdefault(cid, set()).add(oid)
            exists = bind.execute(sa.text("SELECT COUNT(*) FROM clinics WHERE id = :c"), {"c": cid}).scalar()
            if not exists:
                # A clinic known only to the earlier table becomes a clinic again, under its organization.
                bind.execute(sa.text(
                    "INSERT INTO clinics (id, name, address, phone, status, org_id, created_at, updated_at) "
                    "VALUES (:c, :n, :a, '', :s, NULL, :now, :now)"),
                    {"c": cid, "n": name, "a": location or "", "s": "Active" if active in (1, True, None) else "Inactive",
                     "now": now})
        org_ids = {o for owners in explicit.values() for o in owners if o}
        have = {r[0] for r in bind.execute(sa.text("SELECT id FROM organizations"))}
        for oid in sorted(org_ids - have):
            bind.execute(sa.text("INSERT INTO organizations (id, name, status, time_zone, created_date, updated_at) "
                                 "VALUES (:id, :id, 'Active', 'UTC', :now, :now)"), {"id": oid, "now": now})

    def activity(cid):
        owners = set()
        for table in ("users", "signals"):
            if insp.has_table(table):
                owners |= {r[0] for r in bind.execute(sa.text(
                    f"SELECT DISTINCT org_id FROM {table} WHERE clinic_id = :c AND org_id IS NOT NULL"), {"c": cid})}
        owners.discard("UNASSIGNED")
        return owners

    for cid, current in bind.execute(sa.text("SELECT id, org_id FROM clinics")).fetchall():
        mapped = explicit.get(cid, set())
        seen = activity(cid)
        if current:
            if mapped and mapped != {current}:
                _review(bind, cid, "conflict", f"owner {current}; org_clinics says {sorted(mapped)}", now)
            continue
        if len(mapped) == 1:
            owner = next(iter(mapped))
            if seen and seen != {owner}:
                _review(bind, cid, "conflict", f"org_clinics says {owner}; users/signals say {sorted(seen)}", now)
                continue
            bind.execute(sa.text("UPDATE clinics SET org_id = :o, updated_at = :now WHERE id = :c"),
                         {"o": owner, "c": cid, "now": now})
        elif len(mapped) > 1:
            _review(bind, cid, "conflict", f"org_clinics names {sorted(mapped)}", now)
        elif len(seen) == 1:
            bind.execute(sa.text("UPDATE clinics SET org_id = :o, updated_at = :now WHERE id = :c"),
                         {"o": next(iter(seen)), "c": cid, "now": now})
        elif len(seen) > 1:
            _review(bind, cid, "conflict", f"users/signals name {sorted(seen)}", now)
        else:
            _review(bind, cid, "orphan", "no org_clinics mapping, users or signals name an organization", now)


def _audit_scope(bind, insp):
    if not insp.has_table("audit_logs"):
        return
    if "scope_source" not in {c["name"] for c in insp.get_columns("audit_logs")}:
        op.add_column("audit_logs", sa.Column("scope_source", sa.String(16), nullable=True))
    # Rows written by the MS-001 audit writer name their subject: their scope was recorded at the time.
    bind.execute(sa.text("UPDATE audit_logs SET scope_source = 'event' "
                         "WHERE scope_source IS NULL AND target_type IS NOT NULL"))
    # Everything older has no event-time evidence of its scope (an earlier r1 copied today's account).
    bind.execute(sa.text("UPDATE audit_logs SET org_id = NULL, clinic_id = NULL, scope_source = 'unresolved' "
                         "WHERE scope_source IS NULL"))


def _settings_unique(bind, insp):
    if not insp.has_table("settings"):
        return
    # Merge duplicates: keep the oldest row of each scope.
    for org_id, keep in bind.execute(sa.text(
            "SELECT org_id, MIN(id) FROM settings WHERE clinic_id IS NULL AND org_id IS NOT NULL "
            "GROUP BY org_id HAVING COUNT(*) > 1")).fetchall():
        bind.execute(sa.text("DELETE FROM settings WHERE org_id = :o AND clinic_id IS NULL AND id <> :k"),
                     {"o": org_id, "k": keep})
    for org_id, clinic_id, keep in bind.execute(sa.text(
            "SELECT org_id, clinic_id, MIN(id) FROM settings WHERE clinic_id IS NOT NULL "
            "GROUP BY org_id, clinic_id HAVING COUNT(*) > 1")).fetchall():
        bind.execute(sa.text("DELETE FROM settings WHERE org_id = :o AND clinic_id = :c AND id <> :k"),
                     {"o": org_id, "c": clinic_id, "k": keep})
    have = {i["name"] for i in insp.get_indexes("settings")}
    if "uq_settings_org_scope" not in have:
        op.create_index("uq_settings_org_scope", "settings", ["org_id"], unique=True,
                        sqlite_where=sa.text("clinic_id IS NULL AND org_id IS NOT NULL"),
                        postgresql_where=sa.text("clinic_id IS NULL AND org_id IS NOT NULL"))
    if "uq_settings_clinic_scope" not in have:
        op.create_index("uq_settings_clinic_scope", "settings", ["org_id", "clinic_id"], unique=True,
                        sqlite_where=sa.text("clinic_id IS NOT NULL"),
                        postgresql_where=sa.text("clinic_id IS NOT NULL"))


def _settings_review(bind, insp, now):
    """A configuration row with no organization (the pre-MS-001 global row, when r1 could not tell which
    organization it belonged to) is listed for the Platform Administrator instead of being assigned."""
    if not insp.has_table("settings"):
        return
    for (sid,) in bind.execute(sa.text("SELECT id FROM settings WHERE org_id IS NULL")).fetchall():
        seen = bind.execute(sa.text("SELECT COUNT(*) FROM ownership_review WHERE subject_type = 'settings' "
                                    "AND subject_id = :s AND resolved_at IS NULL"), {"s": str(sid)}).scalar()
        if not seen:
            bind.execute(sa.text("INSERT INTO ownership_review (subject_type, subject_id, kind, detail, created_at) "
                                 "VALUES ('settings', :s, 'orphan', :d, :now)"),
                         {"s": str(sid), "d": "configuration row with no organization; not assigned", "now": now})


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.utcnow()
    _create_tables(sa.inspect(bind))
    insp = sa.inspect(bind)
    _reconcile_clinics(bind, insp, now)
    _audit_scope(bind, insp)
    _settings_unique(bind, sa.inspect(bind))
    _settings_review(bind, sa.inspect(bind), now)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("settings"):
        have = {i["name"] for i in insp.get_indexes("settings")}
        for name in _SETTINGS_INDEXES:
            if name in have:
                op.drop_index(name, table_name="settings")
    for name in ("ownership_review", "data_imports", "connector_sync_runs", "connector_probes"):
        if insp.has_table(name):
            op.drop_table(name)
    if insp.has_table("audit_logs") and "scope_source" in {c["name"] for c in insp.get_columns("audit_logs")}:
        with op.batch_alter_table("audit_logs") as batch:
            batch.drop_column("scope_source")
