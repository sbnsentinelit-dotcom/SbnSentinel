"""Figures shown with an assessment, derived only from the records the assessment itself counted.

Amounts stay in signed integer minor units (fils / cents). Payer adjudication (advised), bank
settlement and ledger posting are reported separately: each answers a different question.
"""
from collections import defaultdict

from .assessment import STAGES, index_records, line_name, positions, trace_matches
from .contracts import Batch, Context

BUCKETS = ("payer_ar", "patient_ar", "credit", "unapplied", "unbilled", "closed_watch")


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def summarize(ctx: Context, batch: Batch, result) -> dict:
    data, _refs = index_records(ctx, batch.records, set())
    bindings = {b.connector: b.role for b in ctx.bindings}

    # Displayed balances come only from validated unique positions. A duplicated, conflicting or
    # invalid position is left out of its bucket total, the bucket is marked unverified and its rows
    # are listed, so a held position never inflates a figure.
    unverified = {}

    def totals(kind, at):
        out = {b: 0 for b in BUCKETS}
        valid, disputed = positions(data[kind], at)
        for (_line, bucket), minor in valid.items():
            out[bucket] += minor
        for (line, bucket), rows in sorted(disputed.items(), key=lambda kv: (line_name(kv[0][0]), kv[0][1])):
            unverified.setdefault(kind, {}).setdefault(bucket, []).extend(
                {"key": r.key, "line": line_name(line), "minor": r.data.minor, "effective": _iso(r.effective)}
                for r in sorted(rows, key=lambda x: x.key))
        return out

    legacy_lines = {r.data.line for r in data["control"].values()}
    new_lines = {r.data.line for r in data["new_line"].values()}

    # Each bank record counts once as observed. Confirmed cash uses the same trace eligibility as the
    # rules (trace_matches): an ambiguous trace, cash or noncash, stays unmatched until reconciled.
    matches = trace_matches(data)
    advised = adjustments = noncash = 0
    for r in data["advice"].values():
        a = r.data
        if a.settlement == "noncash":
            noncash += 1
            continue
        advised += a.net
        adjustments += sum(p.minor for p in a.parts if p.type == "provider_adjustment")
    observed = sum(r.data.minor for r in data["deposit"].values())
    matched = sum(d[0].data.minor for d, _a, eligible in matches.values() if eligible)
    unapplied = sum(r.data.minor for d, a, _ok in matches.values() if not a for r in d)
    posted = sum(r.data.component.minor for r in data["posting"].values())
    unproven = {i.item for i in result.issues if i.code == "COVERAGE_UNPROVEN"}

    def coverage(role, rows):
        if role not in unproven:
            return "complete"
        return "incomplete" if rows else "missing"

    bank_coverage = coverage("bank", data["deposit"])
    ledger_coverage = coverage("ledger", data["posting"])
    bank_known = bank_coverage != "missing"

    appeals = defaultdict(set)
    for r in data["appeal"].values():
        appeals[r.data.denial].add(r.data.stage)
    owners = defaultdict(list)
    for r in data["assignment"].values():
        owners[r.data.line].append(r.data.owner)
    denials = []
    for key, r in sorted(data["denial"].items()):
        d = r.data
        stages = appeals.get(key, set())
        stage = next((s for s in ("decided", "received", "sent") if s in stages), None)
        line_owners = owners.get(d.line, [])
        denials.append({
            "key": key, "line": line_name(d.line), "noticed_at": _iso(r.effective), "due": _iso(d.due),
            "rule_verified": bool(d.rule), "appeal_stage": stage,
            "owner": line_owners[0] if len(line_owners) == 1 else None,
        })

    unproven = {i.item for i in result.issues if i.code == "UNPROVEN_ROUTE"}
    routes = [{"stage": s, "proved": s not in unproven} for s in sorted(STAGES)]

    sources = defaultdict(lambda: {"rows": 0, "last_effective": None})
    for kind in data.values():
        for r in kind.values():
            src = sources[bindings[r.connector]]
            src["rows"] += 1
            if src["last_effective"] is None or r.effective > src["last_effective"]:
                src["last_effective"] = r.effective
    seals = {s.role: s for s in ctx.seals}
    covered = {i.item for i in result.issues if i.code == "COVERAGE_UNPROVEN"}
    roles = sorted({b.role for b in ctx.bindings} | set(seals))
    source_rows = [{
        "role": role,
        "rows": sources[role]["rows"],
        "last_effective": _iso(sources[role]["last_effective"]),
        "sealed_through": _iso(seals[role].through) if role in seals else None,
        "sealed_rows": seals[role].unique_rows if role in seals else None,
        "coverage_proved": role not in covered,
    } for role in roles]

    return {
        "currency": ctx.scope.currency,
        "lines": {"legacy": len(legacy_lines), "new": len(new_lines)},
        "control": totals("control", ctx.cutover),
        "opening": totals("opening", ctx.cutover),
        "closing": totals("closing", ctx.snapshot_at),
        "unverified": unverified,
        # bank_* amounts are None when no bank evidence was delivered (unknown, not zero); with a
        # delivered but unproven bank source the amounts are measured and bank_coverage qualifies them.
        "cash": {"advised": advised,
                 "bank_observed": observed if bank_known else None,
                 "bank_confirmed": matched if bank_known else None,
                 "bank_unmatched": observed - matched if bank_known else None,
                 "bank_coverage": bank_coverage,
                 "ledger_posted": posted if ledger_coverage != "missing" else None,
                 "ledger_coverage": ledger_coverage,
                 "provider_adjustments": adjustments,
                 "unapplied_bank": unapplied if bank_known else None,
                 "noncash_advices": noncash},
        "denials": denials,
        "routes": routes,
        "sources": source_rows,
        # The control population this assessment used: signed controls and opening at the cutover,
        # the closing snapshot at snapshot_at (record keys, as delivered).
        "control_ids": {kind: sorted(data[kind]) for kind in ("control", "opening", "closing")},
        "snapshot_at": _iso(ctx.snapshot_at),
    }
