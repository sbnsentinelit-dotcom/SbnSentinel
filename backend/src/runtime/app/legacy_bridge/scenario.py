"""The auditor's synthetic UAE transition, placed on a clinic's own timeline.

One remittance pays 700 on an old-platform claim and 300 on a new-platform claim with the same raw
claim and line identifiers, less a 100 provider-level recovery; the bank shows one 900 deposit; the
old payer balance moves from 1,000 to 300; a separate credit of -5 stays open; a denial is still being
worked. Times are offsets from the transition's cutover, so the story is the same for every clinic and
never changes once the transition exists. Used only by the synthetic sources (SYNTHETIC_TEST_ENABLED).
"""
import copy
from datetime import datetime, timedelta

ROLES = ("control", "legacy", "destination", "payer", "bank", "ledger", "operations", "archive")


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def instants(cutover: datetime) -> dict:
    return {
        "cut": cutover,
        "prior": cutover - timedelta(days=3, hours=15),
        "day": cutover + timedelta(days=3, hours=10),
        "end": cutover + timedelta(days=4, hours=18),
        "due": cutover + timedelta(days=5, hours=18),
    }


def records_by_role(cutover: datetime) -> dict:
    """{role: [record without scope/connector]} for the scenario."""
    t = {k: _iso(v) for k, v in instants(cutover).items()}
    legacy = dict(book="legacy", claim="C7", line="01")
    new = dict(book="new", claim="C7", line="01")       # same raw IDs in the replacement book
    credit = dict(book="legacy", claim="C8", line="01")
    watch = dict(book="legacy", claim="C9", line="01")
    out = {role: [] for role in ROLES}

    def add(key, role, at, kind, **body):
        out[role].append(dict(key=key, evidence="ev-" + key, effective=at, seen=at, revision=1,
                              data=dict(kind=kind, **copy.deepcopy(body))))

    for name, line, bucket, opening, closing in [
            ("ar", legacy, "payer_ar", 100000, 30000), ("credit", credit, "credit", -500, -500),
            ("watch", watch, "closed_watch", 0, 0), ("patient", legacy, "patient_ar", 0, 0)]:
        add("control-" + name, "control", t["cut"], "control", line=line, bucket=bucket, minor=opening)
        add("opening-" + name, "legacy", t["cut"], "opening", line=line, bucket=bucket, minor=opening)
        add("closing-" + name, "control", t["end"], "closing", line=line, bucket=bucket, minor=closing)
    for no, line in enumerate((legacy, credit, watch)):
        add(f"owner-{no}", "operations", t["cut"], "assignment", line=line, owner="bill-team",
            accepted_ref=f"accept-{no}", next_action="monitor")
    add("new-c7", "destination", t["cut"], "new_line", line=new)
    add("original-claim-file", "archive", t["prior"], "archive_doc", line=legacy,
        artifact="original_submission", content_hash="a" * 64)
    add("archived-advice", "archive", t["prior"], "archive_doc", line=legacy,
        artifact="historical_advice", content_hash="b" * 64)
    legs = [dict(part="A", type="claim_payment", minor=70000, line=legacy),
            dict(part="B", type="claim_payment", minor=30000, line=new),
            dict(part="P", type="provider_adjustment", minor=-10000, adjustment_ref="offset-18")]
    add("advice-1", "payer", t["day"], "advice", trace="pay-9", net=90000, settlement="cash", parts=legs)
    add("offset-owner", "operations", t["day"], "adjustment_owner", adjustment_ref="offset-18",
        owner="finance-lead", accepted_ref="accept-offset")
    add("bank-1", "bank", t["day"], "deposit", trace="pay-9", minor=90000)
    for leg in legs:
        add("ledger-" + leg["part"], "ledger", t["day"], "posting", advice="advice-1", component=leg)
    add("delta-A", "ledger", t["day"], "movement", line=legacy, bucket="payer_ar", minor=-70000,
        reason="payer_cash", link="ledger-A")
    add("den1", "payer", t["prior"], "denial", line=legacy, notice="notice-1", route="dest",
        rule="SYNTHETIC-APPEAL-WINDOW", due=t["due"])
    for stage in ("old_retrieval", "old_claim_new_route", "new_claim_new_route", "archive_restore",
                  "fallback_recovery", "denial_route"):
        is_new = stage == "new_claim_new_route"
        role = "archive" if stage == "archive_restore" else "operations"
        route = {"old_retrieval": "payer-history", "fallback_recovery": "payer-backup",
                 "archive_restore": "clinic-archive"}.get(stage, "dest")
        doc = ("den1" if stage == "denial_route" else "advice-1" if stage.endswith("_new_route")
               else "original-claim-file" if stage == "archive_restore" else "archived-advice")
        add(stage, role, t["day"], "probe", stage=stage, line=new if is_new else legacy,
            originated_at=t["cut"] if is_new else t["prior"], source_doc=doc, receipt="receipt-" + stage,
            route=route)
    return out


TRANSITION = dict(new_route="dest", old_route="payer-history", fallback_route="payer-backup",
                  archive_route="clinic-archive", max_probe_age_days=14)


def exit_inventory(scope: dict, cutover: datetime, subscription: str) -> dict:
    """A complete synthetic dependency inventory for the subscription (HUMAN_EXIT_REVIEW when every
    scope is reviewable)."""
    end = _iso(instants(cutover)["end"])
    kinds = ("access", "bundled_clinical", "contract", "records", "fallback")
    return {
        "subscription": subscription,
        "inventory_receipt": "contract-scope-attestation",
        "certified_at": end,
        "as_of": end,
        "required_scopes": [scope],
        "dependencies": [dict(key=k, kind=k, proof_ref="verified-" + k.replace("_", "-"),
                              accepted_by="owner-" + k.replace("_", "-"), valid_through=end) for k in kinds],
        "signoffs": {k: "decision-" + k.replace("_", "-") for k in
                     ("finance", "revenue_cycle", "records", "contract_owner")},
        "currency": scope["currency"],
        "avoided_minor": 600000,
        "incremental_minor": 220000,
        "cost_basis_ref": "synthetic-quotes",
    }
