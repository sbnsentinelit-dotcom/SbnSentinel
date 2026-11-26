"""Deliberately fictional payer, facility, economic events and policy times."""
import copy
import json
from collections import defaultdict
from typing import get_args
from app.legacy_bridge.contracts import Batch, Context, Role
from app.legacy_bridge.assessment import digest_rows, assess

CUT = "2026-11-01T00:00:00Z"
DAY = "2026-11-04T10:00:00Z"
END = "2026-11-05T18:00:00Z"
PRIOR = "2026-10-28T09:00:00Z"
L = dict(book="legacy", claim="C7", line="01")
N = dict(book="new", claim="C7", line="01")  # same raw ID in different books
CREDIT = dict(book="legacy", claim="C8", line="01")
WATCH = dict(book="legacy", claim="C9", line="01")
S = dict(deployment="pilot", region="UAE", org="ORG1", clinic="SITE1", facility="FAC1",
         payee="PAYEE1", payer="TPA1", currency="AED")
ROLES = get_args(Role)


def rec(key, role, at, kind, **body):
    return dict(scope=dict(S), connector=role, key=key, evidence="ev-" + key, effective=at, seen=at,
                revision=1, data=dict(kind=kind, **body))


def sample():
    rows = []

    def add(*args, **kw):
        rows.append(rec(*args, **kw))
    for name, line, bucket, opening, closing in [
        ("ar", L, "payer_ar", 100000, 30000), ("credit", CREDIT, "credit", -500, -500),
            ("watch", WATCH, "closed_watch", 0, 0), ("patient", L, "patient_ar", 0, 0)]:
        for kind, role, at, value in [("control", "control", CUT, opening), ("opening", "legacy", CUT, opening),
                                      ("closing", "control", END, closing)]:
            add(kind + "-" + name, role, at, kind, line=dict(line), bucket=bucket, minor=value)
    for no, line in enumerate((L, CREDIT, WATCH)):
        add("owner-" + str(no), "operations", CUT, "assignment", line=dict(line), owner="bill-team",
            accepted_ref="accept-" + str(no), next_action="monitor")
    add("new-c7", "destination", CUT, "new_line", line=dict(N))
    add("original-claim-file", "archive", PRIOR, "archive_doc", line=dict(L), artifact="original_submission", content_hash="a" * 64)
    add("archived-advice", "archive", PRIOR, "archive_doc", line=dict(L), artifact="historical_advice", content_hash="b" * 64)
    legs = [dict(part="A", type="claim_payment", minor=70000, line=dict(L)),
            dict(part="B", type="claim_payment", minor=30000, line=dict(N)),
            dict(part="P", type="provider_adjustment", minor=-10000, adjustment_ref="offset-18")]
    add("advice-1", "payer", DAY, "advice", trace="pay-9", net=90000, settlement="cash", parts=copy.deepcopy(legs))
    add("offset-owner", "operations", DAY, "adjustment_owner", adjustment_ref="offset-18", owner="finance-lead", accepted_ref="accept-offset")
    add("bank-1", "bank", DAY, "deposit", trace="pay-9", minor=90000)
    for leg in legs:
        add("ledger-" + leg["part"], "ledger", DAY, "posting", advice="advice-1", component=copy.deepcopy(leg))
    add("delta-A", "ledger", DAY, "movement", line=dict(L), bucket="payer_ar", minor=-70000, reason="payer_cash", link="ledger-A")
    add("den1", "payer", PRIOR, "denial", line=dict(L), notice="notice-1", route="dest", rule="DEMO-ONLY",
        due="2026-11-06T18:00:00Z")
    for stage in ("old_retrieval", "old_claim_new_route", "new_claim_new_route", "archive_restore", "fallback_recovery", "denial_route"):
        new = stage == "new_claim_new_route"
        role = "archive" if stage == "archive_restore" else "operations"
        route = {"old_retrieval": "payer-history", "fallback_recovery": "payer-backup", "archive_restore": "clinic-archive"}.get(stage, "dest")
        doc = "den1" if stage == "denial_route" else "advice-1" if stage.endswith("_new_route") else "original-claim-file" if stage == "archive_restore" else "archived-advice"
        add(stage, role, DAY, "probe", stage=stage, line=dict(N if new else L), originated_at=(CUT if new else PRIOR),
            source_doc=doc, receipt="receipt-" + stage, route=route)
    ctx = dict(scope=dict(S), cutover=CUT, as_of=END, snapshot_at=END, new_route="dest", old_route="payer-history",
               fallback_route="payer-backup", archive_route="clinic-archive", max_probe_age_days=14,
               bindings=[dict(connector=r, role=r, scope=dict(S), valid_from="2026-01-01T00:00:00Z",
                              valid_until="2027-01-01T00:00:00Z") for r in ROLES], seals=[])
    batch = dict(version="legacy-continuity.v1", records=rows)
    seal(ctx, batch)
    return ctx, batch


def seal(ctx, batch):
    """Fixture issuer only. Never use to replace independent incoming controls."""
    records = Batch.model_validate_json(json.dumps(batch)).records
    roles = {b["connector"]: b["role"] for b in ctx["bindings"]}
    groups = defaultdict(dict)
    for r in records:
        groups[roles[r.connector]].setdefault((r.data.kind, r.key), r)
    ctx["seals"] = [dict(role=role, through=ctx["as_of"], unique_rows=len(groups[role]),
                         digest=digest_rows(groups[role].values()), receipt="seal-" + role) for role in ROLES]


def run(ctx, batch):
    return assess(Context.model_validate_json(json.dumps(ctx)), Batch.model_validate_json(json.dumps(batch)))


def row(batch, key):
    return next(r for r in batch["records"] if r["key"] == key)
