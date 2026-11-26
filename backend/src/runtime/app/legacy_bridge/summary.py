"""Figures shown with an assessment, derived only from the records the assessment itself counted.

Amounts stay in signed integer minor units (fils / cents). Payer adjudication (advised), bank
settlement and ledger posting are reported separately: each answers a different question.
"""
from collections import defaultdict

from .assessment import STAGES, index_records, line_name
from .contracts import Batch, Context

BUCKETS = ("payer_ar", "patient_ar", "credit", "unapplied", "unbilled", "closed_watch")


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def summarize(ctx: Context, batch: Batch, result) -> dict:
    data, _refs = index_records(ctx, batch.records, set())
    bindings = {b.connector: b.role for b in ctx.bindings}

    def totals(kind):
        out = {b: 0 for b in BUCKETS}
        for r in data[kind].values():
            out[r.data.bucket] += r.data.minor
        return out

    legacy_lines = {r.data.line for r in data["control"].values()}
    new_lines = {r.data.line for r in data["new_line"].values()}

    deposits = defaultdict(int)
    for r in data["deposit"].values():
        deposits[r.data.trace] += r.data.minor
    advised = bank = posted = adjustments = 0
    noncash = 0
    for r in data["advice"].values():
        a = r.data
        if a.settlement == "noncash":
            noncash += 1
            continue
        advised += a.net
        bank += deposits.get(a.trace, 0)
        adjustments += sum(p.minor for p in a.parts if p.type == "provider_adjustment")
    posted = sum(r.data.component.minor for r in data["posting"].values())
    unapplied = sum(v for trace, v in deposits.items()
                    if trace not in {r.data.trace for r in data["advice"].values()})

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
        "control": totals("control"),
        "opening": totals("opening"),
        "closing": totals("closing"),
        "cash": {"advised": advised, "bank_confirmed": bank, "ledger_posted": posted,
                 "provider_adjustments": adjustments, "unapplied_bank": unapplied, "noncash_advices": noncash},
        "denials": denials,
        "routes": routes,
        "sources": source_rows,
    }
