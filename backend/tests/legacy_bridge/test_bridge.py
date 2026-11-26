import copy
import json
import random
import unittest
from itertools import permutations
from pydantic import ValidationError
from app.legacy_bridge.contracts import Batch
from .scenario import CUT, DAY, END, L, N, PRIOR, rec, row, run, sample, seal


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.ctx, self.batch = sample()
        self.rows = self.batch["records"]

    def delete(self, key):
        self.rows.remove(row(self.batch, key))

    def data(self, key):
        return row(self.batch, key)["data"]

    def hold(self, code):
        result = run(self.ctx, self.batch)
        self.assertEqual(result.status, "HOLD")
        self.assertIn(code, {i.code for i in result.issues})

    def review(self):
        result = run(self.ctx, self.batch)
        self.assertEqual(result.issues, ())
        self.assertEqual(result.status, "SAMPLE_REVIEWABLE")
        return result

    def append(self, *args, **kwargs):
        self.rows.append(rec(*args, **kwargs))

    def test_mixed_legacy_new_recovery_credit_and_live_denial(self):
        result = self.review()
        self.assertEqual({x.code for x in result.follow_up}, {"WORK_DENIAL", "REVIEW_PROVIDER_ADJUSTMENT"})
        self.assertNotEqual(L, N)

    def test_empty_population_holds(self):
        self.rows.clear()
        self.hold("NO_CONTROL_POPULATION")

    def test_equal_and_opposite_opening_errors_do_not_net(self):
        self.data("opening-ar")["minor"] -= 100
        self.data("opening-patient")["minor"] += 100
        self.hold("OPENING_VARIANCE")

    def test_omitting_credit_or_closed_zero_row(self):
        self.delete("opening-watch")
        self.delete("opening-credit")
        self.hold("OPENING_VARIANCE")

    def test_negative_payer_ar_not_a_credit_bucket(self):
        self.data("opening-ar")["minor"] = -55
        self.hold("INVALID_BALANCE_SIGN")

    def test_duplicate_opening_under_another_key(self):
        duplicate = copy.deepcopy(row(self.batch, "opening-ar"))
        duplicate["key"] = "other-key"
        self.rows.append(duplicate)
        self.hold("INVALID_INVENTORY_ROW")

    def test_no_owner_acceptance(self):
        self.delete("owner-0")
        self.hold("OWNER_MISSING_OR_AMBIGUOUS")

    def test_two_owners_are_ambiguous(self):
        clone = copy.deepcopy(row(self.batch, "owner-0"))
        clone["key"] = "also-owns"
        self.rows.append(clone)
        self.hold("OWNER_MISSING_OR_AMBIGUOUS")

    def test_identical_external_claim_ids_stay_in_separate_books(self):
        self.data("ledger-A")["component"]["line"] = N
        self.hold("LEDGER_PROOF_INCOMPLETE")

    def test_unknown_line_has_no_guess(self):
        self.data("advice-1")["parts"][0]["line"]["line"] = "unmapped"
        self.hold("UNRESOLVED_LINE")

    def test_missing_bank_even_if_all_ledgers_agree(self):
        self.delete("bank-1")
        self.hold("BANK_PROOF_INCOMPLETE")

    def test_bank_without_advice(self):
        self.delete("advice-1")
        self.hold("UNAPPLIED_BANK_CASH")

    def test_one_minor_unit_variance(self):
        self.data("bank-1")["minor"] += 1
        self.hold("BANK_PROOF_INCOMPLETE")

    def test_same_amount_different_reference(self):
        self.data("bank-1")["trace"] = "other"
        self.hold("UNAPPLIED_BANK_CASH")

    def test_two_bank_transactions_with_one_trace(self):
        self.append("bank-second", "bank", DAY, "deposit", trace="pay-9", minor=0)
        self.hold("BANK_PROOF_INCOMPLETE")

    def test_two_advices_cannot_consume_same_deposit(self):
        dupe = copy.deepcopy(row(self.batch, "advice-1"))
        dupe["key"] = "other-advice"
        self.rows.append(dupe)
        self.hold("AMBIGUOUS_PAYMENT_REFERENCE")

    def test_missing_provider_adjustment_post(self):
        self.delete("ledger-P")
        self.hold("LEDGER_PROOF_INCOMPLETE")

    def test_net_must_equal_components(self):
        self.data("advice-1")["net"] = 100000
        self.hold("ADVICE_DOES_NOT_BALANCE")

    def test_same_component_twice(self):
        self.data("advice-1")["parts"][1]["part"] = "A"
        self.hold("DUPLICATE_COMPONENT")

    def test_duplicate_external_ledger_post(self):
        dupe = copy.deepcopy(row(self.batch, "ledger-A"))
        dupe["key"] = "post-twice"
        self.rows.append(dupe)
        self.hold("LEDGER_PROOF_INCOMPLETE")

    def test_orphan_posting(self):
        self.data("ledger-P")["advice"] = "missing"
        self.hold("ORPHAN_LEDGER_POSTING")

    def test_renamed_copy_keeps_single_logical_effect_and_both_receipts(self):
        dup = copy.deepcopy(row(self.batch, "advice-1"))
        dup["evidence"] = "archive-copy"
        self.rows.append(dup)
        self.assertIn("archive-copy", self.review().evidence)

    def test_conflicting_replay_held_regardless_of_arrival(self):
        dup = copy.deepcopy(row(self.batch, "bank-1"))
        dup["data"]["minor"] = 300
        self.rows.insert(0, dup)
        first = run(self.ctx, self.batch)
        self.rows.reverse()
        self.assertEqual(first, run(self.ctx, self.batch))
        self.hold("CONFLICTING_BUSINESS_EVENT")

    def test_unmapped_revision_is_quarantined(self):
        row(self.batch, "bank-1")["revision"] = 2
        self.hold("REVISION_REQUIRES_MAPPING")

    def test_transfer_changes_responsibility_without_new_cash(self):
        for bucket, val in [("payer_ar", -3000), ("patient_ar", 3000)]:
            self.append("x-" + bucket, "ledger", DAY, "movement", line=L, bucket=bucket, minor=val, reason="transfer", link="transfer-1")
        self.data("closing-ar")["minor"] -= 3000
        self.data("closing-patient")["minor"] += 3000
        seal(self.ctx, self.batch)
        self.review()

    def test_single_sided_transfer_is_held_even_with_matching_closing_total(self):
        self.append("x", "ledger", DAY, "movement", line=L, bucket="payer_ar", minor=-100, reason="transfer", link="x1")
        self.data("closing-ar")["minor"] -= 100
        self.hold("UNBALANCED_TRANSFER")

    def test_missing_delta_is_visible(self):
        self.delete("delta-A")
        self.hold("CASH_MOVEMENT_MISMATCH")
        self.hold("CLOSING_VARIANCE")

    def test_writeoff_needs_independent_approval(self):
        self.append("m", "ledger", DAY, "movement", line=L, bucket="payer_ar", minor=-30000, reason="approved_adjustment", link="okay")
        self.data("closing-ar")["minor"] = 0
        self.hold("APPROVAL_UNPROVEN")
        self.append("okay", "operations", DAY, "approval", line=L, bucket="payer_ar", minor=-30000, reason="approved_adjustment")
        seal(self.ctx, self.batch)
        self.review()

    def test_missing_cash_movement_parent(self):
        self.data("delta-A")["link"] = "absent"
        self.hold("ORPHAN_CASH_MOVEMENT")

    def extra_advice(self, aid, legs, net, settlement="cash"):
        self.append(aid, "payer", DAY, "advice", trace=aid, net=net, settlement=settlement, parts=copy.deepcopy(legs))
        if settlement == "cash":
            self.append(aid + "-bank", "bank", DAY, "deposit", trace=aid, minor=net)
        for leg in legs:
            self.append(aid + "-" + leg["part"], "ledger", DAY, "posting", advice=aid, component=copy.deepcopy(leg))

    def reverse(self, amount):
        leg = dict(part="R", type="reversal", minor=-amount, line=L, original_advice="advice-1", original_part="A")
        self.extra_advice("reverse", [leg], -amount)
        self.append("reverse-delta", "ledger", DAY, "movement", line=L, bucket="payer_ar", minor=amount,
                    reason="reversal", link="reverse-R")
        self.data("closing-ar")["minor"] += amount

    def test_linked_partial_reversal_reopens_work(self):
        self.reverse(12000)
        seal(self.ctx, self.batch)
        self.assertIn("REVIEW_REOPENED_LINE", {x.code for x in self.review().follow_up})

    def test_reversal_cannot_exceed_parent(self):
        self.reverse(70001)
        self.hold("REVERSAL_LINEAGE_UNPROVEN")

    def test_orphan_reversal(self):
        self.reverse(100)
        self.data("reverse")["parts"][0]["original_advice"] = "other"
        self.hold("REVERSAL_LINEAGE_UNPROVEN")

    def test_two_partial_reversals_cannot_exceed_original(self):
        self.reverse(40000)
        legs = copy.deepcopy(self.data("reverse")["parts"])
        self.extra_advice("r2", legs, -40000)
        self.hold("REVERSAL_LINEAGE_UNPROVEN")

    def test_zero_advice_needs_no_bank_when_parts_explain_it(self):
        self.extra_advice("zero", [dict(part="Z", type="claim_payment", minor=0, line=L)], 0, "noncash")
        self.append("zero-denial", "payer", DAY, "denial", advice="zero", line=L, notice="payer-zero", route="dest",
                    rule="DEMO-ONLY", due="2026-11-06T18:00:00Z")
        seal(self.ctx, self.batch)
        self.review()

    def test_cash_cannot_be_relabeled_zero(self):
        self.data("advice-1")["settlement"] = "noncash"
        self.hold("INVALID_NONCASH_ADVICE")

    def test_missing_denial_rule_or_deadline(self):
        self.data("den1")["rule"] = None
        self.data("den1")["due"] = None
        self.hold("DEADLINE_UNVERIFIED")

    def test_sent_is_not_receipt(self):
        self.data("den1")["due"] = DAY
        self.append("send", "operations", DAY, "appeal", denial="den1", stage="sent", receipt="dispatch")
        self.hold("DEADLINE_WITHOUT_RECEIPT")

    def test_late_receipt_does_not_reset_clock(self):
        self.data("den1")["due"] = CUT
        for stage in ("sent", "received"):
            self.append(stage, ("operations" if stage == "sent" else "payer"), DAY, "appeal", denial="den1", stage=stage, receipt=stage)
        self.hold("APPEAL_RECEIPT_LATE")

    def test_decision_still_needs_financial_outcome(self):
        for stage in ("sent", "received", "decided"):
            self.append(stage, ("operations" if stage == "sent" else "payer"), DAY, "appeal", denial="den1", stage=stage, receipt=stage)
        seal(self.ctx, self.batch)
        self.assertIn("VERIFY_FINANCIAL_OUTCOME", {x.code for x in self.review().follow_up})

    def test_receipt_without_prior_dispatch(self):
        self.append("received", "payer", DAY, "appeal", denial="den1", stage="received", receipt="ack")
        self.hold("APPEAL_STAGE_GAP")

    def test_wrong_payer_role_cannot_confirm_cash(self):
        row(self.batch, "bank-1")["connector"] = "payer"
        self.hold("SOURCE_NOT_AUTHORIZED")

    def test_wrong_facility_payer_currency_region_or_payee(self):
        for field, value in [("clinic", "elsewhere"), ("org", "other"), ("facility", "F2"), ("payer", "P2"),
                             ("payee", "PAYEE2"), ("region", "US"), ("currency", "USD"), ("deployment", "D2")]:
            with self.subTest(field=field):
                v = row(self.batch, "bank-1")["scope"]
                old = v[field]
                v[field] = value
                self.hold("SCOPE_MISMATCH")
                v[field] = old

    def test_revoked_or_expired_role_holds_even_on_cached_files(self):
        next(b for b in self.ctx["bindings"] if b["role"] == "bank")["valid_until"] = DAY
        self.hold("SOURCE_NOT_AUTHORIZED")

    def test_unknown_connector_is_held(self):
        row(self.batch, "bank-1")["connector"] = "unregistered"
        self.hold("SOURCE_NOT_AUTHORIZED")

    def test_later_observation_cannot_rewrite_prior_asof(self):
        dup = copy.deepcopy(row(self.batch, "bank-1"))
        dup["seen"] = "2026-11-06T10:00:00Z"
        dup["data"]["minor"] = 1
        self.rows.append(dup)
        self.review()

    def test_stale_or_wrong_route(self):
        self.ctx["max_probe_age_days"] = 1
        self.hold("UNPROVEN_ROUTE")

    def test_new_claim_test_cannot_certify_old_claim(self):
        self.data("old_claim_new_route")["line"] = dict(N)
        self.hold("INVALID_ROUTE_PROBE")

    def test_observation_without_source_advice(self):
        self.data("old_claim_new_route")["source_doc"] = "unverified"
        self.hold("INVALID_ROUTE_PROBE")

    def test_historical_archive_needs_resolvable_document(self):
        self.delete("archived-advice")
        self.hold("INVALID_ROUTE_PROBE")

    def test_old_system_login_cannot_prove_restored_clinic_archive(self):
        self.data("archive_restore")["route"] = "old-system-login"
        self.hold("INVALID_ROUTE_PROBE")

    def test_stale_or_missing_source_seal(self):
        self.ctx["seals"][0]["through"] = DAY
        self.hold("COVERAGE_UNPROVEN")

    def test_consistent_but_incomplete_export_fails_independent_seals(self):
        for key in ("control-credit", "opening-credit", "closing-credit", "owner-1"):
            self.delete(key)
        self.hold("COVERAGE_UNPROVEN")

    def test_rejects_float_boolean_unknown_currency_naive_timestamp_and_unknown_fields(self):
        for key, field, value in [("data", "minor", 90000.0), ("data", "minor", True), ("scope", "currency", "ZZZ"),
                                  ("data", "unexpected", 1), ("root", "effective", "2026-11-01T00:00:00")]:
            with self.subTest(field=field, value=value):
                batch = copy.deepcopy(self.batch)
                r = row(batch, "bank-1")
                (r if key == "root" else r[key])[field] = value
                with self.assertRaises(ValidationError):
                    Batch.model_validate_json(json.dumps(batch))

    def test_all_source_arrival_orders_converge(self):
        ref = self.review()
        for order in permutations(("advice-1", "bank-1", "ledger-A", "delta-A")):
            movable = [row(self.batch, k) for k in order]
            self.batch["records"] = [r for r in self.rows if r not in movable] + movable
            self.assertEqual(ref, run(self.ctx, self.batch))
        for seed in range(60):
            random.Random(seed).shuffle(self.rows)
            self.batch["records"] = self.rows
            self.assertEqual(ref, run(self.ctx, self.batch))

    def test_every_baseline_business_record_is_covered_by_a_seal(self):
        for r in self.rows:
            with self.subTest(record=r["key"]):
                batch = dict(self.batch, records=[v for v in self.rows if v is not r])
                self.assertIn("COVERAGE_UNPROVEN", {i.code for i in run(self.ctx, batch).issues})

    def test_internal_dispatch_cannot_vouch_for_payer_receipt(self):
        self.append("receipt", "operations", DAY, "appeal", denial="den1", stage="received", receipt="task-complete")
        self.hold("SOURCE_NOT_AUTHORIZED")

    def test_historical_payment_reversed_against_a_previously_zero_line(self):
        from .scenario import WATCH
        for kind, role, time in [("control", "control", CUT), ("opening", "legacy", CUT), ("closing", "control", END)]:
            self.append(kind + "-watch-ar", role, time, kind, line=WATCH, bucket="payer_ar", minor=(5000 if kind == "closing" else 0))
        original = dict(part="A", type="claim_payment", minor=5000, line=WATCH)
        self.extra_advice("oldpay", [original], 5000)
        for key in ("oldpay", "oldpay-bank", "oldpay-A"):
            r = row(self.batch, key)
            r["effective"] = r["seen"] = PRIOR
        reversal = dict(part="R", type="reversal", minor=-5000, line=WATCH, original_advice="oldpay", original_part="A")
        self.extra_advice("takeback", [reversal], -5000)
        self.append("takeback-delta", "ledger", DAY, "movement", line=WATCH, bucket="payer_ar", minor=5000,
                    reason="reversal", link="takeback-R")
        seal(self.ctx, self.batch)
        self.assertIn(("REVIEW_REOPENED_LINE", "legacy/C9/01"), [(i.code, i.item) for i in self.review().follow_up])

    def test_unattributed_provider_recovery_needs_finance_owner(self):
        self.delete("offset-owner")
        self.hold("PROVIDER_ADJUSTMENT_OWNER_UNPROVEN")

    def test_denial_on_new_book_requires_accepted_owner_too(self):
        self.data("den1")["line"] = dict(N)
        self.hold("DENIAL_OWNER_UNPROVEN")

    def test_zero_net_cannot_substitute_for_adjudication(self):
        self.extra_advice("zero", [dict(part="Z", type="claim_payment", minor=0, line=L)], 0, "noncash")
        seal(self.ctx, self.batch)
        self.hold("ZERO_COMPONENT_UNEXPLAINED")
