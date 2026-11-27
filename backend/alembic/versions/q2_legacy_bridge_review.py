"""q2_legacy_bridge_review

Legacy Claims Bridge, PR 07 review: current-assessment pointer, assessment revisions and sealed flag,
per-transition write lock, clinic ownership policy, hold investigations, reviewer assignment, the
work item event history and subscription sign-offs given on the screen. Additive; existing rows are kept and backfilled.

Two q1 shapes may exist: q1 as first shipped (d40da770), and a q1 copy that already carries some of
these columns (780144a). Every step checks what is there, so both upgrade to the same schema as a
fresh installation. History that was never recorded is marked as such, never invented.

Revision ID: q2_legacy_bridge_review
Revises: q1_legacy_claims_bridge
Create Date: 2026-11-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'q2_legacy_bridge_review'
down_revision = 'q1_legacy_claims_bridge'
branch_labels = None
depends_on = None

MIGRATION_ACTOR = "migration q2 (history before event tracking)"
UNAVAILABLE = "Recorded from the stored row by migration q2; earlier state changes were not tracked."


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _has_unique(insp, table, name):
    return (name in {c.get("name") for c in insp.get_unique_constraints(table)}
            or name in {i.get("name") for i in insp.get_indexes(table)})


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = _columns(insp, "legacy_transitions")
    if "ownership_json" not in cols:
        op.add_column("legacy_transitions", sa.Column("ownership_json", sa.Text(), nullable=True))
    if "current_assessment_id" not in cols:
        op.add_column("legacy_transitions", sa.Column("current_assessment_id", sa.String(40), nullable=True))
    if "lock_version" not in cols:
        op.add_column("legacy_transitions", sa.Column("lock_version", sa.Integer(), nullable=False,
                                                      server_default="0"))

    cols = _columns(insp, "legacy_assessments")
    new_revision = "revision" not in cols
    if new_revision:
        op.add_column("legacy_assessments", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    if "sealed" not in cols:
        op.add_column("legacy_assessments", sa.Column("sealed", sa.Boolean(), nullable=False,
                                                      server_default=sa.false()))

    cols = _columns(insp, "legacy_work_items")
    if "kind" not in cols:
        op.add_column("legacy_work_items", sa.Column("kind", sa.String(16), nullable=False,
                                                     server_default="follow_up"))
    if "issues_json" not in cols:
        op.add_column("legacy_work_items", sa.Column("issues_json", sa.Text(), nullable=True))
    if "assigned_to" not in cols:
        op.add_column("legacy_work_items", sa.Column("assigned_to", sa.String(160), nullable=True))
    for name, kind in (("outcome", sa.String(24)), ("outcome_by", sa.String(160)), ("outcome_at", sa.DateTime()),
                       ("outcome_note", sa.Text())):
        if name not in cols:
            op.add_column("legacy_work_items", sa.Column(name, kind, nullable=True))

    if not insp.has_table("legacy_work_item_events"):
        op.create_table(
            "legacy_work_item_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("work_item_id", sa.Integer(), sa.ForeignKey("legacy_work_items.id"), nullable=False),
            sa.Column("transition_id", sa.String(40), sa.ForeignKey("legacy_transitions.id"), nullable=False),
            sa.Column("event", sa.String(16), nullable=False),
            sa.Column("from_state", sa.String(16), nullable=True),
            sa.Column("to_state", sa.String(16), nullable=False),
            sa.Column("actor", sa.String(160), nullable=False),
            sa.Column("assessment_id", sa.String(40), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("at", sa.DateTime(), nullable=False),
        )
    if not insp.has_table("legacy_exit_signoffs"):
        op.create_table(
            "legacy_exit_signoffs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("inventory_id", sa.String(40), sa.ForeignKey("legacy_exit_inventories.id"), nullable=False),
            sa.Column("role", sa.String(24), nullable=False),
            sa.Column("decision_ref", sa.String(96), nullable=False),
            sa.Column("signed_by", sa.String(160), nullable=False),
            sa.Column("signed_at", sa.DateTime(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.UniqueConstraint("inventory_id", "role", name="uq_legacy_exit_signoff"),
        )
        op.create_index("ix_legacy_exit_signoffs_inventory_id", "legacy_exit_signoffs", ["inventory_id"])
    insp = sa.inspect(bind)
    for col in ("work_item_id", "transition_id"):
        name = f"ix_legacy_work_item_events_{col}"
        if name not in {i["name"] for i in insp.get_indexes("legacy_work_item_events")}:
            op.create_index(name, "legacy_work_item_events", [col])

    _backfill(bind, new_revision)

    insp = sa.inspect(bind)
    if not _has_unique(insp, "legacy_assessments", "uq_legacy_assessment_revision"):
        op.create_index("uq_legacy_assessment_revision", "legacy_assessments",
                        ["transition_id", "as_of", "revision"], unique=True)


def _backfill(bind, new_revision: bool) -> None:
    rows = bind.execute(sa.text(
        "SELECT id, transition_id, as_of FROM legacy_assessments ORDER BY transition_id, as_of, computed_at, id"
    )).fetchall()
    seals = {(t, through) for t, through in bind.execute(sa.text(
        "SELECT transition_id, through FROM legacy_seals")).fetchall()}
    # Revisions number successive publications for one cutoff, in the order they were computed.
    if new_revision:
        counter = {}
        for aid, tid, as_of in rows:
            counter[(tid, as_of)] = counter.get((tid, as_of), 0) + 1
            bind.execute(sa.text("UPDATE legacy_assessments SET revision = :r WHERE id = :id"),
                         {"r": counter[(tid, as_of)], "id": aid})
    # An assessment is sealed when a source's completeness seal is through its cutoff.
    for aid, tid, as_of in rows:
        bind.execute(sa.text("UPDATE legacy_assessments SET sealed = :s WHERE id = :id"),
                     {"s": (tid, as_of) in seals, "id": aid})
    # Current pointer: the newest sealed assessment (cutoff, then revision). A pointer to an unsealed
    # placeholder is replaced; without any sealed assessment the transition reads as a preview.
    sealed_rows = bind.execute(sa.text(
        "SELECT id, transition_id, as_of, revision FROM legacy_assessments WHERE sealed = :t"), {"t": True}).fetchall()
    best = {}
    for aid, tid, as_of, rev in sealed_rows:
        if tid not in best or (as_of, rev) > best[tid][1:]:
            best[tid] = (aid, as_of, rev)
    for tid, current in bind.execute(sa.text("SELECT id, current_assessment_id FROM legacy_transitions")).fetchall():
        pointer = current
        if current is not None:
            ok = bind.execute(sa.text("SELECT sealed FROM legacy_assessments WHERE id = :id"), {"id": current}).fetchone()
            if not ok or not ok[0]:
                pointer = None
        if pointer is None and tid in best:
            pointer = best[tid][0]
        if pointer != current:
            bind.execute(sa.text("UPDATE legacy_transitions SET current_assessment_id = :p WHERE id = :id"),
                         {"p": pointer, "id": tid})
    # Work that later evidence already closed keeps that as its outcome.
    bind.execute(sa.text("UPDATE legacy_work_items SET outcome = 'resolved_by_evidence' "
                         "WHERE state = 'RESOLVED' AND outcome IS NULL"))
    # Work items without any event: record what the stored row proves, marked as reconstructed.
    have = {w for (w,) in bind.execute(sa.text("SELECT DISTINCT work_item_id FROM legacy_work_item_events")).fetchall()}
    items = bind.execute(sa.text(
        "SELECT id, transition_id, state, created_at, first_assessment_id, last_assessment_id, accepted_by, "
        "accepted_at, note, resolved_at FROM legacy_work_items ORDER BY id")).fetchall()
    insert = sa.text(
        "INSERT INTO legacy_work_item_events (work_item_id, transition_id, event, from_state, to_state, actor, "
        "assessment_id, note, at) VALUES (:w, :t, :e, :f, :s, :a, :aid, :n, :at)")
    for (wid, tid, state, created, first, last, accepted_by, accepted_at, note, resolved_at) in items:
        if wid in have:
            continue
        bind.execute(insert, dict(w=wid, t=tid, e="OPENED", f=None, s="OPEN", a=MIGRATION_ACTOR, aid=first,
                                  n=UNAVAILABLE, at=created))
        if accepted_by:
            bind.execute(insert, dict(w=wid, t=tid, e="ACCEPTED", f="OPEN", s="ACCEPTED", a=accepted_by, aid=last,
                                      n=note, at=accepted_at or created))
        if state == "RESOLVED":
            bind.execute(insert, dict(w=wid, t=tid, e="RESOLVED", f="ACCEPTED" if accepted_by else "OPEN",
                                      s="RESOLVED", a=MIGRATION_ACTOR, aid=last, n=UNAVAILABLE,
                                      at=resolved_at or accepted_at or created))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    name = "uq_legacy_assessment_revision"
    as_constraint = name in {c.get("name") for c in insp.get_unique_constraints("legacy_assessments")}
    if not as_constraint and name in {i["name"] for i in insp.get_indexes("legacy_assessments")}:
        op.drop_index(name, table_name="legacy_assessments")
    if insp.has_table("legacy_work_item_events"):
        op.drop_table("legacy_work_item_events")
    if insp.has_table("legacy_exit_signoffs"):
        op.drop_table("legacy_exit_signoffs")
    work_item_columns = ("kind", "issues_json", "assigned_to", "outcome", "outcome_by", "outcome_at", "outcome_note")
    for table, names in (("legacy_work_items", work_item_columns),
                         ("legacy_assessments", ("revision", "sealed")),
                         ("legacy_transitions", ("ownership_json", "current_assessment_id", "lock_version"))):
        present = [n for n in names if n in _columns(insp, table)]
        if present:
            with op.batch_alter_table(table) as batch:
                if table == "legacy_assessments" and as_constraint:
                    batch.drop_constraint(name, type_="unique")
                for n in present:
                    batch.drop_column(n)
