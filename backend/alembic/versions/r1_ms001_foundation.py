"""r1_ms001_foundation

MS-001 founding V1: organization ownership of clinics, configuration and audit rows; organization and
clinic lifecycle columns; the six approved roles (earlier names renamed); server-side sessions;
per-user notification read state; profile, terms and one-time link columns.

Data: every organization id already used by users or signals gets an organizations row (named by its
id until an administrator renames it). A configuration row without an owner goes to the only
organization when there is exactly one. Clinic ownership and historical audit scope are reconciled
from event-time evidence by r2_ms001_ownership.

Revision ID: r1_ms001_foundation
Revises: p7_waiting_room_evidence
Create Date: 2026-11-27 18:00:00.000000

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = 'r1_ms001_foundation'
down_revision = 'p7_waiting_room_evidence'
branch_labels = None
depends_on = None

_COLUMNS = {
    "users": (("phone", sa.String()), ("job_title", sa.String()), ("updated_at", sa.DateTime(timezone=True)),
              ("password_changed_at", sa.DateTime(timezone=True)), ("terms_accepted_at", sa.DateTime(timezone=True)),
              ("terms_version", sa.String()), ("invited_by", sa.String())),
    "organizations": (("updated_at", sa.DateTime()), ("suspended_at", sa.DateTime()), ("suspended_reason", sa.String())),
    "clinics": (("org_id", sa.String()), ("created_at", sa.DateTime()), ("updated_at", sa.DateTime()),
                ("deactivated_at", sa.DateTime())),
    "settings": (("org_id", sa.String()), ("clinic_id", sa.String())),
    "audit_logs": (("org_id", sa.String()), ("clinic_id", sa.String()), ("target_type", sa.String()),
                   ("target_id", sa.String()), ("detail", sa.String())),
    "otps": (("token_hash", sa.String()), ("expires_at", sa.DateTime()), ("used_at", sa.DateTime())),
}
_INDEXES = (("ix_clinics_org_id", "clinics", "org_id"), ("ix_settings_org_id", "settings", "org_id"),
            ("ix_settings_clinic_id", "settings", "clinic_id"), ("ix_audit_logs_org_id", "audit_logs", "org_id"),
            ("ix_audit_logs_clinic_id", "audit_logs", "clinic_id"), ("ix_otps_token_hash", "otps", "token_hash"))
_ROLE_RENAMES = (("Clinic Manager", "Clinic Administrator"), ("Front Desk", "Staff User"))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, cols in _COLUMNS.items():
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, kind in cols:
            if name not in have:
                op.add_column(table, sa.Column(name, kind, nullable=True))
    insp = sa.inspect(bind)
    for name, table, col in _INDEXES:
        if insp.has_table(table) and name not in {i["name"] for i in insp.get_indexes(table)}:
            op.create_index(name, table, [col])

    if not insp.has_table("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("end_reason", sa.String(32), nullable=True),
            sa.Column("ip_address", sa.String(64), nullable=True),
            sa.Column("user_agent", sa.String(255), nullable=True),
        )
    if not insp.has_table("notification_reads"):
        op.create_table(
            "notification_reads",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False, index=True),
            sa.Column("signal_id", sa.String(), nullable=False, index=True),
            sa.Column("read_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "signal_id", name="uq_notification_read"),
        )

    if insp.has_table("users"):
        for old, new in _ROLE_RENAMES:
            bind.execute(sa.text("UPDATE users SET role = :new WHERE role = :old"), {"old": old, "new": new})
    _backfill_ownership(bind, sa.inspect(bind))


def _backfill_ownership(bind, insp) -> None:
    if not insp.has_table("organizations"):
        return
    now = datetime.utcnow()
    have_orgs = {r[0] for r in bind.execute(sa.text("SELECT id FROM organizations"))}
    used = set()
    if insp.has_table("users"):
        used |= {r[0] for r in bind.execute(sa.text("SELECT DISTINCT org_id FROM users WHERE org_id IS NOT NULL"))}
    if insp.has_table("signals"):
        used |= {r[0] for r in bind.execute(sa.text("SELECT DISTINCT org_id FROM signals WHERE org_id IS NOT NULL"))}
    for org_id in sorted(o for o in used if o and o != "UNASSIGNED" and o not in have_orgs):
        bind.execute(sa.text("INSERT INTO organizations (id, name, status, time_zone, created_date, updated_at) "
                             "VALUES (:id, :name, 'Active', 'UTC', :now, :now)"),
                     {"id": org_id, "name": org_id, "now": now})

    # Clinic ownership is reconciled by r2_ms001_ownership (explicit org_clinics mappings first).

    orgs = [r[0] for r in bind.execute(sa.text("SELECT id FROM organizations"))]
    if insp.has_table("settings") and len(orgs) == 1:
        owned = bind.execute(sa.text("SELECT COUNT(*) FROM settings WHERE org_id = :o AND clinic_id IS NULL"),
                             {"o": orgs[0]}).scalar()
        if not owned:
            first = bind.execute(sa.text("SELECT MIN(id) FROM settings WHERE org_id IS NULL")).scalar()
            if first is not None:
                bind.execute(sa.text("UPDATE settings SET org_id = :o WHERE id = :i"), {"o": orgs[0], "i": first})

    # Historical audit rows are not given today's membership as their scope (see r2_ms001_ownership).


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for name in ("notification_reads", "user_sessions"):
        if insp.has_table(name):
            op.drop_table(name)
    if insp.has_table("users"):
        for old, new in _ROLE_RENAMES:
            bind.execute(sa.text("UPDATE users SET role = :old WHERE role = :new"), {"old": old, "new": new})
    for name, table, _ in _INDEXES:
        if insp.has_table(table) and name in {i["name"] for i in insp.get_indexes(table)}:
            op.drop_index(name, table_name=table)
    for table, cols in _COLUMNS.items():
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        drop = [name for name, _ in cols if name in have]
        if drop:
            with op.batch_alter_table(table) as batch:
                for name in drop:
                    batch.drop_column(name)
