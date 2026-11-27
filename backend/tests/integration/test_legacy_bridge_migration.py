"""
Legacy Claims Bridge upgrade path (PR 07 second review, R1).

A database populated by q1 as first shipped (d40da770), and a q1 copy that already carried some of
the later columns (780144a), are both upgraded with the normal `alembic upgrade head`. Every stored
transition, record, seal, assessment and accepted task stays readable through the service; the
current pointer, revisions and task kinds are backfilled; an acceptance keeps its original user; and
the resulting schema equals a fresh installation. Each case runs in its own throwaway database on the
same server as the test database (a temporary file for SQLite, a temporary database for PostgreSQL).
"""
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from app.db.database import SQLALCHEMY_DATABASE_URL
from app.legacy_bridge import scenario
from app.legacy_bridge.assessment import digest_rows
from app.legacy_bridge.contracts import Record
from app.models import legacy_bridge as m
from app.services import legacy_bridge_service as svc

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CUTOVER = datetime(2026, 11, 1)
END = scenario.instants(CUTOVER)["end"]
TID = "LCT-MIGRATIONCASE01"


@contextmanager
def scratch_database():
    """A brand-new empty database next to the test database; dropped afterwards."""
    url = sa.engine.make_url(SQLALCHEMY_DATABASE_URL)     # the app's URL, with its driver normalized
    if url.get_backend_name() == "sqlite":
        folder = os.path.dirname(url.database) or "."
        path = os.path.join(folder, f"legacy_migration_{uuid.uuid4().hex[:8]}.db")
        yield f"sqlite:///{path.replace(os.sep, '/')}"
        if os.path.exists(path):
            os.remove(path)
        return
    name = f"legacy_migration_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def migrate(url, target, direction="upgrade"):
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        getattr(command, direction)(cfg, target)
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def migrate_to_shipped_q1(url, engine):
    """Reach q1 with the q1 tables exactly as q1 creates them. (In a process that has imported the
    models, the 000 baseline's create_all already makes the legacy tables in today's shape, which
    q1 would then skip; they are dropped first so q1 itself creates them.)"""
    migrate(url, "p7_waiting_room_evidence")
    meta = sa.MetaData()
    meta.reflect(bind=engine, only=lambda name, _m: name.startswith("legacy_"))
    meta.drop_all(bind=engine)
    migrate(url, "q1_legacy_claims_bridge")


def version(engine):
    with engine.connect() as c:
        return c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()


def populate_q1(engine, updated_shape=False):
    """Rows as the q1 code wrote them: a sealed transition with 32 records, one sealed assessment and an
    earlier unsealed placeholder, two follow-up tasks of which one was accepted. With updated_shape the
    780144a columns exist and the acceptance already has its events."""
    scope = dict(deployment="development", region="UAE", org="ORG-M", clinic="CLINIC-M", facility="CLINIC-M",
                 payee="CLINIC-M", payer="TPA-M", currency="AED")
    now = END + timedelta(days=2)
    by_role = scenario.records_by_role(CUTOVER)
    with engine.begin() as c:
        ins = lambda table, **v: c.execute(sa.text(  # noqa: E731
            f"INSERT INTO {table} ({', '.join(v)}) VALUES ({', '.join(':' + k for k in v)})"), v)
        ins("legacy_transitions", id=TID, deployment="development", region="UAE", org_id="ORG-M",
            clinic_id="CLINIC-M", facility="CLINIC-M", payee="CLINIC-M", payer="TPA-M", currency="AED",
            cutover=CUTOVER, label="Migrated run-off", origin="clinic", created_by="admin@clinic.test",
            created_at=CUTOVER, **scenario.TRANSITION)
        for n, role in enumerate(scenario.ROLES, start=1):
            conn = f"src-{role}"
            ins("legacy_source_bindings", id=n, transition_id=TID, connector=conn, role=role,
                valid_from=CUTOVER - timedelta(days=30), valid_until=CUTOVER + timedelta(days=365),
                created_by="admin@clinic.test", created_at=CUTOVER)
            page = f"LCP-M{n}"
            recs = [Record.model_validate_json(json.dumps({**r, "scope": scope, "connector": conn}))
                    for r in by_role[role]]
            ins("legacy_pages", id=page, transition_id=TID, connector=conn, resource="export", token=f"{role}-1",
                previous_token=None, digest=f"{n:064d}", record_count=len(recs), seal_count=1,
                evidence_id=f"evd_m{n}", received_at=now, received_by="source")
            for i, r in enumerate(recs):
                ins("legacy_records", transition_id=TID, page_id=page, connector=conn, kind=r.data.kind,
                    business_key=r.key, revision=1, content_digest=f"{n:02d}{i:062d}", evidence_ref=r.evidence,
                    effective_at=r.effective.replace(tzinfo=None), seen_at=r.seen.replace(tzinfo=None),
                    received_at=now, record_json=r.model_dump_json())
            ins("legacy_seals", transition_id=TID, page_id=page, connector=conn, role=role, through=END,
                unique_rows=len(recs), digest=digest_rows(recs), receipt=f"seal-{role}", received_at=now)
            ins("legacy_stream_heads", transition_id=TID, connector=conn, resource="export", head_token=f"{role}-1",
                updated_at=now)
        common = dict(transition_id=TID, policy_version="legacy-continuity-policy.v1",
                      mapping_version="legacy-continuity.v1", evidence_json="[]",
                      review_fact_json=json.dumps({"fact": {"quality_state": "RESEARCH_ONLY"}}))
        extra = {"revision": 1} if updated_shape else {}
        # An unsealed placeholder published before delivery (clock-time cutoff), then the sealed one.
        ins("legacy_assessments", id="LCA-PLACEHOLDER", as_of=now - timedelta(hours=1), status="HOLD",
            input_digest="d" * 64, record_count=0, issues_json="[]", follow_up_json="[]", summary_json="{}",
            computed_at=now - timedelta(hours=1), computed_by="admin@clinic.test", **common, **extra)
        follow = [{"code": "WORK_DENIAL", "item": "den1", "owner": "bill-team"},
                  {"code": "REVIEW_PROVIDER_ADJUSTMENT", "item": "offset-18", "owner": "finance-lead"}]
        ins("legacy_assessments", id="LCA-SEALED", as_of=END, status="SAMPLE_REVIEWABLE", input_digest="e" * 64,
            record_count=32, issues_json="[]", follow_up_json=json.dumps(follow), summary_json="{}",
            computed_at=now, computed_by="admin@clinic.test", **common, **extra)
        kind = {"kind": "follow_up"} if updated_shape else {}
        ins("legacy_work_items", id=1, transition_id=TID, code="WORK_DENIAL", item="den1", owner="bill-team",
            state="ACCEPTED", first_assessment_id="LCA-SEALED", last_assessment_id="LCA-SEALED", created_at=now,
            accepted_by="finance@clinic.test", accepted_at=now + timedelta(minutes=5), note="Appeal in preparation",
            resolved_at=None, **kind)
        ins("legacy_work_items", id=2, transition_id=TID, code="REVIEW_PROVIDER_ADJUSTMENT", item="offset-18",
            owner="finance-lead", state="OPEN", first_assessment_id="LCA-SEALED", last_assessment_id="LCA-SEALED",
            created_at=now, accepted_by=None, accepted_at=None, note=None, resolved_at=None, **kind)
        if updated_shape:
            c.execute(sa.text("UPDATE legacy_transitions SET current_assessment_id = 'LCA-PLACEHOLDER'"))
            for wid, event, frm, to, actor, at in ((1, "OPENED", None, "OPEN", "admin@clinic.test", now),
                                                   (1, "ACCEPTED", "OPEN", "ACCEPTED", "finance@clinic.test",
                                                    now + timedelta(minutes=5)),
                                                   (2, "OPENED", None, "OPEN", "admin@clinic.test", now)):
                ins("legacy_work_item_events", work_item_id=wid, transition_id=TID, event=event, from_state=frm,
                    to_state=to, actor=actor, assessment_id="LCA-SEALED", note=None, at=at)


def add_780144a_columns(engine):
    """The q1 copy shipped at 780144a: the same tables plus these columns and the event table."""
    with engine.begin() as c:
        for ddl in ("ALTER TABLE legacy_transitions ADD COLUMN ownership_json TEXT",
                    "ALTER TABLE legacy_transitions ADD COLUMN current_assessment_id VARCHAR(40)",
                    "ALTER TABLE legacy_assessments ADD COLUMN revision INTEGER NOT NULL DEFAULT 1",
                    "ALTER TABLE legacy_work_items ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'follow_up'",
                    "ALTER TABLE legacy_work_items ADD COLUMN issues_json TEXT"):
            c.execute(sa.text(ddl))
        if engine.dialect.name == "postgresql":
            c.execute(sa.text("ALTER TABLE legacy_assessments ADD CONSTRAINT uq_legacy_assessment_revision "
                              "UNIQUE (transition_id, as_of, revision)"))
        else:
            c.execute(sa.text("CREATE UNIQUE INDEX uq_legacy_assessment_revision "
                              "ON legacy_assessments (transition_id, as_of, revision)"))
        c.execute(sa.text(
            "CREATE TABLE legacy_work_item_events (id INTEGER PRIMARY KEY"
            + (" GENERATED BY DEFAULT AS IDENTITY" if engine.dialect.name == "postgresql" else "")
            + ", work_item_id INTEGER NOT NULL REFERENCES legacy_work_items(id), transition_id VARCHAR(40) NOT NULL "
            "REFERENCES legacy_transitions(id), event VARCHAR(16) NOT NULL, from_state VARCHAR(16), "
            "to_state VARCHAR(16) NOT NULL, actor VARCHAR(160) NOT NULL, assessment_id VARCHAR(40), note TEXT, "
            "at TIMESTAMP NOT NULL)"))


def schema(engine):
    insp = sa.inspect(engine)
    out = {}
    for table in sorted(n for n in insp.get_table_names() if n.startswith("legacy_")):
        uniques = {u["name"] for u in insp.get_unique_constraints(table)} | {
            i["name"] for i in insp.get_indexes(table) if i.get("unique")}
        out[table] = (sorted(c["name"] for c in insp.get_columns(table)), "uq_legacy_assessment_revision" in uniques)
    return out


def read_back(engine):
    """Read the migrated transition through the service, as the API does."""
    session = sessionmaker(bind=engine)()
    try:
        t = session.get(m.LegacyTransitionModel, TID)
        current = svc.read_assessment(session, t, "reader")
        view = svc.assessment_view(session, t, current, now=END + timedelta(days=3))
        counts = {model.__tablename__: session.query(model).filter(model.transition_id == TID).count()
                  for model in (m.LegacyRecordModel, m.LegacySealModel, m.LegacyPageModel, m.LegacySourceBindingModel,
                                m.LegacyAssessmentModel, m.LegacyWorkItemModel)}
        return t.current_assessment_id, view, counts
    finally:
        session.close()


EXPECTED_COUNTS = {"legacy_records": 32, "legacy_seals": 8, "legacy_pages": 8, "legacy_source_bindings": 8,
                   "legacy_assessments": 2, "legacy_work_items": 2}


@pytest.mark.parametrize("shape", ["q1-as-shipped", "q1-780144a"])
def test_r1_a_populated_q1_database_upgrades_and_stays_readable(shape):
    with scratch_database() as url, scratch_database() as fresh_url:
        engine = sa.create_engine(url)
        migrate_to_shipped_q1(url, engine)
        assert "lock_version" not in {c["name"] for c in sa.inspect(engine).get_columns("legacy_transitions")}
        if shape == "q1-780144a":
            add_780144a_columns(engine)
        populate_q1(engine, updated_shape=shape == "q1-780144a")

        migrate(url, "head")
        assert version(engine) == "q2_legacy_bridge_review"
        current_id, view, counts = read_back(engine)
        assert counts == EXPECTED_COUNTS
        # The sealed assessment is current; the clock-time placeholder never is.
        assert current_id == "LCA-SEALED" and view["is_current"] is True and view["revision"] == 1
        items = {w["code"]: w for w in view["work_items"]}
        accepted = items["WORK_DENIAL"]
        assert accepted["state"] == "ACCEPTED" and accepted["accepted_by"] == "finance@clinic.test"
        assert accepted["kind"] == "follow_up"
        events = [(e["event"], e["actor"]) for e in accepted["history"]]
        if shape == "q1-as-shipped":
            # Known acceptance provenance kept; the unknown earlier history is marked as reconstructed.
            assert events == [("OPENED", "migration q2 (history before event tracking)"),
                              ("ACCEPTED", "finance@clinic.test")]
            assert "earlier state changes were not tracked" in accepted["history"][0]["note"]
        else:
            assert events == [("OPENED", "admin@clinic.test"), ("ACCEPTED", "finance@clinic.test")]
        assert items["REVIEW_PROVIDER_ADJUSTMENT"]["state"] == "OPEN"

        # A fresh installation reaches the same schema.
        fresh = sa.create_engine(fresh_url)
        migrate(fresh_url, "head")
        assert schema(engine) == schema(fresh)

        # Upgrading again is a no-op; the downgrade to q1 and back keeps the rows.
        migrate(url, "head")
        migrate(url, "q1_legacy_claims_bridge", "downgrade")
        migrate(url, "head")
        current_id, view, counts = read_back(engine)
        assert counts == EXPECTED_COUNTS and current_id == "LCA-SEALED"
        engine.dispose()
        fresh.dispose()
