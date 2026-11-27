"""Evaluate one explicitly scoped, normalized sample. The result has no write effects."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from .identity import encode, digest

from .contracts import Batch, Context, Role
from typing import get_args

ROLE = {
    "control": "control", "closing": "control", "opening": "legacy",
    "new_line": "destination", "archive_doc": "archive", "assignment": "operations", "advice": "payer",
    "deposit": "bank", "posting": "ledger", "movement": "ledger", "approval": "operations",
    "adjustment_owner": "operations", "denial": "payer", "appeal": "operations",
}
STAGES = {"old_retrieval", "old_claim_new_route", "new_claim_new_route",
          "archive_restore", "fallback_recovery", "denial_route"}


@dataclass(frozen=True, order=True)
class Issue:
    code: str
    item: str


@dataclass(frozen=True, order=True)
class FollowUp:
    code: str
    item: str
    owner: str | None


def line_owner(data, line):
    owners = [r.data.owner for r in data["assignment"].values() if r.data.line == line]
    return owners[0] if len(owners) == 1 else None


@dataclass(frozen=True)
class Result:
    status: str
    issues: tuple[Issue, ...]
    follow_up: tuple[FollowUp, ...]
    evidence: tuple[str, ...]
    # A reviewable sample never authorizes external actions.


def line_name(line):
    return "/".join((line.book, line.claim, line.line))


def index_records(ctx, records, issues):
    bindings = {b.connector: b for b in ctx.bindings}
    grouped = defaultdict(list)
    for r in records:
        if r.scope != ctx.scope:
            issues.add(Issue("SCOPE_MISMATCH", "batch"))
            continue
        if r.seen > ctx.as_of:
            continue  # reconstruct only information available at the decision instant
        kind = r.data.kind
        role = ("archive" if r.data.stage == "archive_restore" else "operations") if kind == "probe" else ROLE[kind]
        if kind == "appeal" and r.data.stage in {"received", "decided"}:
            role = "payer"
        binding = bindings.get(r.connector)
        if (not binding or binding.role != role or not binding.valid_from <= r.seen <= ctx.as_of < binding.valid_until):
            issues.add(Issue("SOURCE_NOT_AUTHORIZED", kind))
            continue
        grouped[kind, r.key].append(r)
    by_kind = defaultdict(dict)
    refs = set()
    for (kind, key), group in grouped.items():
        refs.update(x.evidence for x in group)
        if any(x.revision != 1 for x in group):
            issues.add(Issue("REVISION_REQUIRES_MAPPING", key))
            continue
        first = group[0]
        if any((x.effective, x.data) != (first.effective, first.data) for x in group):
            issues.add(Issue("CONFLICTING_BUSINESS_EVENT", key))
            continue
        # Retransmissions from another authorized delivery path keep all archive references.
        by_kind[kind][key] = min(group, key=lambda x: (x.seen, x.evidence, x.connector))
    return by_kind, refs


def digest_rows(rows):
    """Semantic document fingerprints; archives retain separate delivery-byte hashes."""
    values = (dict(key=r.key, revision=r.revision, effective=r.effective, data=r.data.model_dump()) for r in rows)
    return digest(sorted(encode(v) for v in values))


def check_coverage(ctx, data, issues):
    bindings = {b.connector: b for b in ctx.bindings}
    by_role = defaultdict(list)
    for kind in data.values():
        for r in kind.values():
            by_role[bindings[r.connector].role].append(r)
    seals = defaultdict(list)
    for seal in ctx.seals:
        seals[seal.role].append(seal)
    for role in get_args(Role):
        group = seals[role]
        rows = by_role[role]
        if (len(group) != 1 or group[0].through != ctx.as_of or group[0].unique_rows != len(rows)
                or group[0].digest != digest_rows(rows)):
            issues.add(Issue("COVERAGE_UNPROVEN", role))
    if ctx.snapshot_at != ctx.as_of:
        issues.add(Issue("CLOSING_SNAPSHOT_BEHIND_DECISION", "control"))


def sign_ok(bucket, minor):
    return not ((bucket in {"credit", "unapplied"} and minor > 0)
                or (bucket not in {"credit", "unapplied"} and minor < 0)
                or (bucket == "closed_watch" and minor != 0))


def positions(records, at):
    """Validated positions for display: ({(line, bucket): minor} for positions with exactly one valid
    row, {(line, bucket): [rows]} for duplicated, conflicting or invalid positions). The rules in
    balances() hold on every row that is not in the first set."""
    groups = defaultdict(list)
    for r in records.values():
        groups[r.data.line, r.data.bucket].append(r)
    valid, disputed = {}, {}
    for key, rows in groups.items():
        r = rows[0]
        if (len(rows) == 1 and r.data.line.book == "legacy" and r.effective == at
                and sign_ok(r.data.bucket, r.data.minor)):
            valid[key] = r.data.minor
        else:
            disputed[key] = rows
    return valid, disputed


def trace_matches(data):
    """Per payment trace, computed once for the rules and the summary: (deposits, advices, eligible).
    All advices count when detecting collisions, cash or noncash. A trace is eligible as confirmed
    cash only with exactly one advice, which is a nonzero cash advice, and exactly one deposit of the
    same amount."""
    deposits, advices = defaultdict(list), defaultdict(list)
    for r in data["deposit"].values():
        deposits[r.data.trace].append(r)
    for r in data["advice"].values():
        advices[r.data.trace].append(r)
    out = {}
    for trace in set(deposits) | set(advices):
        d, a = deposits[trace], advices[trace]
        eligible = (len(a) == 1 and a[0].data.settlement == "cash" and a[0].data.net != 0
                    and len(d) == 1 and d[0].data.minor == a[0].data.net)
        out[trace] = (d, a, eligible)
    return out


def balances(records, at, issues, overlap_code=None):
    result = {}
    for r in records.values():
        d = r.data
        key = d.line, d.bucket
        if key in result and overlap_code:
            # Two rows of one snapshot describe the same position: hold with an explicit reason.
            issues.add(Issue(overlap_code, line_name(d.line) + "/" + d.bucket))
        if (key in result and not overlap_code) or d.line.book != "legacy" or r.effective != at:
            issues.add(Issue("INVALID_INVENTORY_ROW", r.key))
        if not sign_ok(d.bucket, d.minor):
            issues.add(Issue("INVALID_BALANCE_SIGN", r.key))
        result[key] = d.minor
    return result


def assess_inventory(ctx, data, issues):
    control = balances(data["control"], ctx.cutover, issues)
    opening = balances(data["opening"], ctx.cutover, issues)
    closing = balances(data["closing"], ctx.snapshot_at, issues, overlap_code="CLOSING_SNAPSHOT_OVERLAP")
    if not control:
        issues.add(Issue("NO_CONTROL_POPULATION", "opening"))
    for line, bucket in control.keys() | opening.keys():
        if control.get((line, bucket)) != opening.get((line, bucket)):
            issues.add(Issue("OPENING_VARIANCE", line_name(line) + "/" + bucket))
    legacy = {line for line, _ in control}
    new = set()
    for r in data["new_line"].values():
        line = r.data.line
        if line.book != "new" or r.effective < ctx.cutover:
            issues.add(Issue("INVALID_NEW_LINE", r.key))
        else:
            new.add(line)
    owners = defaultdict(list)
    for r in data["assignment"].values():
        owners[r.data.line].append(r)
        if r.data.line not in legacy | new:
            issues.add(Issue("UNKNOWN_ASSIGNED_LINE", r.key))
    for line in legacy:
        if len(owners[line]) != 1:
            issues.add(Issue("OWNER_MISSING_OR_AMBIGUOUS", line_name(line)))
    return opening, closing, legacy | new


def check_cash(ctx, data, lines, issues, follow):
    matches = trace_matches(data)
    deposits = defaultdict(list, {trace: d for trace, (d, _a, _ok) in matches.items() if d})
    traces = defaultdict(list, {trace: a for trace, (_d, a, _ok) in matches.items() if a})
    postings = defaultdict(list)
    for r in data["posting"].values():
        postings[r.data.advice, r.data.component.part].append(r)
    for trace in set(deposits) - set(traces):
        issues.add(Issue("UNAPPLIED_BANK_CASH", trace))
    parts = {}
    reversed_amounts = defaultdict(int)
    for aid, r in data["advice"].items():
        advice = r.data
        if len(traces[advice.trace]) != 1:
            issues.add(Issue("AMBIGUOUS_PAYMENT_REFERENCE", advice.trace))
        if sum(p.minor for p in advice.parts) != advice.net:
            issues.add(Issue("ADVICE_DOES_NOT_BALANCE", aid))
        bank = deposits[advice.trace]
        if advice.settlement == "noncash":
            if advice.net != 0 or bank:
                issues.add(Issue("INVALID_NONCASH_ADVICE", aid))
            adjudicated_lines = {x.data.line for x in data["denial"].values() if x.data.advice == aid}
            for part in advice.parts:
                if part.minor == 0 and (not part.line or part.line not in adjudicated_lines):
                    issues.add(Issue("ZERO_COMPONENT_UNEXPLAINED", aid + "/" + part.part))
        elif advice.net == 0 or len(bank) != 1 or bank[0].data.minor != advice.net:
            issues.add(Issue("BANK_PROOF_INCOMPLETE", aid))
        for part in advice.parts:
            identity = aid, part.part
            if identity in parts:
                issues.add(Issue("DUPLICATE_COMPONENT", aid + "/" + part.part))
            parts[identity] = r, part
            if part.line and part.line not in lines:
                issues.add(Issue("UNRESOLVED_LINE", aid + "/" + part.part))
            entries = postings[identity]
            if len(entries) != 1 or entries[0].data.component != part or entries[0].effective < r.effective:
                issues.add(Issue("LEDGER_PROOF_INCOMPLETE", aid + "/" + part.part))
            if part.type == "provider_adjustment":
                owners = [x.data.owner for x in data["adjustment_owner"].values()
                          if x.data.adjustment_ref == part.adjustment_ref]
                if len(owners) != 1:
                    issues.add(Issue("PROVIDER_ADJUSTMENT_OWNER_UNPROVEN", part.adjustment_ref))
                follow.add(FollowUp("REVIEW_PROVIDER_ADJUSTMENT", part.adjustment_ref,
                                    owners[0] if len(owners) == 1 else None))
            if part.type == "reversal":
                reversed_amounts[part.original_advice, part.original_part] -= part.minor
                follow.add(FollowUp("REVIEW_REOPENED_LINE", line_name(part.line), line_owner(data, part.line)))
    for aid, pid in set(postings) - set(parts):
        issues.add(Issue("ORPHAN_LEDGER_POSTING", aid + "/" + pid))
    for (aid, _), (record, part) in parts.items():
        if part.type != "reversal":
            continue
        parent_key = part.original_advice, part.original_part
        parent = parts.get(parent_key)
        if (not parent or parent[1].type != "claim_payment" or part.line != parent[1].line
                or record.effective < parent[0].effective or reversed_amounts[parent_key] > parent[1].minor):
            issues.add(Issue("REVERSAL_LINEAGE_UNPROVEN", aid + "/" + part.part))


def roll_forward(ctx, data, opening, closing, issues):
    expected = dict(opening)
    cash_links = defaultdict(list)
    transfers = defaultdict(list)
    approvals_used = set()
    for r in data["movement"].values():
        if not ctx.cutover < r.effective <= ctx.snapshot_at:
            if r.effective <= ctx.cutover:
                issues.add(Issue("PRE_CUTOFF_MOVEMENT_REQUIRES_REBASE", r.key))
            continue
        d = r.data
        pos = d.line, d.bucket
        if pos not in opening:
            issues.add(Issue("UNDECLARED_POSITION", r.key))
            continue
        expected[pos] += d.minor
        if d.reason in {"payer_cash", "reversal"}:
            cash_links[d.link].append(r)
        elif d.reason == "transfer":
            transfers[d.link].append(r)
        else:
            a = data["approval"].get(d.link)
            if (not a or a.effective > r.effective or d.link in approvals_used or
                    (a.data.line, a.data.bucket, a.data.minor, a.data.reason) != (d.line, d.bucket, d.minor, d.reason)):
                issues.add(Issue("APPROVAL_UNPROVEN", r.key))
            approvals_used.add(d.link)
    for link, rows in transfers.items():
        if (len(rows) != 2 or len({r.data.line for r in rows}) != 1 or
            {r.data.bucket for r in rows} != {"payer_ar", "patient_ar"} or
                sum(r.data.minor for r in rows) != 0 or not all(r.data.minor for r in rows)):
            issues.add(Issue("UNBALANCED_TRANSFER", link))
    expected_links = set()
    for pid, p in data["posting"].items():
        part = p.data.component
        if (not part.line or part.line.book != "legacy" or not ctx.cutover < p.effective <= ctx.snapshot_at
                or part.minor == 0):
            continue
        expected_links.add(pid)
        rows = cash_links[pid]
        expected_reason = "reversal" if part.type == "reversal" else "payer_cash"
        if (len(rows) != 1 or rows[0].effective != p.effective or
            (rows[0].data.line, rows[0].data.bucket, rows[0].data.minor, rows[0].data.reason)
                != (part.line, "payer_ar", -part.minor, expected_reason)):
            issues.add(Issue("CASH_MOVEMENT_MISMATCH", pid))
    for link in set(cash_links) - expected_links:
        issues.add(Issue("ORPHAN_CASH_MOVEMENT", link))
    for line, bucket in expected.keys() | closing.keys():
        if expected.get((line, bucket)) != closing.get((line, bucket)):
            issues.add(Issue("CLOSING_VARIANCE", line_name(line) + "/" + bucket))


def check_denials(ctx, data, lines, issues, follow):
    by_denial = defaultdict(dict)
    for r in data["appeal"].values():
        a = r.data
        origin = data["denial"].get(a.denial)
        stages = by_denial[a.denial]
        if not origin or r.effective < origin.effective or a.stage in stages:
            issues.add(Issue("APPEAL_LINEAGE_UNPROVEN", r.key))
        stages[a.stage] = r
    for key, r in data["denial"].items():
        d = r.data
        if d.line not in lines:
            issues.add(Issue("UNKNOWN_DENIAL_LINE", key))
        if line_owner(data, d.line) is None:
            issues.add(Issue("DENIAL_OWNER_UNPROVEN", key))
        if not d.rule or not d.due or d.due < r.effective:
            issues.add(Issue("DEADLINE_UNVERIFIED", key))
        stages = by_denial[key]
        sent, received, decided = [stages.get(k) for k in ("sent", "received", "decided")]
        if ((received and (not sent or received.effective < sent.effective))
                or (decided and (not received or decided.effective < received.effective))):
            issues.add(Issue("APPEAL_STAGE_GAP", key))
        if received and d.due and received.effective > d.due:
            issues.add(Issue("APPEAL_RECEIPT_LATE", key))
        if not received and d.due and ctx.as_of >= d.due:
            issues.add(Issue("DEADLINE_WITHOUT_RECEIPT", key))
        state = "VERIFY_FINANCIAL_OUTCOME" if decided else ("AWAIT_DECISION" if received else "OBTAIN_RECEIPT" if sent else "WORK_DENIAL")
        follow.add(FollowUp(state, key, line_owner(data, d.line)))


def movement_proofs(data, issues):
    """Whether each ledger movement proves money for its claim line, with the reconciliation rules'
    own links (07.T2). A collection (payer_cash) needs its posting on the same line and amount, the
    posting's advice, a single eligible bank deposit for the advice's trace, and no cash, ledger or
    bank finding against any of them. A write-off (approved_adjustment) needs an earlier approval with
    the same line, bucket, direction, amount and reason, and no approval finding.
    Returns {movement key: {kind, proven, reason, amount, refs}}; amount is the receivable reduction."""
    found = {(i.code, i.item) for i in issues}
    matches = trace_matches(data)
    out = {}
    for key, r in data["movement"].items():
        d = r.data
        if d.reason == "payer_cash":
            entry = {"kind": "collection", "proven": False, "amount": -d.minor, "refs": {"movement": key}}
            posting = data["posting"].get(d.link)
            advice = data["advice"].get(posting.data.advice) if posting else None
            m = matches.get(advice.data.trace) if advice else None
            if posting is None:
                entry["reason"] = "its payment posting is missing"
            elif (posting.data.component.line != d.line or posting.data.component.minor != -d.minor
                  or d.bucket != "payer_ar" or posting.effective != r.effective):
                entry["reason"] = "it does not match its payment posting"
            elif advice is None:
                entry["reason"] = "the posting's payment advice is missing"
            elif not m or not m[2]:
                entry["reason"] = "the advice has no single matching bank deposit"
            elif ({("CASH_MOVEMENT_MISMATCH", d.link), ("ORPHAN_CASH_MOVEMENT", d.link),
                   ("BANK_PROOF_INCOMPLETE", posting.data.advice),
                   ("LEDGER_PROOF_INCOMPLETE", posting.data.advice + "/" + posting.data.component.part)} & found):
                entry["reason"] = "the reconciliation holds its posting, advice or deposit"
            elif d.minor >= 0:
                entry["reason"] = "it does not reduce the receivable"
            else:
                entry.update(proven=True, reason=None,
                             refs={"movement": key, "posting": d.link, "advice": posting.data.advice,
                                   "deposit": m[0][0].key})
            out[key] = entry
        elif d.reason == "approved_adjustment":
            entry = {"kind": "write_off", "proven": False, "amount": -d.minor, "refs": {"movement": key}}
            approval = data["approval"].get(d.link)
            if approval is None:
                entry["reason"] = "its approval is missing"
            elif (approval.data.line, approval.data.bucket, approval.data.minor, approval.data.reason) != (
                    d.line, d.bucket, d.minor, d.reason):
                entry["reason"] = "its approval differs in line, bucket, direction or amount"
            elif approval.effective > r.effective:
                entry["reason"] = "its approval is dated after it"
            elif ("APPROVAL_UNPROVEN", key) in found:
                entry["reason"] = "the reconciliation holds its approval"
            elif d.bucket != "payer_ar" or d.minor >= 0:
                entry["reason"] = "it does not reduce the payer receivable"
            else:
                entry.update(proven=True, reason=None, refs={"movement": key, "approval": d.link})
            out[key] = entry
    return out


def check_probes(ctx, data, lines, issues):
    proved = set()
    for r in data["probe"].values():
        p = r.data
        stage = p.stage
        ok = p.line in lines and p.originated_at <= r.effective <= ctx.as_of
        ok &= r.effective >= ctx.cutover and ctx.as_of - r.effective <= timedelta(days=ctx.max_probe_age_days)
        if stage == "new_claim_new_route":
            ok &= p.line.book == "new" and p.originated_at >= ctx.cutover
        else:
            ok &= p.line.book == "legacy" and p.originated_at < ctx.cutover
        if stage in {"old_claim_new_route", "new_claim_new_route"}:
            a = data["advice"].get(p.source_doc)
            ok &= bool(a and ctx.cutover <= a.effective <= r.effective and any(x.line == p.line for x in a.data.parts))
        if stage in {"archive_restore", "old_retrieval", "fallback_recovery"}:
            source = data["archive_doc"].get(p.source_doc)
            expected = "original_submission" if stage == "archive_restore" else "historical_advice"
            ok &= bool(source and source.data.line == p.line and source.data.artifact == expected
                       and source.effective == p.originated_at)
        if stage == "old_claim_new_route":
            ok &= any(x.data.line == p.line and x.data.artifact == "original_submission"
                      and x.effective == p.originated_at for x in data["archive_doc"].values())
        if stage == "new_claim_new_route":
            ok &= any(x.data.line == p.line and x.effective == p.originated_at for x in data["new_line"].values())
        if stage == "denial_route":
            d = data["denial"].get(p.source_doc)
            ok &= bool(d and d.data.line == p.line and d.data.route == p.route and d.effective <= r.effective)
        expected_route = {"old_retrieval": ctx.old_route, "fallback_recovery": ctx.fallback_route}.get(stage, ctx.new_route)
        # Archive receipt identifies a separate owned repository, not a payer receiver.
        ok &= p.route == (ctx.archive_route if stage == "archive_restore" else expected_route)
        if ok:
            proved.add(stage)
        else:
            issues.add(Issue("INVALID_ROUTE_PROBE", r.key))
    for stage in STAGES - proved:
        issues.add(Issue("UNPROVEN_ROUTE", stage))


def assess(ctx: Context, batch: Batch) -> Result:
    issues, follow = set(), set()
    data, refs = index_records(ctx, batch.records, issues)
    check_coverage(ctx, data, issues)
    opening, closing, lines = assess_inventory(ctx, data, issues)
    check_cash(ctx, data, lines, issues, follow)
    roll_forward(ctx, data, opening, closing, issues)
    check_denials(ctx, data, lines, issues, follow)
    check_probes(ctx, data, lines, issues)
    refs.update(seal.receipt for seal in ctx.seals)
    return Result("HOLD" if issues else "SAMPLE_REVIEWABLE", tuple(sorted(issues)), tuple(sorted(follow)), tuple(sorted(refs)))
