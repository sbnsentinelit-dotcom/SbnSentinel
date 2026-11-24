"""p7_waiting_room_evidence

Waiting-room evidence on visit events: when Sentinel learned an event, the source team that recorded
it, handoff identity and destination, whether the service needs payer approval, and supersession of
corrected events (never deleted).

Revision ID: p7_waiting_room_evidence
Revises: p6_review_roster
Create Date: 2026-11-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'p7_waiting_room_evidence'
down_revision = 'p6_review_roster'
branch_labels = None
depends_on = None

_COLUMNS = (
    ("known_at", sa.DateTime()),
    ("source_role", sa.String(32)),
    ("handoff_id", sa.String(64)),
    ("destination", sa.String(64)),
    ("payer_required", sa.Boolean()),
    ("superseded_at", sa.DateTime()),
    ("superseded_by", sa.String(160)),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("visit_events"):
        return
    have = {c["name"] for c in insp.get_columns("visit_events")}
    for name, kind in _COLUMNS:
        if name not in have:
            op.add_column("visit_events", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("visit_events"):
        return
    have = {c["name"] for c in insp.get_columns("visit_events")}
    with op.batch_alter_table("visit_events") as batch:
        for name, _ in reversed(_COLUMNS):
            if name in have:
                batch.drop_column(name)
