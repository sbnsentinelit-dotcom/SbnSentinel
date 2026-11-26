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
from app.legacy_bridge.work_policy import hold_tasks
from app.models.legacy_bridge import (
    LegacyAssessmentModel, LegacyExitInventoryModel, LegacyPageModel, LegacyRecordModel, LegacySealModel,
    LegacySourceBindingModel, LegacyStreamHeadModel, LegacyTransitionModel, LegacyWorkItemEventModel,
    LegacyWorkItemModel,
)

POLICY_VERSION = "legacy-continuity-policy.v1"
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
    b = LegacySourceBindingModel(transition_id=t.id, connector=data.connector, role=data.role,
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
               now: Optional[datetime] = None) -> Dict:
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
        if (old.previous_token, old.digest) != (page.previous, page_digest):
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
                dup = db.query(LegacySealModel.id).filter(
                    LegacySealModel.transition_id == t.id, LegacySealModel.connector == b.connector,
                    LegacySealModel.through == to_db(s.through), LegacySealModel.receipt == s.receipt).first()
                if not dup:
                    db.add(LegacySealModel(transition_id=t.id, page_id=page_id, connector=b.connector, role=b.role,
                                           through=to_db(s.through), unique_rows=s.unique_rows, digest=s.digest,
                                           receipt=s.receipt, received_at=now))
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


def build_inputs(db: Session, t: LegacyTransitionModel, as_of: Optional[datetime] = None,
                 now: Optional[datetime] = None) -> Tuple[Context, Batch]:
    now = now or _utcnow()
    as_of = to_db(as_of) if as_of else (latest_seal_instant(db, t) or now)
    if as_of > now:
        raise BridgeError(422, "The assessment time cannot be in the future.")
    if as_of < t.cutover:
        raise BridgeError(422, "The assessment time is before the transition cutover.")
    # The designated closing snapshot for this cutoff: the latest closing date known at as_of. Only its
    # rows enter the batch, so successive snapshots of an unchanged balance are never added together,
    # and reading an earlier cutoff reproduces the population it had then.
    closing = (db.query(LegacyRecordModel.effective_at).filter(
        LegacyRecordModel.transition_id == t.id, LegacyRecordModel.kind == "closing",
        LegacyRecordModel.effective_at <= as_of, LegacyRecordModel.seen_at <= as_of,
        LegacyRecordModel.effective_at >= t.cutover)
        .order_by(LegacyRecordModel.effective_at.desc()).first())
    snapshot = closing[0] if closing else t.cutover
    scope = scope_of(t)
    seals = db.query(LegacySealModel).filter(LegacySealModel.transition_id == t.id,
                                             LegacySealModel.through == as_of).order_by(LegacySealModel.id).all()
    ctx = Context.model_validate_json(json.dumps({
        "scope": scope, "cutover": iso(t.cutover), "as_of": iso(as_of), "snapshot_at": iso(snapshot),
        "new_route": t.new_route, "old_route": t.old_route, "fallback_route": t.fallback_route,
        "archive_route": t.archive_route, "max_probe_age_days": t.max_probe_age_days,
        "bindings": [dict(connector=b.connector, role=b.role, scope=scope, valid_from=iso(b.valid_from),
                          valid_until=iso(b.valid_until)) for b in bindings(db, t)],
        "seals": [dict(role=s.role, through=iso(s.through), unique_rows=s.unique_rows, digest=s.digest,
                       receipt=s.receipt) for s in seals],
    }))
    rows = [r for (r,) in db.query(LegacyRecordModel.record_json).filter(
        LegacyRecordModel.transition_id == t.id, LegacyRecordModel.received_at <= now,
        or_(LegacyRecordModel.kind != "closing", LegacyRecordModel.effective_at == snapshot))
        .order_by(LegacyRecordModel.id).all()]
    batch = Batch.model_validate_json('{"version":"legacy-continuity.v1","records":[' + ",".join(rows) + "]}")
    return ctx, batch


def _issue_rows(result: Result):
    return [{"code": i.code, "item": i.item, "message": message(i.code)} for i in result.issues]


def _evaluate(db: Session, t: LegacyTransitionModel, actor: str, as_of: Optional[datetime], now: datetime):
    """The assessment of the stored evidence as of an instant, as an unsaved row. No write effects."""
    ctx, batch = build_inputs(db, t, as_of, now)
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
        computed_at=now, computed_by=actor)


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
    return b is None or (a.as_of, a.revision) > (b.as_of, b.revision)


def publish_assessment(db: Session, t: LegacyTransitionModel, actor: str,
                       now: Optional[datetime] = None) -> LegacyAssessmentModel:
    """Assess the evidence as of its latest completeness statement and publish it. In ONE transaction:
    the immutable assessment row (once per distinct input; revision numbers new inputs for the same
    cutoff), the current pointer when it is newer (evidence cutoff, then revision), and the work
    items it drives. Publishing the same evidence again changes nothing."""
    now = now or _utcnow()
    for attempt in (1, 2):
        candidate = _evaluate(db, t, actor, None, now)
        row = _stored(db, t, candidate.input_digest)
        if row is None:
            last = (db.query(LegacyAssessmentModel.revision).filter(
                LegacyAssessmentModel.transition_id == t.id, LegacyAssessmentModel.as_of == candidate.as_of)
                .order_by(LegacyAssessmentModel.revision.desc()).first())
            candidate.id, candidate.revision = _new_id("LCA"), (last[0] + 1 if last else 1)
            db.add(candidate)
            row = candidate
        current = current_assessment(db, t)
        if row.id != t.current_assessment_id and _newer(row, current):
            t.current_assessment_id = row.id
            sync_work_items(db, t, row, actor, now)
        try:
            db.commit()
            return row
        except exc.IntegrityError:  # a concurrent publication stored this input or revision first
            db.rollback()
            if attempt == 2:
                raise BridgeError(409, "Another assessment was published at the same time; try again.")
    raise BridgeError(409, "Another assessment was published at the same time; try again.")


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
    New work opens; work no longer wanted resolves; resolved work wanted again reopens. Every change
    is an event, so an acceptance stays in the history after a later resolution and reopening."""
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
        if w.state == "RESOLVED":
            w.state, w.resolved_at, w.accepted_by, w.accepted_at, w.note = "OPEN", None, None, None, None
            _event(db, w, "REOPENED", "RESOLVED", actor, now, a.id)
    for key, w in rows.items():
        if key not in wanted and w.state != "RESOLVED":
            previous = w.state
            w.state, w.resolved_at, w.last_assessment_id = "RESOLVED", now, a.id
            _event(db, w, "RESOLVED", previous, actor, now, a.id)


def work_items(db: Session, t: LegacyTransitionModel) -> List[LegacyWorkItemModel]:
    return (db.query(LegacyWorkItemModel).filter(LegacyWorkItemModel.transition_id == t.id)
            .order_by(LegacyWorkItemModel.state, LegacyWorkItemModel.id).all())


def accept_work_item(db: Session, w: LegacyWorkItemModel, actor: str, note: Optional[str],
                     now: Optional[datetime] = None) -> LegacyWorkItemModel:
    if w.state == "RESOLVED":
        raise BridgeError(409, "This item is already resolved by later evidence.")
    if w.state == "ACCEPTED":
        raise BridgeError(409, "This item was already accepted.")
    now = now or _utcnow()
    w.state, w.accepted_by, w.accepted_at, w.note = "ACCEPTED", actor, now, note
    _event(db, w, "ACCEPTED", "OPEN", actor, now, w.last_assessment_id, note)
    from app.services.data_audit_engine import data_audit_engine
    data_audit_engine._log_internal(db, user_system=actor, action="LEGACY_WORK_ITEM_ACCEPTED",
                                    module="Legacy Claims Bridge", correlation_id=f"{w.transition_id}:{w.id}",
                                    should_commit=False)
    db.commit()
    db.refresh(w)
    return w


def work_item_view(db: Session, w: LegacyWorkItemModel) -> dict:
    events = (db.query(LegacyWorkItemEventModel).filter(LegacyWorkItemEventModel.work_item_id == w.id)
              .order_by(LegacyWorkItemEventModel.at, LegacyWorkItemEventModel.id).all())
    links = json.loads(w.issues_json) if w.issues_json else []
    return {"id": w.id, "kind": w.kind, "code": w.code, "item": w.item, "owner": w.owner, "state": w.state,
            "message": message(w.code),
            "issues": [{**i, "message": message(i["code"])} for i in links],
            "first_assessment_id": w.first_assessment_id,
            "last_assessment_id": w.last_assessment_id, "created_at": iso(w.created_at),
            "accepted_by": w.accepted_by, "accepted_at": iso(w.accepted_at), "note": w.note,
            "resolved_at": iso(w.resolved_at),
            "history": [{"event": e.event, "from_state": e.from_state, "to_state": e.to_state, "actor": e.actor,
                         "assessment_id": e.assessment_id, "note": e.note, "at": iso(e.at)} for e in events]}


def transition_view(t: LegacyTransitionModel, latest: Optional[LegacyAssessmentModel] = None) -> dict:
    return {"id": t.id, "label": t.label, "scope": scope_of(t), "cutover": iso(t.cutover),
            "routes": {"new": t.new_route, "old": t.old_route, "fallback": t.fallback_route,
                       "archive": t.archive_route},
            "max_probe_age_days": t.max_probe_age_days, "origin": t.origin, "created_at": iso(t.created_at),
            "current_assessment_id": t.current_assessment_id,
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
        "computed_at": iso(a.computed_at), "status": a.status,
        "policy_version": a.policy_version, "mapping_version": a.mapping_version,
        "review_quality": fact.get("quality_state"), "record_count": a.record_count,
        "source_age_hours": round((now - a.as_of).total_seconds() / 3600, 1),
        "last_received_at": iso(last_received[0]) if last_received else None,
        "issues": json.loads(a.issues_json),
        # Work follows the current published assessment only; an earlier or unsaved one has none.
        "work_items": [work_item_view(db, w) for w in work_items(db, t)] if is_current else [],
        "evidence_count": len(json.loads(a.evidence_json)),
        "summary": json.loads(a.summary_json),
        "authority": "Review only. Sentinel does not post money, submit claims or end a subscription.",
    }


def assessment_history(db: Session, t: LegacyTransitionModel, limit: int = 20) -> List[dict]:
    rows = (db.query(LegacyAssessmentModel).filter(LegacyAssessmentModel.transition_id == t.id)
            .order_by(LegacyAssessmentModel.as_of.desc(), LegacyAssessmentModel.revision.desc()).limit(limit).all())
    return [{"assessment_id": a.id, "as_of": iso(a.as_of), "revision": a.revision,
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
    db.commit()
    db.refresh(row)
    return row


def exit_review(db: Session, row: LegacyExitInventoryModel, now: Optional[datetime] = None) -> dict:
    inv = ExitInventory.model_validate_json(row.inventory_json)
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


def deliver_synthetic(db: Session, clinic_id: str, now: Optional[datetime] = None) -> Optional[str]:
    """Synthetic mode only: the eight scenario sources deliver their pages through stage_page (the same
    path as the API), each with its own completeness seal, and the contract owner delivers the
    subscription inventory. Idempotent: on restart every page is ALREADY_STAGED."""
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
    end = scenario.instants(t.cutover)["end"]
    by_role = scenario.records_by_role(t.cutover)
    for b in bindings(db, t):
        if not b.connector.startswith("synthetic-"):
            continue
        rows = by_role.get(b.role, [])
        sealed = [Record.model_validate_json(json.dumps({**r, "scope": scope, "connector": b.connector}))
                  for r in rows]
        seal = IncomingSeal(through=end.replace(tzinfo=timezone.utc), unique_rows=len(sealed),
                            digest=digest_rows(sealed), receipt=f"seal-{b.role}")
        page = PageIn.model_validate_json(json.dumps({
            "binding_id": b.id, "resource": "export", "token": f"synthetic-{b.role}-1",
            "records": rows, "seal": seal.model_dump(mode="json")}))
        try:
            stage_page(db, t, page, SYNTHETIC_ACTOR, now)
        except BridgeError as e:
            if e.status != 409:
                raise
    inventory = scenario.exit_inventory(scope, t.cutover, f"legacy-billing-{clinic_id.lower()}")
    store_exit_inventory(db, json.dumps(inventory).encode(), org_id=t.org_id, clinic_id=clinic_id,
                         actor=SYNTHETIC_ACTOR, now=now)
    publish_assessment(db, t, SYNTHETIC_ACTOR, now=now)
    return t.id
