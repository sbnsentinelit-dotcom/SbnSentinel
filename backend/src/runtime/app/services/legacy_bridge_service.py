"""
Legacy Claims Continuity Bridge service (V3 candidate).

Intake: a page from a bound source is checked (binding, role per record kind, scope, observation
time not in the future, expected predecessor) and then the page, its records, its completeness seal,
an evidence_repository custody row and the stream head are committed in ONE transaction. The same
page sent again is recognised (ALREADY_STAGED); a different page under a used token, or a page that
does not follow the current head, is refused and nothing is stored.

Assessment: the trusted context is built on the server from the transition, its bindings and the
seals received for the assessment instant; the batch is every stored record known at that instant,
with closing balances taken only from the designated snapshot of the cutoff. Publishing stores the
result (status, issues, follow-up work, evidence references, figures and the G2 review fact marked
RESEARCH_ONLY) once per distinct input, moves the current pointer when it is newer (cutoff, then
revision) and moves the work items, all in one transaction. Reading an assessment, current or
historical, has no write effects. Nothing here posts money, submits a claim or ends a subscription.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import exc, or_, update
from sqlalchemy.orm import Session

from app.legacy_bridge.assessment import Result
from app.legacy_bridge.contracts import Batch, Context
from app.legacy_bridge.identity import digest
from app.legacy_bridge.intake import BindingIn, PageIn, TransitionIn, role_for
from app.legacy_bridge.messages import message
from app.legacy_bridge.portfolio import ExitInventory, prepare_exit
from app.legacy_bridge.sentinel_adapter import build_review_fact
from app.legacy_bridge.summary import summarize
from app.legacy_bridge.work_policy import DEFAULT_OWNERS, PRIORITY, hold_tasks
from app.models.legacy_bridge import (
    LegacyAssessmentModel, LegacyExitInventoryModel, LegacyPageModel, LegacyRecordModel, LegacySealModel,
    LegacyExitSignoffModel, LegacySourceBindingModel, LegacyStreamHeadModel, LegacyTransitionModel,
    LegacyWorkItemEventModel,
    LegacyWorkItemModel,
)

# The rules, summary and evidence semantics. It is part of every input digest, so an assessment stored
# under earlier rules is never returned for the same evidence; supersede_outdated() replaces it.
# v1: d40da770 and 780144a. v2: one trace eligibility for rules and summary, validated positions,
# snapshot by ingestion cutoff, input manifest (6c3f4b5 and later).
POLICY_VERSION = "legacy-continuity-policy.v2"
MAPPING_VERSION = "legacy-continuity.v1"


class BridgeError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def bridge_enabled() -> bool:
    """LEGACY_BRIDGE_ENABLED when set; otherwise on outside PRODUCTION and off in PRODUCTION."""
    from app.core.config import settings
    flag = getattr(settings, "LEGACY_BRIDGE_ENABLED", None)
    if flag is not None:
        return bool(flag)
    return getattr(settings, "ENVIRONMENT", "DEVELOPMENT") != "PRODUCTION"


# ---------------------------------------------------------------- time helpers

def to_db(value: datetime) -> datetime:
    """Aware instant -> naive UTC for storage."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso(value: Optional[datetime]) -> Optional[str]:
    """Stored naive UTC -> ISO string with Z."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = to_db(value)
    return value.isoformat(timespec="microseconds" if value.microsecond else "seconds") + "Z"


def _utcnow() -> datetime:
    return datetime.utcnow()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


# ---------------------------------------------------------------- transitions and bindings

def scope_of(t: LegacyTransitionModel) -> dict:
    return dict(deployment=t.deployment, region=t.region, org=t.org_id, clinic=t.clinic_id,
                facility=t.facility, payee=t.payee, payer=t.payer, currency=t.currency)


def deployment_name() -> str:
    from app.core.config import settings
    return (getattr(settings, "ENVIRONMENT", "DEVELOPMENT") or "DEVELOPMENT").lower()


def create_transition(db: Session, data: TransitionIn, *, org_id: str, clinic_id: str, actor: str,
                      origin: str = "clinic") -> LegacyTransitionModel:
    from app.core.config import get_deployment_currency, settings
    region = getattr(settings, "DEPLOYMENT_REGION", "US")
    currency = data.currency or get_deployment_currency()
    t = LegacyTransitionModel(
        id=_new_id("LCT"), deployment=deployment_name(), region=region, org_id=org_id, clinic_id=clinic_id,
        facility=data.facility, payee=data.payee, payer=data.payer, currency=currency,
        cutover=to_db(data.cutover), new_route=data.new_route, old_route=data.old_route,
        fallback_route=data.fallback_route, archive_route=data.archive_route,
        max_probe_age_days=data.max_probe_age_days, label=data.label, origin=origin,
        ownership_json=json.dumps(data.hold_owners, sort_keys=True) if data.hold_owners else None,
        created_by=actor, created_at=_utcnow())
    # Validate the scope with the contract model before storing it.
    from app.legacy_bridge.contracts import Scope
    try:
        Scope.model_validate_json(json.dumps(scope_of(t)))
    except Exception as e:
        raise BridgeError(422, f"Invalid transition scope: {e}")
    db.add(t)
    try:
        db.commit()
    except exc.IntegrityError:
        db.rollback()
        raise BridgeError(409, "A transition for this clinic, facility, payee, payer and currency already exists.")
    db.refresh(t)
    return t


def add_binding(db: Session, t: LegacyTransitionModel, data: BindingIn, actor: str) -> LegacySourceBindingModel:
    if data.valid_from >= data.valid_until:
        raise BridgeError(422, "valid_from must be before valid_until")
    connector = data.connector
    if connector is None:      # bound from the screen: a server-made name, unique in the transition
        taken = {c for (c,) in db.query(LegacySourceBindingModel.connector).filter(
            LegacySourceBindingModel.transition_id == t.id).all()}
        connector = f"clinic-{data.role}"
        n = 2
        while connector in taken:
            connector, n = f"clinic-{data.role}-{n}", n + 1
    b = LegacySourceBindingModel(transition_id=t.id, connector=connector, role=data.role,
                                 valid_from=to_db(data.valid_from), valid_until=to_db(data.valid_until),
                                 created_by=actor, created_at=_utcnow())
    db.add(b)
    try:
        db.commit()
    except exc.IntegrityError:
        db.rollback()
        raise BridgeError(409, "This source is already bound to the transition.")
    db.refresh(b)
    return b


def bindings(db: Session, t: LegacyTransitionModel) -> List[LegacySourceBindingModel]:
    return (db.query(LegacySourceBindingModel).filter(LegacySourceBindingModel.transition_id == t.id)
            .order_by(LegacySourceBindingModel.id).all())


def stream_heads(db: Session, t: LegacyTransitionModel) -> List[LegacyStreamHeadModel]:
    return db.query(LegacyStreamHeadModel).filter(LegacyStreamHeadModel.transition_id == t.id).all()


# ---------------------------------------------------------------- intake

def _check_page(t: LegacyTransitionModel, b: LegacySourceBindingModel, page: PageIn, now: datetime):
    from app.legacy_bridge.contracts import Record
    if not (b.valid_from <= now < b.valid_until):
        raise BridgeError(403, "This source is not bound for the transition at this time.")
    scope = scope_of(t)
    problems = []
    full = []
    for r in page.records:
        if r.connector is not None and r.connector != b.connector:
            problems.append({"key": r.key, "reason": "record names another source"})
            continue
        if r.scope is not None and r.scope.model_dump() != scope:
            problems.append({"key": r.key, "reason": "record scope differs from the transition"})
            continue
        if role_for(r.data) != b.role:
            problems.append({"key": r.key, "reason": f"a {b.role} source cannot deliver {r.data.kind} records"})
            continue
        if to_db(r.seen) > now:
            problems.append({"key": r.key, "reason": "observation time is in the future"})
            continue
        body = r.model_dump(mode="json")
        body.update(scope=scope, connector=b.connector)
        try:
            full.append(Record.model_validate_json(json.dumps(body)))
        except Exception as e:  # e.g. observed before the event
            problems.append({"key": r.key, "reason": str(e).splitlines()[-1][:200]})
    if page.seal is not None and to_db(page.seal.through) > now:
        problems.append({"key": "seal", "reason": "completeness statement is dated in the future"})
    if problems:
        raise BridgeError(422, {"message": "The page was refused; nothing was stored.", "records": problems})
    return full


class _HeadMoved(Exception):
    """The stream head changed between reading it and advancing it."""


def _read_head(db: Session, t: LegacyTransitionModel, connector: str, resource: str):
    return db.query(LegacyStreamHeadModel).filter(
        LegacyStreamHeadModel.transition_id == t.id, LegacyStreamHeadModel.connector == connector,
        LegacyStreamHeadModel.resource == resource).first()


def stage_page(db: Session, t: LegacyTransitionModel, page: PageIn, actor: str,
               now: Optional[datetime] = None, attestation: Optional[dict] = None) -> Dict:
    """Store one page atomically. Returns {"status": "STAGED" | "ALREADY_STAGED", ...}."""
    from app.models.evidence import EvidenceModel
    now = now or _utcnow()
    b = db.query(LegacySourceBindingModel).filter(LegacySourceBindingModel.id == page.binding_id,
                                                  LegacySourceBindingModel.transition_id == t.id).first()
    if not b:
        raise BridgeError(403, "Unknown source for this transition.")
    records = _check_page(t, b, page, now)
    content = [(r, digest(r.model_dump())) for r in records]
    page_digest = digest({"resource": page.resource, "records": [d for _, d in content],
                          "seal": page.seal.model_dump() if page.seal else None})
    token = page.token or page_digest

    def existing_outcome():
        old = db.query(LegacyPageModel).filter(
            LegacyPageModel.transition_id == t.id, LegacyPageModel.connector == b.connector,
            LegacyPageModel.resource == page.resource, LegacyPageModel.token == token).first()
        if old is None:
            return None
        # The same contents under the same token are a replay, whatever predecessor the sender names now
        # (for example the same file uploaded again after later pages); different contents conflict.
        if old.digest != page_digest:
            raise BridgeError(409, "A different page was already received under this token.")
        return {"status": "ALREADY_STAGED", "page_id": old.id, "token": token,
                "records_added": 0, "seal_stored": False}

    for attempt in (1, 2):
        done = existing_outcome()
        if done:
            return done
        head = _read_head(db, t, b.connector, page.resource)
        if (head.head_token if head else None) != page.previous:
            raise BridgeError(409, {"message": "The page does not follow the stream's current head.",
                                    "expected_previous": head.head_token if head else None,
                                    "retryable": attempt == 2})
        try:
            # Advance the head first, as a compare-and-swap on the predecessor: of two concurrent pages
            # naming the same predecessor exactly one moves the head; the other moves nothing, keeps
            # nothing and is answered with a retryable conflict. A first page inserts the head, and
            # the stream's unique key lets only one concurrent first page in.
            if head:
                moved = db.execute(
                    update(LegacyStreamHeadModel)
                    .where(LegacyStreamHeadModel.id == head.id, LegacyStreamHeadModel.head_token == page.previous)
                    .values(head_token=token, updated_at=now)
                    .execution_options(synchronize_session=False)).rowcount
                if moved != 1:
                    raise _HeadMoved()
            else:
                db.add(LegacyStreamHeadModel(transition_id=t.id, connector=b.connector, resource=page.resource,
                                             head_token=token, updated_at=now))
                db.flush()
            page_id = _new_id("LCP")
            evidence_id = f"evd_{uuid.uuid4().hex}"
            db.add(EvidenceModel(
                evidence_id=evidence_id, canonical_entity="LegacyClaimsPage",
                fact_key=digest({"transition": t.id, "connector": b.connector, "resource": page.resource,
                                 "token": token, "digest": page_digest}),
                fact_value_str=json.dumps({"records": len(records), "seal": page.seal is not None}),
                source_connector=f"legacy-bridge:{b.connector}", retrieval_timestamp=now,
                evidence_type="LegacyBridgePage", version=1, clinic_id=t.clinic_id, external_record_id=token,
                resource_kind=f"legacy.{page.resource}", source_version=page_digest,
                metadata_json=json.dumps({"transition_id": t.id, "role": b.role, "previous": page.previous,
                                          "token": token, "records": len(records),
                                          "attestation": attestation,
                                          "quality_state": "RESEARCH_ONLY", "eligible_for_decision": False})))
            db.add(LegacyPageModel(id=page_id, transition_id=t.id, connector=b.connector, resource=page.resource,
                                   token=token, previous_token=page.previous, digest=page_digest,
                                   record_count=len(records), seal_count=1 if page.seal else 0,
                                   evidence_id=evidence_id, received_at=now, received_by=actor))
            db.flush()
            have = {d for (d,) in db.query(LegacyRecordModel.content_digest).filter(
                LegacyRecordModel.transition_id == t.id, LegacyRecordModel.connector == b.connector,
                LegacyRecordModel.content_digest.in_([d for _, d in content])).all()} if content else set()
            added = 0
            for r, d in content:
                if d in have:
                    continue  # the same record delivered again: stored once
                have.add(d)
                db.add(LegacyRecordModel(
                    transition_id=t.id, page_id=page_id, connector=b.connector, kind=r.data.kind,
                    business_key=r.key, revision=r.revision, content_digest=d, evidence_ref=r.evidence,
                    effective_at=to_db(r.effective), seen_at=to_db(r.seen), received_at=now,
                    record_json=r.model_dump_json()))
                added += 1
            seal_stored = False
            if page.seal is not None:
                s = page.seal
                # One completeness statement per source, instant and population (07.T4): the same rows and
                # digest stated again, under any receipt, file name or uploader, is the same statement and
                # is not stored twice. A different population for the same instant replaces the earlier
                # statement, which stays on record as superseded.
                earlier = (db.query(LegacySealModel).filter(
                    LegacySealModel.transition_id == t.id, LegacySealModel.connector == b.connector,
                    LegacySealModel.through == to_db(s.through), LegacySealModel.superseded_by_id.is_(None))
                    .order_by(LegacySealModel.id).all())
                if not any(e.digest == s.digest and e.unique_rows == s.unique_rows for e in earlier):
                    new_seal = LegacySealModel(transition_id=t.id, page_id=page_id, connector=b.connector, role=b.role,
                                               through=to_db(s.through), unique_rows=s.unique_rows, digest=s.digest,
                                               receipt=s.receipt, received_at=now)
                    db.add(new_seal)
                    db.flush()
                    for e in earlier:
                        e.superseded_by_id = new_seal.id
                    seal_stored = True
            db.commit()
            return {"status": "STAGED", "page_id": page_id, "token": token, "records_added": added,
                    "seal_stored": seal_stored}
        except (exc.IntegrityError, _HeadMoved):
            # A concurrent delivery committed first: nothing of this attempt is kept. Re-check once:
            # the same page is then ALREADY_STAGED, any other page gets the new head as a 409.
            db.rollback()
    raise BridgeError(409, {"message": "Concurrent delivery for this stream; retry with the current head.",
                            "retryable": True})


# ---------------------------------------------------------------- assessment

def latest_seal_instant(db: Session, t: LegacyTransitionModel) -> Optional[datetime]:
    row = (db.query(LegacySealModel.through).filter(LegacySealModel.transition_id == t.id)
           .order_by(LegacySealModel.through.desc()).first())
    return row[0] if row else None


def _context(t: LegacyTransitionModel, as_of: datetime, snapshot: datetime, seals, binding_rows) -> Context:
    scope = scope_of(t)
    return Context.model_validate_json(json.dumps({
        "scope": scope, "cutover": iso(t.cutover), "as_of": iso(as_of), "snapshot_at": iso(snapshot),
        "new_route": t.new_route, "old_route": t.old_route, "fallback_route": t.fallback_route,
        "archive_route": t.archive_route, "max_probe_age_days": t.max_probe_age_days,
        "bindings": [dict(connector=b.connector, role=b.role, scope=scope, valid_from=iso(b.valid_from),
                          valid_until=iso(b.valid_until)) for b in binding_rows],
        "seals": [dict(role=s.role, through=iso(s.through), unique_rows=s.unique_rows, digest=s.digest,
                       receipt=s.receipt) for s in seals],
    }))


def _batch(stored) -> Batch:
    return Batch.model_validate_json(
        '{"version":"legacy-continuity.v1","records":[' + ",".join(r.record_json for r in stored) + "]}")


def _effective_seals(db: Session, t: LegacyTransitionModel, as_of: datetime, now: datetime):
    """Completeness statements through as_of that had arrived by `now`; for one source and instant the
    latest statement replaces earlier ones (an identical statement is never stored twice)."""
    rows = (db.query(LegacySealModel).filter(LegacySealModel.transition_id == t.id, LegacySealModel.through == as_of,
                                             LegacySealModel.received_at <= now)
            .order_by(LegacySealModel.id).all())
    latest = {}
    for seal in rows:
        latest[seal.connector] = seal
    return sorted(latest.values(), key=lambda x: x.id)


def _inputs(db: Session, t: LegacyTransitionModel, as_of: Optional[datetime], now: datetime) -> dict:
    """Everything an assessment at as_of may use, limited to what had been received by `now` (the
    ingestion cutoff): the closing snapshot, the seals, the bindings and the records."""
    as_of = to_db(as_of) if as_of else (latest_seal_instant(db, t) or now)
    if as_of > now:
        raise BridgeError(422, "The assessment time cannot be in the future.")
    if as_of < t.cutover:
        raise BridgeError(422, "The assessment time is before the transition cutover.")
    # The designated closing snapshot for this cutoff: the latest closing date known at as_of among the
    # records received by `now`. Only its rows enter the batch, so successive snapshots of an unchanged
    # balance are never added together, and a record delivered later never changes this assessment.
    closing = (db.query(LegacyRecordModel.effective_at).filter(
        LegacyRecordModel.transition_id == t.id, LegacyRecordModel.kind == "closing",
        LegacyRecordModel.effective_at <= as_of, LegacyRecordModel.seen_at <= as_of,
        LegacyRecordModel.received_at <= now, LegacyRecordModel.effective_at >= t.cutover)
        .order_by(LegacyRecordModel.effective_at.desc()).first())
    snapshot = closing[0] if closing else t.cutover
    seals = _effective_seals(db, t, as_of, now)
    binding_rows = bindings(db, t)
    stored = (db.query(LegacyRecordModel).filter(
        LegacyRecordModel.transition_id == t.id, LegacyRecordModel.received_at <= now,
        or_(LegacyRecordModel.kind != "closing", LegacyRecordModel.effective_at == snapshot))
        .order_by(LegacyRecordModel.id).all())
    return {"ctx": _context(t, as_of, snapshot, seals, binding_rows), "batch": _batch(stored), "stored": stored,
            "seals": seals, "bindings": binding_rows, "snapshot": snapshot, "as_of": as_of}


def build_inputs(db: Session, t: LegacyTransitionModel, as_of: Optional[datetime] = None,
                 now: Optional[datetime] = None, with_rows: bool = False) -> Tuple[Context, Batch]:
    got = _inputs(db, t, as_of, now or _utcnow())
    if with_rows:
        return got["ctx"], got["batch"], got["stored"]   # batch.records[i] is stored[i]
    return got["ctx"], got["batch"]


class HistoryUnavailable(Exception):
    """A published assessment whose exact inputs or rules cannot be reconstructed."""


def inputs_of(db: Session, t: LegacyTransitionModel, a: LegacyAssessmentModel):
    """The exact inputs of a published assessment, from its manifest: (ctx, batch, stored records).
    Raises HistoryUnavailable for an assessment stored before manifests or under other rules."""
    if a.policy_version != POLICY_VERSION:
        raise HistoryUnavailable(f"This assessment was computed under {a.policy_version}; its detail cannot be "
                                 f"rebuilt under {POLICY_VERSION}. Its stored figures are shown as recorded.")
    if not a.manifest_json or a.snapshot_at is None:
        raise HistoryUnavailable("This assessment was stored without its input manifest; its detail is unavailable.")
    m = json.loads(a.manifest_json)
    stored = (db.query(LegacyRecordModel).filter(LegacyRecordModel.id.in_(m["records"]))
              .order_by(LegacyRecordModel.id).all()) if m["records"] else []
    seals = (db.query(LegacySealModel).filter(LegacySealModel.id.in_(m["seals"]))
             .order_by(LegacySealModel.id).all()) if m["seals"] else []
    binding_rows = (db.query(LegacySourceBindingModel).filter(LegacySourceBindingModel.id.in_(m["bindings"]))
                    .order_by(LegacySourceBindingModel.id).all()) if m["bindings"] else []
    return _context(t, a.as_of, a.snapshot_at, seals, binding_rows), _batch(stored), stored


def _issue_rows(result: Result):
    return [{"code": i.code, "item": i.item, "message": message(i.code)} for i in result.issues]


def _evaluate(db: Session, t: LegacyTransitionModel, actor: str, as_of: Optional[datetime], now: datetime,
              inputs_until: Optional[datetime] = None):
    """The assessment of the stored evidence as of an instant, as an unsaved row with its input manifest.
    inputs_until limits the inputs to what had been received by then (default: now). No write effects."""
    got = _inputs(db, t, as_of, inputs_until or now)
    ctx, batch = got["ctx"], got["batch"]
    observation, fact, result = build_review_fact(ctx, batch, sample_id=t.id, archive_ref=f"assessment-{t.id}")
    input_digest = digest([observation.source_version, list(result.evidence), POLICY_VERSION])
    return LegacyAssessmentModel(
        id=None, transition_id=t.id, as_of=to_db(ctx.as_of), revision=None, status=result.status,
        input_digest=input_digest, policy_version=POLICY_VERSION, mapping_version=MAPPING_VERSION,
        record_count=len(batch.records), issues_json=json.dumps(_issue_rows(result)),
        follow_up_json=json.dumps([{"code": f.code, "item": f.item, "owner": f.owner} for f in result.follow_up]),
        evidence_json=json.dumps(list(result.evidence)),
        summary_json=json.dumps(summarize(ctx, batch, result)),
        review_fact_json=json.dumps({"observation": observation.model_dump(mode="json"),
                                     "fact": fact.model_dump(mode="json")}),
        sealed=bool(ctx.seals), computed_at=now, computed_by=actor, snapshot_at=got["snapshot"],
        manifest_json=json.dumps({"records": [r.id for r in got["stored"]], "seals": [x.id for x in got["seals"]],
                                  "bindings": [b.id for b in got["bindings"]],
                                  "inputs_until": iso(inputs_until or now)}))


def _stored(db: Session, t: LegacyTransitionModel, input_digest: str) -> Optional[LegacyAssessmentModel]:
    return db.query(LegacyAssessmentModel).filter(LegacyAssessmentModel.transition_id == t.id,
                                                  LegacyAssessmentModel.input_digest == input_digest).first()


def current_assessment(db: Session, t: LegacyTransitionModel) -> Optional[LegacyAssessmentModel]:
    return db.get(LegacyAssessmentModel, t.current_assessment_id) if t.current_assessment_id else None


def read_assessment(db: Session, t: LegacyTransitionModel, actor: str, as_of: Optional[datetime] = None,
                    now: Optional[datetime] = None) -> LegacyAssessmentModel:
    """Read only. Without as_of: the current published assessment. With as_of (or before anything was
    published): the stored assessment for exactly that evidence, else an unsaved preview (id None).
    Reading never publishes, never changes work items and never changes the current pointer."""
    now = now or _utcnow()
    if as_of is None:
        current = current_assessment(db, t)
        if current is not None:
            return current
    preview = _evaluate(db, t, actor, as_of, now)
    return _stored(db, t, preview.input_digest) or preview


def _newer(a: LegacyAssessmentModel, b: Optional[LegacyAssessmentModel]) -> bool:
    """Only a sealed assessment can become current; it replaces an unsealed placeholder (left by an
    earlier version) and otherwise orders by evidence cutoff, then revision."""
    if not a.sealed:
        return False
    return b is None or not b.sealed or (a.as_of, a.revision) > (b.as_of, b.revision)


def _lock(db: Session, t: LegacyTransitionModel) -> None:
    """First statement of every publication and acceptance: a write to the transition row. It holds
    the row lock (PostgreSQL) or the write lock (SQLite) until commit, so concurrent writers of one
    transition's assessments and work items run one after the other, each on freshly read state."""
    db.execute(update(LegacyTransitionModel).where(LegacyTransitionModel.id == t.id)
               .values(lock_version=LegacyTransitionModel.lock_version + 1)
               .execution_options(synchronize_session=False))
    db.expire_all()


def publish_assessment(db: Session, t: LegacyTransitionModel, actor: str,
                       now: Optional[datetime] = None) -> LegacyAssessmentModel:
    """Assess the evidence as of its latest completeness statement and publish it. Under the
    transition's lock and in ONE transaction: the immutable assessment row (once per distinct input;
    revision numbers new inputs for the same cutoff), the current pointer when it is newer, and the
    work items it drives. Before any source has sealed its evidence the result is only a preview
    (nothing stored, nothing current). Publishing the same evidence again changes nothing; two
    identical publications at once return the same assessment."""
    now = now or _utcnow()
    for attempt in (1, 2):
        try:
            _lock(db, t)
            candidate = _evaluate(db, t, actor, None, now)
            if not candidate.sealed:
                db.rollback()
                return candidate
            row = _stored(db, t, candidate.input_digest)
            if row is None:
                last = (db.query(LegacyAssessmentModel.revision).filter(
                    LegacyAssessmentModel.transition_id == t.id, LegacyAssessmentModel.as_of == candidate.as_of)
                    .order_by(LegacyAssessmentModel.revision.desc()).first())
                candidate.id, candidate.revision = _new_id("LCA"), (last[0] + 1 if last else 1)
                db.add(candidate)
                db.flush()
                row = candidate
            current = current_assessment(db, t)
            if row.id != t.current_assessment_id and _newer(row, current):
                if current is not None and row.supersedes_id is None:
                    row.supersedes_id = current.id
                t.current_assessment_id = row.id
                sync_work_items(db, t, row, actor, now)
            db.commit()
            return row
        except exc.IntegrityError:
            # Only reachable if the lock is bypassed: roll back everything and evaluate again.
            db.rollback()
    raise BridgeError(409, "Another assessment was published at the same time; try again.")


RULES_UPDATE_ACTOR = "rules update"


def supersede_outdated(db: Session, now: Optional[datetime] = None) -> List[str]:
    """Replace every current assessment computed under earlier rules (07.T1): re-evaluate its own cutoff
    from its original inputs (the records and seals received by the time it was computed) under the
    current rules, store it as the next revision with a link to the replaced one, move the current
    pointer and the work in one transaction. The replaced assessment, its result and the decisions
    already taken stay as they were. Running it again changes nothing. Returns the new assessment IDs."""
    now = now or _utcnow()
    done = []
    for (tid,) in db.query(LegacyTransitionModel.id).filter(LegacyTransitionModel.current_assessment_id.isnot(None)).all():
        t = db.get(LegacyTransitionModel, tid)
        _lock(db, t)
        old = current_assessment(db, t)
        if old is None or old.policy_version == POLICY_VERSION:
            db.rollback()
            continue
        candidate = _evaluate(db, t, RULES_UPDATE_ACTOR, old.as_of, now, inputs_until=max(old.computed_at, old.as_of))
        if not candidate.sealed:
            db.rollback()
            continue
        row = _stored(db, t, candidate.input_digest)
        if row is None:
            last = (db.query(LegacyAssessmentModel.revision).filter(
                LegacyAssessmentModel.transition_id == t.id, LegacyAssessmentModel.as_of == candidate.as_of)
                .order_by(LegacyAssessmentModel.revision.desc()).first())
            candidate.id, candidate.revision, candidate.supersedes_id = _new_id("LCA"), (last[0] + 1 if last else 1), old.id
            db.add(candidate)
            db.flush()
            row = candidate
        t.current_assessment_id = row.id
        sync_work_items(db, t, row, RULES_UPDATE_ACTOR, now)
        db.commit()
        done.append(row.id)
    return done


def _event(db: Session, w: LegacyWorkItemModel, event: str, from_state: Optional[str], actor: str,
           now: datetime, assessment_id: Optional[str] = None, note: Optional[str] = None) -> None:
    db.add(LegacyWorkItemEventModel(work_item_id=w.id, transition_id=w.transition_id, event=event,
                                    from_state=from_state, to_state=w.state, actor=actor,
                                    assessment_id=assessment_id, note=note, at=now))


def _ownership(t: LegacyTransitionModel) -> dict:
    return json.loads(t.ownership_json) if t.ownership_json else {}


def sync_work_items(db: Session, t: LegacyTransitionModel, a: LegacyAssessmentModel, actor: str,
                    now: datetime) -> None:
    """Called only when `a` becomes the current published assessment, inside its transaction (no
    commit here). Wanted work = the assessment's follow-up work plus one investigation per hold area.
    New work opens; work no longer wanted resolves (outcome resolved_by_evidence); resolved work wanted
    again reopens. CLOSED work keeps the outcome a reviewer recorded. Every change is an event, so an
    acceptance stays in the history after a later resolution and reopening."""
    wanted = {}
    for f in json.loads(a.follow_up_json):
        wanted[(f["code"], f["item"])] = ("follow_up", f.get("owner"), None)
    for code, area, owner, links in hold_tasks(json.loads(a.issues_json), _ownership(t)):
        wanted[(code, area)] = ("hold", owner, links)
    rows = {(w.code, w.item): w for w in db.query(LegacyWorkItemModel)
            .filter(LegacyWorkItemModel.transition_id == t.id).all()}
    for key, (kind, owner, links) in wanted.items():
        w = rows.get(key)
        issues_json = json.dumps(links) if links is not None else None
        if w is None:
            w = LegacyWorkItemModel(transition_id=t.id, kind=kind, code=key[0], item=key[1], owner=owner,
                                    issues_json=issues_json, state="OPEN", first_assessment_id=a.id,
                                    last_assessment_id=a.id, created_at=now)
            db.add(w)
            db.flush()
            _event(db, w, "OPENED", None, actor, now, a.id)
            continue
        w.owner, w.issues_json, w.last_assessment_id = owner, issues_json, a.id
        if w.state == "CLOSED" and w.outcome == "risk_accepted":
            # An accepted risk covers only the discrepancies it named (07.T5): a new one opens a new
            # decision cycle; the earlier decision stays in the history.
            accepted = {tuple(x) for x in json.loads(w.outcome_scope_json or "{}").get("discrepancies", [])}
            new = sorted(set(_scope_of(w)) - accepted)
            if new:
                w.state, w.accepted_by, w.accepted_at, w.note = "OPEN", None, None, None
                w.outcome, w.outcome_by, w.outcome_at, w.outcome_note = None, None, None, None
                w.outcome_scope_json = None
                _event(db, w, "REOPENED", "CLOSED", actor, now, a.id,
                       "New discrepancy after an accepted risk: " + "; ".join(f"{c} {i}" for c, i in new))
            continue
        if w.state == "RESOLVED":
            w.state, w.resolved_at, w.accepted_by, w.accepted_at, w.note = "OPEN", None, None, None, None
            w.outcome, w.outcome_by, w.outcome_at, w.outcome_note = None, None, None, None
            _event(db, w, "REOPENED", "RESOLVED", actor, now, a.id)
    for key, w in rows.items():
        if key not in wanted and w.state not in ("RESOLVED", "CLOSED"):
            previous = w.state
            w.state, w.resolved_at, w.last_assessment_id = "RESOLVED", now, a.id
            w.outcome, w.outcome_by, w.outcome_at = "resolved_by_evidence", actor, now
            _event(db, w, "RESOLVED", previous, actor, now, a.id)


def work_items(db: Session, t: LegacyTransitionModel) -> List[LegacyWorkItemModel]:
    return (db.query(LegacyWorkItemModel).filter(LegacyWorkItemModel.transition_id == t.id)
            .order_by(LegacyWorkItemModel.state, LegacyWorkItemModel.id).all())


def accept_work_item(db: Session, w: LegacyWorkItemModel, actor: str, note: Optional[str],
                     now: Optional[datetime] = None) -> LegacyWorkItemModel:
    """Claim an OPEN item: under the transition's lock, an atomic update from OPEN. Of two reviewers
    accepting at once exactly one succeeds; the other gets a 409 naming the recorded acceptance, and
    only the successful claim writes an event and an audit row. A resolution is never overwritten."""
    now = now or _utcnow()
    item_id, transition_id = w.id, w.transition_id
    _lock(db, db.get(LegacyTransitionModel, transition_id))
    claimed = db.execute(
        update(LegacyWorkItemModel)
        .where(LegacyWorkItemModel.id == item_id, LegacyWorkItemModel.state == "OPEN")
        .values(state="ACCEPTED", accepted_by=actor, accepted_at=now, note=note)
        .execution_options(synchronize_session=False)).rowcount
    w = db.get(LegacyWorkItemModel, item_id)
    if claimed != 1:
        state, by, at = w.state, w.accepted_by, w.accepted_at
        db.rollback()
        if state == "RESOLVED":
            raise BridgeError(409, {"message": "This item is already resolved by later evidence.", "state": state})
        if state == "CLOSED":
            raise BridgeError(409, {"message": "This item is already closed with an outcome.", "state": state})
        raise BridgeError(409, {"message": "This item was already accepted.", "state": state,
                                "accepted_by": by, "accepted_at": iso(at)})
    _event(db, w, "ACCEPTED", "OPEN", actor, now, w.last_assessment_id, note)
    from app.services.data_audit_engine import data_audit_engine
    data_audit_engine._log_internal(db, user_system=actor, action="LEGACY_WORK_ITEM_ACCEPTED",
                                    module="Legacy Claims Bridge", correlation_id=f"{w.transition_id}:{w.id}",
                                    should_commit=False)
    db.commit()
    db.refresh(w)
    return w


def _audit(db: Session, actor: str, action: str, w: LegacyWorkItemModel) -> None:
    from app.services.data_audit_engine import data_audit_engine
    data_audit_engine._log_internal(db, user_system=actor, action=action, module="Legacy Claims Bridge",
                                    correlation_id=f"{w.transition_id}:{w.id}", should_commit=False)


def _locked_item(db: Session, w: LegacyWorkItemModel) -> LegacyWorkItemModel:
    """Take the transition lock, then reread the item: every work action sees the latest state."""
    item_id = w.id
    _lock(db, db.get(LegacyTransitionModel, w.transition_id))
    return db.get(LegacyWorkItemModel, item_id)


def assignees(db: Session, t: LegacyTransitionModel) -> List[dict]:
    """Who a task can be assigned to: the clinic's responsible teams (ownership policy, defaults and the
    owners named in its evidence) and the clinic's active users who can work legacy claims."""
    from app.models.user import User, UserRole
    teams = set(DEFAULT_OWNERS.values()) | set(_ownership(t).values()) | {
        o for (o,) in db.query(LegacyWorkItemModel.owner).filter(LegacyWorkItemModel.transition_id == t.id).all() if o}
    writers = {UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
               UserRole.CLINIC_MANAGER.value}
    users = (db.query(User).filter(User.clinic_id == t.clinic_id, User.is_active.is_(True), User.role.in_(writers))
             .order_by(User.email).all())
    return ([{"value": name, "label": name, "kind": "team"} for name in sorted(teams)]
            + [{"value": u.email, "label": f"{u.full_name or u.email} ({u.email})", "kind": "user"}
               for u in users if u.email])


def assign_work_item(db: Session, w: LegacyWorkItemModel, assignee: str, actor: str, note: Optional[str] = None,
                     now: Optional[datetime] = None) -> LegacyWorkItemModel:
    now = now or _utcnow()
    w = _locked_item(db, w)
    t = db.get(LegacyTransitionModel, w.transition_id)
    if assignee not in {a["value"] for a in assignees(db, t)}:
        db.rollback()
        raise BridgeError(422, "Assign the task to one of the clinic's teams or users.")
    if w.state in ("RESOLVED", "CLOSED"):
        state = w.state
        db.rollback()
        raise BridgeError(409, {"message": "This item is already closed.", "state": state})
    previous = w.assigned_to
    w.assigned_to = assignee
    _event(db, w, "ASSIGNED", w.state, actor, now, w.last_assessment_id,
           f"Assigned to {assignee}" + (f" (was {previous})" if previous else "") + (f": {note}" if note else ""))
    _audit(db, actor, "LEGACY_WORK_ITEM_ASSIGNED", w)
    db.commit()
    db.refresh(w)
    return w


def note_work_item(db: Session, w: LegacyWorkItemModel, note: str, actor: str,
                   now: Optional[datetime] = None) -> LegacyWorkItemModel:
    now = now or _utcnow()
    w = _locked_item(db, w)
    _event(db, w, "NOTE", w.state, actor, now, w.last_assessment_id, note)
    _audit(db, actor, "LEGACY_WORK_ITEM_NOTE", w)
    db.commit()
    db.refresh(w)
    return w


def release_work_item(db: Session, w: LegacyWorkItemModel, actor: str, may_release_others: bool,
                      note: Optional[str] = None, now: Optional[datetime] = None) -> LegacyWorkItemModel:
    """Give an accepted task back: ACCEPTED -> OPEN, by the reviewer who accepted it or an administrator.
    The acceptance stays in the history."""
    now = now or _utcnow()
    w = _locked_item(db, w)
    if w.state != "ACCEPTED":
        state = w.state
        db.rollback()
        raise BridgeError(409, {"message": "Only an accepted item can be released.", "state": state})
    if w.accepted_by != actor and not may_release_others:
        db.rollback()
        raise BridgeError(403, "Only the reviewer who accepted this item, or an administrator, can release it.")
    w.state, w.accepted_by, w.accepted_at = "OPEN", None, None
    _event(db, w, "RELEASED", "ACCEPTED", actor, now, w.last_assessment_id, note)
    _audit(db, actor, "LEGACY_WORK_ITEM_RELEASED", w)
    db.commit()
    db.refresh(w)
    return w


OUTCOMES = ("collected", "written_off", "risk_accepted")


def allowed_outcomes(w: LegacyWorkItemModel) -> List[str]:
    """Collection and write-off close a denial only after the payer decided (financial closure stays
    separate from the payer's decision); any open work can be closed by an accepted risk."""
    if w.state in ("RESOLVED", "CLOSED"):
        return []
    return list(OUTCOMES) if w.code == "VERIFY_FINANCIAL_OUTCOME" else ["risk_accepted"]


def _money(minor: int, currency: str) -> str:
    return f"{currency} {minor / 100:,.2f}"


def _outcome_proof(db: Session, t: LegacyTransitionModel, w: LegacyWorkItemModel, outcome: str) -> dict:
    """The proof for a collection or write-off of a decided denial, from the reconciliation rules applied
    to the current assessment's exact inputs: proven movements on the denied claim line dated at or
    after the payer's decision that earlier outcomes have not used, their applied amount and the
    line's remaining payer balance (None when that position is unverified)."""
    from app.legacy_bridge.assessment import Issue, movement_proofs, positions
    from app.services.legacy_bridge_evidence import _Evidence
    a = current_assessment(db, t)
    if a is None:
        raise BridgeError(409, "There is no published assessment to prove the outcome from.")
    try:
        ev = _Evidence(db, t, a)
    except HistoryUnavailable as e:
        raise BridgeError(409, f"{e} Reassess first.")
    denial = ev.data["denial"].get(w.item)
    if denial is None:
        raise BridgeError(409, "The denial is not in the current assessment.")
    decided = [r for r in ev.data["appeal"].values() if r.data.denial == w.item and r.data.stage == "decided"]
    if not decided:
        raise BridgeError(409, "The payer has not decided yet; a decision comes before the financial outcome.")
    since = min(r.effective for r in decided)
    kind = "collection" if outcome == "collected" else "write_off"
    issues = {Issue(i["code"], i["item"]) for i in json.loads(a.issues_json)}
    proofs = movement_proofs(ev.data, issues)
    used = {p["refs"]["movement"] for p in (json.loads(w.outcome_proof_json) if w.outcome_proof_json else [])}
    relevant = [(k, p) for k, p in sorted(proofs.items())
                if p["kind"] == kind and ev.data["movement"][k].data.line == denial.data.line
                and ev.data["movement"][k].effective >= since and k not in used]
    proven = [dict(p, refs=p["refs"]) for _k, p in relevant if p["proven"]]
    if not proven:
        what = "payer cash posted to this claim line" if kind == "collection" else "an approved write-off on this claim line"
        reasons = "; ".join(f"{k}: {p['reason']}" for k, p in relevant if not p["proven"])
        raise BridgeError(422, {"message": f"No proven {what} after the payer's decision.",
                                "unproven": [{"movement": k, "reason": p["reason"]} for k, p in relevant if not p["proven"]],
                                "detail": reasons or "no movement after the decision"})
    valid, _disputed = positions(ev.data["closing"], ev.ctx.snapshot_at)
    remaining = valid.get((denial.data.line, "payer_ar"))
    return {"assessment_id": a.id, "proofs": proven, "amount": sum(p["amount"] for p in proven), "remaining": remaining}


def _scope_of(w: LegacyWorkItemModel) -> list:
    """The discrepancies a risk decision covers: the hold reasons linked to the task, or the task itself."""
    links = json.loads(w.issues_json) if w.issues_json else [{"code": w.code, "item": w.item}]
    return sorted({(i["code"], i["item"]) for i in links})


def record_outcome(db: Session, w: LegacyWorkItemModel, outcome: str, actor: str, is_admin: bool,
                   note: Optional[str] = None, now: Optional[datetime] = None) -> LegacyWorkItemModel:
    """Record an outcome (07.T2, 07.T5). A collection or write-off applies only the amount the
    reconciliation rules prove; it closes the task when the claim line's payer balance is zero, and
    otherwise is recorded as a partial outcome with the task left open. An accepted risk is a named
    administrator's decision with a reason, bound to the discrepancies it covers; it changes no finding.
    The assessment ID, proof records and applied amount are stored with the outcome in one transaction."""
    now = now or _utcnow()
    w = _locked_item(db, w)
    t = db.get(LegacyTransitionModel, w.transition_id)
    try:
        if outcome not in OUTCOMES:
            raise BridgeError(422, "Unknown outcome.")
        if w.state in ("RESOLVED", "CLOSED"):
            raise BridgeError(409, {"message": "This item is already closed.", "state": w.state, "outcome": w.outcome})
        if outcome not in allowed_outcomes(w):
            raise BridgeError(422, "A collection or write-off closes a denial only after the payer's decision.")
        if outcome == "risk_accepted":
            if not is_admin:
                raise BridgeError(403, "Only an administrator can accept a risk.")
            if not note:
                raise BridgeError(422, "Give the reason for accepting the risk.")
            proof = None
        else:
            proof = _outcome_proof(db, t, w, outcome)
    except BridgeError:
        db.rollback()
        raise
    previous = w.state
    if proof is None:
        w.state, w.outcome, w.outcome_by, w.outcome_at = "CLOSED", outcome, actor, now
        w.outcome_assessment_id = w.last_assessment_id
        w.outcome_scope_json = json.dumps({"assessment_id": w.last_assessment_id, "discrepancies": _scope_of(w)})
        w.outcome_note = note
        _event(db, w, "OUTCOME", previous, actor, now, w.last_assessment_id, f"Risk accepted: {note}")
    else:
        earlier = json.loads(w.outcome_proof_json) if w.outcome_proof_json else []
        w.outcome_proof_json = json.dumps(earlier + proof["proofs"])
        w.outcome_amount = (w.outcome_amount or 0) + proof["amount"]
        w.outcome_assessment_id = proof["assessment_id"]
        records = "; ".join(f"{p['refs']['movement']} {_money(p['amount'], t.currency)}" for p in proof["proofs"])
        label = "Collected" if outcome == "collected" else "Written off"
        if proof["remaining"] == 0:
            w.state, w.outcome, w.outcome_by, w.outcome_at = "CLOSED", outcome, actor, now
            w.outcome_note = "; ".join(x for x in (note, f"Proof: {records} (assessment {proof['assessment_id']})") if x)
            _event(db, w, "OUTCOME", previous, actor, now, proof["assessment_id"],
                   f"{label} {_money(w.outcome_amount, t.currency)} in total; this delivery {records}")
        else:
            left = "unverified" if proof["remaining"] is None else _money(proof["remaining"], t.currency)
            _event(db, w, "PARTIAL", previous, actor, now, proof["assessment_id"],
                   f"{label} {_money(proof['amount'], t.currency)} ({records}); the line still owes {left}"
                   + (f"; {note}" if note else ""))
    _audit(db, actor, "LEGACY_WORK_ITEM_OUTCOME", w)
    db.commit()
    db.refresh(w)
    return w


def work_item_view(db: Session, w: LegacyWorkItemModel) -> dict:
    events = (db.query(LegacyWorkItemEventModel).filter(LegacyWorkItemEventModel.work_item_id == w.id)
              .order_by(LegacyWorkItemEventModel.at, LegacyWorkItemEventModel.id).all())
    links = json.loads(w.issues_json) if w.issues_json else []
    return {"id": w.id, "kind": w.kind, "code": w.code, "item": w.item, "owner": w.owner,
            "assigned_to": w.assigned_to, "state": w.state, "priority": "high" if w.code in PRIORITY else "normal",
            "message": message(w.code),
            "issues": [{**i, "message": message(i["code"])} for i in links],
            "first_assessment_id": w.first_assessment_id,
            "last_assessment_id": w.last_assessment_id, "created_at": iso(w.created_at),
            "accepted_by": w.accepted_by, "accepted_at": iso(w.accepted_at), "note": w.note,
            "resolved_at": iso(w.resolved_at),
            "outcome": w.outcome, "outcome_by": w.outcome_by, "outcome_at": iso(w.outcome_at),
            "outcome_note": w.outcome_note, "allowed_outcomes": allowed_outcomes(w),
            "outcome_amount": w.outcome_amount, "outcome_assessment_id": w.outcome_assessment_id,
            "outcome_proof": json.loads(w.outcome_proof_json) if w.outcome_proof_json else [],
            "outcome_scope": json.loads(w.outcome_scope_json) if w.outcome_scope_json else None,
            "history": [{"event": e.event, "from_state": e.from_state, "to_state": e.to_state, "actor": e.actor,
                         "assessment_id": e.assessment_id, "note": e.note, "at": iso(e.at)} for e in events]}


def set_hold_owners(db: Session, t: LegacyTransitionModel, hold_owners: Dict[str, str], actor: str) -> None:
    """The clinic's ownership policy for hold investigations; the next publication routes by it."""
    t.ownership_json = json.dumps(dict(sorted(hold_owners.items()))) if hold_owners else None
    from app.services.data_audit_engine import data_audit_engine
    data_audit_engine._log_internal(db, user_system=actor, action="LEGACY_OWNERSHIP_POLICY_SET",
                                    module="Legacy Claims Bridge", correlation_id=t.id, should_commit=False)
    db.commit()


def transition_view(t: LegacyTransitionModel, latest: Optional[LegacyAssessmentModel] = None) -> dict:
    return {"id": t.id, "label": t.label, "scope": scope_of(t), "cutover": iso(t.cutover),
            "routes": {"new": t.new_route, "old": t.old_route, "fallback": t.fallback_route,
                       "archive": t.archive_route},
            "max_probe_age_days": t.max_probe_age_days, "origin": t.origin, "created_at": iso(t.created_at),
            "current_assessment_id": t.current_assessment_id,
            "hold_owners": {**DEFAULT_OWNERS, **_ownership(t)},
            "latest_status": latest.status if latest else None,
            "latest_as_of": iso(latest.as_of) if latest else None}


def assessment_view(db: Session, t: LegacyTransitionModel, a: LegacyAssessmentModel,
                    now: Optional[datetime] = None) -> dict:
    now = now or _utcnow()
    last_received = (db.query(LegacyPageModel.received_at).filter(LegacyPageModel.transition_id == t.id)
                     .order_by(LegacyPageModel.received_at.desc()).first())
    fact = json.loads(a.review_fact_json)["fact"]
    is_current = a.id is not None and a.id == t.current_assessment_id
    return {
        "assessment_id": a.id, "transition": transition_view(t, current_assessment(db, t)), "as_of": iso(a.as_of),
        "revision": a.revision, "published": a.id is not None, "is_current": is_current,
        "supersedes_id": a.supersedes_id, "rules_current": a.policy_version == POLICY_VERSION,
        "computed_at": iso(a.computed_at), "status": a.status,
        "policy_version": a.policy_version, "mapping_version": a.mapping_version,
        "review_quality": fact.get("quality_state"), "record_count": a.record_count,
        "source_age_hours": round((now - a.as_of).total_seconds() / 3600, 1),
        "last_received_at": iso(last_received[0]) if last_received else None,
        "issues": json.loads(a.issues_json),
        # Work follows the current published assessment only; an earlier or unsaved one has none.
        "work_items": sorted((work_item_view(db, w) for w in work_items(db, t)),
                             key=lambda v: (v["state"] in ("RESOLVED", "CLOSED"), v["priority"] != "high", v["id"])) if is_current else [],
        "evidence_count": len(json.loads(a.evidence_json)),
        "evidence_ids": json.loads(a.evidence_json),
        "summary": json.loads(a.summary_json),
        "authority": "Review only. Sentinel does not post money, submit claims or end a subscription.",
    }


def assessment_history(db: Session, t: LegacyTransitionModel, limit: int = 20) -> List[dict]:
    rows = (db.query(LegacyAssessmentModel).filter(LegacyAssessmentModel.transition_id == t.id)
            .order_by(LegacyAssessmentModel.as_of.desc(), LegacyAssessmentModel.revision.desc()).limit(limit).all())
    return [{"assessment_id": a.id, "as_of": iso(a.as_of), "revision": a.revision, "supersedes_id": a.supersedes_id,
             "policy_version": a.policy_version,
             "is_current": a.id == t.current_assessment_id, "computed_at": iso(a.computed_at), "status": a.status,
             "issues": len(json.loads(a.issues_json)), "records": a.record_count, "computed_by": a.computed_by}
            for a in rows]


# ---------------------------------------------------------------- subscription exit review

def store_exit_inventory(db: Session, raw: bytes, *, org_id: str, clinic_id: str, actor: str,
                         now: Optional[datetime] = None) -> LegacyExitInventoryModel:
    now = now or _utcnow()
    try:
        inv = ExitInventory.model_validate_json(raw)
    except Exception as e:
        raise BridgeError(422, f"Invalid subscription inventory: {str(e).splitlines()[0]}")
    if to_db(inv.as_of) > now:
        raise BridgeError(422, "The inventory is dated in the future.")
    for s in inv.required_scopes:
        if s.clinic != clinic_id:
            raise BridgeError(403, "The inventory names a scope outside your clinic.")
    d = digest(inv.model_dump())
    old = db.query(LegacyExitInventoryModel).filter(LegacyExitInventoryModel.clinic_id == clinic_id,
                                                    LegacyExitInventoryModel.subscription == inv.subscription,
                                                    LegacyExitInventoryModel.digest == d).first()
    if old:
        return old
    row = LegacyExitInventoryModel(id=_new_id("LCX"), org_id=org_id, clinic_id=clinic_id,
                                   subscription=inv.subscription, as_of=to_db(inv.as_of), digest=d,
                                   inventory_json=inv.model_dump_json(), received_at=now, received_by=actor)
    db.add(row)
    try:
        db.commit()
    except exc.IntegrityError:   # the same inventory delivered twice at once: keep the stored one
        db.rollback()
        return db.query(LegacyExitInventoryModel).filter(
            LegacyExitInventoryModel.clinic_id == clinic_id, LegacyExitInventoryModel.subscription == inv.subscription,
            LegacyExitInventoryModel.digest == d).one()
    db.refresh(row)
    return row


SIGNOFF_ROLES = ("finance", "revenue_cycle", "records", "contract_owner")


def exit_signoffs(db: Session, row: LegacyExitInventoryModel) -> List[LegacyExitSignoffModel]:
    return (db.query(LegacyExitSignoffModel).filter(LegacyExitSignoffModel.inventory_id == row.id)
            .order_by(LegacyExitSignoffModel.signed_at, LegacyExitSignoffModel.id).all())


def sign_exit(db: Session, row: LegacyExitInventoryModel, role: str, actor: str, note: Optional[str] = None,
              now: Optional[datetime] = None) -> LegacyExitSignoffModel:
    """One named sign-off for a role. It requests nothing outside Sentinel; the review only reports
    whether every required sign-off is present."""
    if role not in SIGNOFF_ROLES:
        raise BridgeError(422, "Unknown sign-off role.")
    inv = ExitInventory.model_validate_json(row.inventory_json)
    if role in inv.signoffs or any(s.role == role for s in exit_signoffs(db, row)):
        raise BridgeError(409, "This role has already signed off this inventory.")
    s = LegacyExitSignoffModel(inventory_id=row.id, role=role,
                               decision_ref=f"signoff-{role.replace('_', '-')}-{uuid.uuid4().hex[:12]}",
                               signed_by=actor, signed_at=now or _utcnow(), note=note)
    from app.services.data_audit_engine import data_audit_engine
    try:
        # The unique (inventory, role) key decides between two reviewers signing at once; the audit row is
        # written only for the sign-off that was stored.
        db.add(s)
        db.flush()
        data_audit_engine._log_internal(db, user_system=actor, action="LEGACY_EXIT_SIGNOFF",
                                        module="Legacy Claims Bridge", correlation_id=f"{row.id}:{role}",
                                        should_commit=False)
        db.commit()
    except exc.IntegrityError:
        db.rollback()
        raise BridgeError(409, "This role has already signed off this inventory.")
    db.refresh(s)
    return s


def exit_review(db: Session, row: LegacyExitInventoryModel, now: Optional[datetime] = None) -> dict:
    inv = ExitInventory.model_validate_json(row.inventory_json)
    screen = exit_signoffs(db, row)
    signed = {s.role: s for s in screen}
    if screen:   # the inventory's own sign-offs plus those given on the screen
        inv = inv.model_copy(update={"signoffs": {**inv.signoffs, **{s.role: s.decision_ref for s in screen}}})
    packets, scopes = [], []
    for s in inv.required_scopes:
        sd = s.model_dump()
        t = db.query(LegacyTransitionModel).filter_by(
            deployment=sd["deployment"], org_id=sd["org"], clinic_id=sd["clinic"], facility=sd["facility"],
            payee=sd["payee"], payer=sd["payer"], currency=sd["currency"]).first()
        entry = {"scope": sd, "transition_id": t.id if t else None, "label": t.label if t else None,
                 "status": None}
        if t:
            try:
                ctx, batch = build_inputs(db, t, inv.as_of, now)
                packets.append((ctx, batch))
                from app.legacy_bridge.assessment import assess
                entry["status"] = assess(ctx, batch).status
            except BridgeError as e:
                entry["status"] = "HOLD"
                entry["reason"] = e.detail
        scopes.append(entry)
    status, issues = prepare_exit(inv, packets)
    return {
        "inventory_id": row.id, "subscription": inv.subscription, "as_of": iso(row.as_of),
        "received_at": iso(row.received_at), "status": status,
        "issues": [{"code": i.code, "item": i.item, "message": message(i.code)} for i in issues],
        "scopes": scopes,
        "dependencies": [d.model_dump(mode="json") for d in inv.dependencies],
        "signoffs": sorted(inv.signoffs),
        "signoff_details": [{"role": r, "decision_ref": inv.signoffs[r],
                             "signed_by": signed[r].signed_by if r in signed else None,
                             "signed_at": iso(signed[r].signed_at) if r in signed else None,
                             "given_on_screen": r in signed} for r in sorted(inv.signoffs)],
        "missing_signoffs": [r for r in SIGNOFF_ROLES if r not in inv.signoffs],
        "savings": {"currency": inv.currency, "avoided": inv.avoided_minor, "incremental": inv.incremental_minor,
                    "net": inv.avoided_minor - inv.incremental_minor},
        "authority": "A positive review requests human sign-off; it never cancels a license.",
    }


def exit_inventories(db: Session, clinic_ids: Optional[List[str]],
                     transition: Optional[LegacyTransitionModel] = None) -> List[LegacyExitInventoryModel]:
    """Inventories in scope; with a transition, only those whose required scopes include its scope."""
    q = db.query(LegacyExitInventoryModel)
    if clinic_ids is not None:
        q = q.filter(LegacyExitInventoryModel.clinic_id.in_(clinic_ids))
    rows = q.order_by(LegacyExitInventoryModel.received_at.desc()).all()
    if transition is None:
        return rows
    scope = scope_of(transition)
    return [r for r in rows if r.clinic_id == transition.clinic_id
            and any(s == scope for s in json.loads(r.inventory_json).get("required_scopes", []))]


# ---------------------------------------------------------------- synthetic sources (demo mode)

SYNTHETIC_ACTOR = "synthetic legacy-claims sources"


def _clinic_org(db: Session, clinic_id: str) -> str:
    from app.models.user import User
    row = db.query(User.org_id).filter(User.clinic_id == clinic_id, User.org_id.isnot(None)).first()
    return row[0] if row and row[0] else clinic_id


def synthetic_step_minutes() -> int:
    from app.core.config import settings
    value = getattr(settings, "LEGACY_SYNTHETIC_STEP_MINUTES", None)
    return int(value) if value else 30


def deliver_synthetic(db: Session, clinic_id: str, now: Optional[datetime] = None) -> Optional[str]:
    """Synthetic mode only. The eight scenario sources deliver through stage_page (the same path as the
    API) and every delivery is published, exactly as real sources and a reviewer would:

    - delivery 1: the auditor's scenario, sealed through its cutoff, plus the subscription inventory;
    - deliveries 2..: one run-off step every LEGACY_SYNTHETIC_STEP_MINUTES (default 30) after the
      transition was opened (scenario.STEPS), each source re-stating its population with a fresh seal
      and the control source a new closing snapshot.

    Only steps whose time has come are delivered, each received and published at its own time, so the
    history fills in order. Idempotent: delivered pages come back ALREADY_STAGED and are skipped."""
    from app.core.config import get_deployment_currency
    from app.legacy_bridge import scenario
    from app.legacy_bridge.assessment import digest_rows
    from app.legacy_bridge.contracts import Record
    from app.legacy_bridge.intake import IncomingSeal
    now = now or _utcnow()
    t = db.query(LegacyTransitionModel).filter(LegacyTransitionModel.clinic_id == clinic_id,
                                               LegacyTransitionModel.origin == "synthetic").first()
    if t is None:
        from app.services.revenue_overview import clinic_offset
        offset = clinic_offset()
        midnight = (now + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
        cutover = midnight - timedelta(days=5)
        data = TransitionIn.model_validate_json(json.dumps({
            "facility": clinic_id, "payee": clinic_id, "payer": "TPA-SYNTHETIC-01",
            "currency": get_deployment_currency(), "cutover": iso(cutover),
            "label": "Synthetic legacy billing run-off", **scenario.TRANSITION}))
        t = create_transition(db, data, org_id=_clinic_org(db, clinic_id), clinic_id=clinic_id,
                              actor=SYNTHETIC_ACTOR, origin="synthetic")
        for role in scenario.ROLES:
            add_binding(db, t, BindingIn.model_validate_json(json.dumps({
                "connector": f"synthetic-{role}", "role": role, "valid_from": iso(cutover - timedelta(days=30)),
                "valid_until": iso(cutover + timedelta(days=365))})), SYNTHETIC_ACTOR)
    scope = scope_of(t)
    tid, cutover, created_at, org_id = t.id, t.cutover, t.created_at, t.org_id
    end = scenario.instants(cutover)["end"]
    sources = {b.role: (b.id, b.connector) for b in bindings(db, t) if b.connector.startswith("synthetic-")}
    staged = {tok for (tok,) in db.query(LegacyPageModel.token).filter(
        LegacyPageModel.transition_id == tid, LegacyPageModel.connector == "synthetic-control").all()}

    def deliver(n: int, at: datetime, new_by_role: dict, received: datetime) -> bool:
        """Delivery n of every source at instant `at`; False when it was delivered before. Delivery 1
        is the scenario on the `export` stream; later deliveries use the sources' own `feed` stream, so
        a file a reviewer uploads to the same source never blocks them. Each seal states the source's
        whole population at `at` (what it holds, including such uploads, plus this delivery), the same
        rule as a completeness statement made with an upload."""
        from app.services.legacy_bridge_uploads import _population

        def feed_head(connector):
            head = _read_head(db, db.get(LegacyTransitionModel, tid), connector, "feed")
            return head.head_token if head else None
        if f"synthetic-control-{n}" in staged:
            return False
        for role, (binding_id, connector) in sources.items():
            new = new_by_role.get(role, [])
            records = [Record.model_validate_json(json.dumps({**r, "scope": scope, "connector": connector})) for r in new]
            if n == 1:
                sealed = records
            else:
                sealed = _population(db, db.get(LegacyTransitionModel, tid),
                                     db.get(LegacySourceBindingModel, binding_id), records, at)
            seal = IncomingSeal(through=at.replace(tzinfo=timezone.utc), unique_rows=len(sealed),
                                digest=digest_rows(sealed), receipt=f"seal-{role}" + (f"-{n}" if n > 1 else ""))
            page = PageIn.model_validate_json(json.dumps({
                "binding_id": binding_id, "resource": "export" if n == 1 else "feed", "token": f"synthetic-{role}-{n}",
                "previous": None if n == 1 else feed_head(connector),
                "records": new, "seal": seal.model_dump(mode="json")}))
            try:
                stage_page(db, db.get(LegacyTransitionModel, tid), page, SYNTHETIC_ACTOR, received)
            except BridgeError as e:
                db.rollback()
                if e.status != 409:
                    raise
        return True

    if deliver(1, end, scenario.records_by_role(cutover), now):
        inventory = scenario.exit_inventory(scope, cutover, f"legacy-billing-{clinic_id.lower()}")
        store_exit_inventory(db, json.dumps(inventory).encode(), org_id=org_id, clinic_id=clinic_id,
                             actor=SYNTHETIC_ACTOR, now=now)
        publish_assessment(db, db.get(LegacyTransitionModel, tid), SYNTHETIC_ACTOR, now=now)
    anchor = max(end, created_at)
    step = timedelta(minutes=synthetic_step_minutes())
    for k in range(1, len(scenario.STEPS) + 1):
        at = (anchor + k * step).replace(microsecond=0)
        if at > now:
            break
        records = scenario.step_records(cutover, k, at)
        records["control"] = records.get("control", []) + scenario.closing_snapshot(k, at)
        if deliver(k + 1, at, records, at):
            publish_assessment(db, db.get(LegacyTransitionModel, tid), SYNTHETIC_ACTOR, now=at)
    return tid
