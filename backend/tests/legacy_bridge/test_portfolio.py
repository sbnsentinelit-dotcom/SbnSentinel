import json
import unittest
from app.legacy_bridge.portfolio import ExitInventory, prepare_exit
from app.legacy_bridge.contracts import Batch, Context
from .scenario import END, S, sample


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        ctx, events = sample()
        self.packets = [(Context.model_validate_json(json.dumps(ctx)), Batch.model_validate_json(json.dumps(events)))]
        self.inv = dict(subscription="legacy-license", inventory_receipt="signed-roster", certified_at=END, as_of=END,
                        required_scopes=[S], currency="AED", avoided_minor=600000, incremental_minor=220000,
                        cost_basis_ref="signed-quotes", signoffs={r: "decision-" + r for r in ("finance", "revenue_cycle", "records", "contract_owner")},
                        dependencies=[dict(key=k, kind=k, proof_ref="doc-" + k, accepted_by=k, valid_through=END)
                                      for k in ("access", "bundled_clinical", "contract", "records", "fallback")])

    def result(self):
        return prepare_exit(ExitInventory.model_validate_json(json.dumps(self.inv)), self.packets)

    def test_all_evidence_only_prepares_human_review(self):
        self.assertEqual(self.result(), ("HUMAN_EXIT_REVIEW", ()))

    def test_zero_payer_sample_cannot_release_account(self):
        self.packets.clear()
        self.assertEqual(self.result()[0], "HOLD")

    def test_quiet_second_payer_is_not_covered(self):
        self.inv["required_scopes"].append(dict(S, payer="other"))
        self.assertIn("INCOMPLETE_SCOPE_COVERAGE", {i.code for i in self.result()[1]})

    def test_clinical_or_archive_dependency_survives(self):
        self.inv["dependencies"][1]["proof_ref"] = None
        self.assertIn("DEPENDENCY_STILL_OPEN", {i.code for i in self.result()[1]})

    def test_disappearing_entire_dependency_class(self):
        self.inv["dependencies"].pop()
        self.assertIn("DEPENDENCY_INVENTORY_INCOMPLETE", {i.code for i in self.result()[1]})

    def test_stale_roster_and_missing_human_decision(self):
        self.inv["certified_at"] = "2026-11-01T00:00:00Z"
        self.inv["signoffs"].pop("finance")
        self.assertEqual({i.code for i in self.result()[1]}, {"UNVERIFIED_SUBSCRIPTION_INVENTORY", "MISSING_SIGNOFF"})

    def test_negative_savings(self):
        self.inv["incremental_minor"] = 600001
        self.assertIn("NET_SAVINGS_UNPROVEN", {i.code for i in self.result()[1]})

    def test_same_payer_packet_twice(self):
        self.packets *= 2
        self.assertIn("INVALID_SCOPE_PACKET", {i.code for i in self.result()[1]})

    def test_finance_mismatch_blocks_entire_subscription(self):
        ctx, batch = self.packets[0]
        self.packets = [(ctx, batch.model_copy(update={"records": tuple(r for r in batch.records if r.key != "bank-1")}))]
        self.assertIn("SCOPE_ON_HOLD", {i.code for i in self.result()[1]})
