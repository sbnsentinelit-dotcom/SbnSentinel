"""Isolated H02-H08 contract/integration tests. No application DB imports.

python -B -m pytest --noconftest -p no:cacheprovider tests/integration/test_hospital_source_contract.py
"""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Override application seeding: each suite owns its subprocess database."""
    return None


def test_isolated_hospital_contracts():
    result = subprocess.run([sys.executable, "-B", "-I", str(Path(__file__).resolve()), "--worker"],
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True,
                            timeout=600 if os.getenv("HSP01_RUN_LOAD") == "1" else 60)
    assert result.returncode == 0, result.stdout + result.stderr


def worker():
    import types
    import unittest
    import json
    import asyncio
    import io
    from datetime import datetime, timezone
    from decimal import Decimal
    from sqlalchemy import create_engine, select, event
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "runtime"))
    database = types.ModuleType("app.db.database")
    database.Base = declarative_base()
    database.SessionLocal = sessionmaker()
    sys.modules["app.db.database"] = database
    from app.models import organization, clinic, user, signal, encounter, audit, connector, event as event_model, intelligence, governance_storage  # noqa: F401 -- register related ORM models
    from app.models import evidence, financial_reporting, hospital_dimensions, hospital_membership, hospital_processing, work_assignment
    from app.services.hospital_directory_service import HospitalDirectoryService
    from app.services.hospital_scope_service import resolve_hospital_scope, HospitalAccessDenied
    from app.integrations.hospital_read.identity import entity_key
    from app.integrations.hospital_read.manifest import HospitalReadProfile
    from app.integrations.hospital_read.mappings import normalize_record, exact_money
    from app.integrations.hospital_read.csv_source import CsvSource
    from app.integrations.hospital_read.rest_source import RestSource
    from app.integrations.hospital_read.adapter import HospitalReadAdapter
    from app.services.ingress_service import CanonicalIngressService
    from app.integrations.core.source_authority import connector_store, ConnectorBinding
    from app.integrations.core.source_authority import validate_source_authority
    from app.services.hospital_worker import HospitalWorker
    from app.services.financial_reporting_service import FinancialReportingService
    from app.services.work_assignment_service import WorkAssignmentService, AssignmentConflict
    import httpx

    def date(day):
        return datetime(2026, 1, day, tzinfo=timezone.utc)

    class HospitalTests(unittest.TestCase):
        def setUp(self):
            self.prior_approved_origins = os.environ.get("HSP01_APPROVED_SOURCE_ORIGINS")
            os.environ["HSP01_APPROVED_SOURCE_ORIGINS"] = "https://hospital.example.invalid"
            test_url = os.getenv("HSP01_TEST_DATABASE_URL")
            self.pg_schema = None
            if test_url:
                from sqlalchemy.engine import make_url
                import uuid
                url = make_url(test_url)
                if url.host != "127.0.0.1" or url.port != 55439 or not url.database.startswith("hsp01_"):
                    raise RuntimeError("Only the dedicated disposable HSP01 database is permitted")
                self.admin_engine = create_engine(test_url)
                self.pg_schema = "hsp01_test_" + uuid.uuid4().hex
                with self.admin_engine.begin() as connection:
                    connection.exec_driver_sql(f'CREATE SCHEMA "{self.pg_schema}"')
                self.engine = create_engine(test_url, connect_args={"options": f"-csearch_path={self.pg_schema}"})
            else:
                self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

                @event.listens_for(self.engine, "connect")
                def fk(conn, _):
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.isolation_level = None

                @event.listens_for(self.engine, "begin")
                def begin(conn):
                    conn.exec_driver_sql("BEGIN")
            database.Base.metadata.create_all(self.engine)
            self.sessions = sessionmaker(self.engine, expire_on_commit=False)
            database.SessionLocal = self.sessions
            self.db = self.sessions()
            self.db.add(organization.OrganizationModel(id="org", name="Hospital Customer"))
            self.db.flush()
            self.db.add_all([organization.OrganizationClinicModel(id="a", organization_id="org", name="A"),
                             organization.OrganizationClinicModel(id="b", organization_id="org", name="B")])
            self.db.flush()
            self.db.add(hospital_dimensions.HospitalFacilityEnrollment(clinic_id="a", basis_ref="fixture:enrollment"))
            self.db.flush()
            self.actor = user.User(id=1, email="test@example.invalid", hashed_password="not-a-login", org_id="org", clinic_id="a", is_active=True)
            self.db.add(self.actor)
            self.db.flush()
            directory = HospitalDirectoryService(self.db)
            self.a = directory.create_department("org", "a", "CARD", "Cardiology", date(1), "fixture:v1").id
            self.b = directory.create_department("org", "a", "RAD", "Radiology", date(1), "fixture:v1").id
            self.grant = hospital_membership.HospitalMembership(actor_id=1, clinic_id="a", department_ids=[self.a],
                                                                capabilities=["report.read", "report.export", "review.read", "assignment.write"], grant_revision=1)
            self.db.add(self.grant)
            self.db.commit()
            self.profile = HospitalReadProfile(revision="test-v1", org_id="org", clinic_id="a", connector_id="conn",
                                               base_url="https://hospital.example.invalid", endpoints={kind: "/" + kind for kind in ["Claim", "ClaimLine", "SubmissionAttempt", "Receipt"]},
                                               mapping_id="test-status", mapping_version="1", cohort_hash="fixture-v1", department_ids_are_canonical=True,
                                               status_map={"Claim": {"SUB": "submitted"}, "ClaimLine": {"DEN": "denied"},
                                                           "SubmissionAttempt": {"REJ": "rejected_technical"}, "Receipt": {"REC": "received"}})
            connector_store.register(ConnectorBinding(connector_instance_id="conn", vendor_id="hospital_read",
                                                      allowed_clinic_id="a", state="AUTHORIZED_READY", permitted_resources=set(self.profile.endpoints), jurisdiction="UAE"))
            self.intake = CanonicalIngressService()
            self.intake.hospital_session_factory = self.sessions
            self.intake.hospital_authority_override = lambda profile, kind: validate_source_authority(
                profile.connector_id, profile.clinic_id, kind, target_region="UAE")
            # Test the UAE profile without reading the developer's .env region.
            from app.core.config import settings
            settings.DEPLOYMENT_REGION = "UAE"

        def tearDown(self):
            if self.prior_approved_origins is None:
                os.environ.pop("HSP01_APPROVED_SOURCE_ORIGINS", None)
            else:
                os.environ["HSP01_APPROVED_SOURCE_ORIGINS"] = self.prior_approved_origins
            self.db.close()
            self.engine.dispose()
            connector_store.clear()
            if self.pg_schema:
                with self.admin_engine.begin() as connection:
                    connection.exec_driver_sql(f'DROP SCHEMA "{self.pg_schema}" CASCADE')
                self.admin_engine.dispose()

        def raw_claim(self, **changes):
            return {"id": "claim_1", "source_version": "v1", "status": "SUB", "patient_id": "p1", "provider_id": "qualified:pr1",
                    "payer_id": "pay1", "billed_amount": "100.10", "currency": "AED", "service_date": date(2).isoformat(), "source_sequence": 1, **changes}

        def submit(self, kind, raw):
            record = normalize_record(raw, resource_kind=kind, context=self.profile)
            return self.intake.submit_hospital_page(self.profile, kind, record, [],
                                                    {"binding": self.profile.cursor_binding(kind), "token": None})

        def reviewed_source(self):
            from app.services.hospital_source_service import configure_source
            self.grant.capabilities = [*self.grant.capabilities, "source.configure", "source.read"]
            self.grant.all_departments = True
            self.grant.full_record = True
            self.db.commit()
            os.environ["HSP01_FIXTURE_SECRET"] = "synthetic-test-only"
            configure_source(self.db, self.actor, self.profile, secret_ref="env://HSP01_FIXTURE_SECRET",
                             auth_header="Authorization", auth_prefix="Bearer ", reason="Reviewed fixture profile")
            self.db.commit()

        def line_raw(self, **changes):
            return {"id": "1", "claim_id": "claim_1", "source_version": "v1", "status": "DEN",
                    "source_sequence": 1, "department_id": self.a, "attribution_basis": "fixture:line",
                    "procedure_code": "TEST", "code_system": "TEST", "code_version": "1", "units": 1,
                    "billed_amount": "100.10", "currency": "AED", **changes}

        def test_auditor_zero_department_enrollment_guards_legacy_routes(self):
            from app.models.hospital_dimensions import HospitalFacilityEnrollment
            from app.services.hospital_scope_service import hospital_facility_ids
            self.db.add(HospitalFacilityEnrollment(clinic_id="b", basis_ref="explicit:zero-department-hospital"))
            self.db.commit()
            self.assertIn("b", self.db.scalars(hospital_facility_ids()).all())
            self.db.commit()
            from dataclasses import replace
            hospital_b = replace(self.profile, clinic_id="b", connector_id="conn-b")
            connector_store.register(ConnectorBinding(connector_instance_id="conn-b", vendor_id="hospital_read",
                                                      allowed_clinic_id="b", state="AUTHORIZED_READY", permitted_resources=set(hospital_b.endpoints), jurisdiction="UAE"))
            record = normalize_record(self.raw_claim(), resource_kind="Claim", context=hospital_b)
            self.intake.submit_hospital_page(hospital_b, "Claim", record, [],
                                             {"binding": hospital_b.cursor_binding("Claim"), "token": None})
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="zero-departments")
            self.assertEqual(worker.run_once(), "APPLIED")
            self.db.add(hospital_membership.HospitalMembership(actor_id=1, clinic_id="b", capabilities=["report.read"],
                                                               all_departments=True, full_record=True, allow_unattributed=True))
            self.db.commit()
            scope_b = resolve_hospital_scope(self.db, self.actor, {"clinic_id": "b"})
            frozen = FinancialReportingService().freeze_hospital_summary(self.db, scope_b, date(1), date(10), "AED")
            snapshot_id = frozen.snapshot_id
            self.db.commit()
            from starlette.requests import Request
            from fastapi import HTTPException
            from app.services.hospital_legacy_guard import hospital_legacy_guard
            outsider = user.User(id=2, email="outsider@example.invalid", hashed_password="disabled", org_id="another-org", is_active=True)
            self.db.add(outsider)
            self.db.commit()
            request = Request({"type": "http", "method": "GET", "path": "/financial-reports/owner-summary",
                               "query_string": b"clinic_id=b", "headers": [], "path_params": {}})
            with self.assertRaises(HTTPException) as denied:
                hospital_legacy_guard(request, outsider, self.db)
            self.assertEqual(denied.exception.status_code, 403)
            with self.assertRaises(HospitalAccessDenied):
                resolve_hospital_scope(self.db, outsider, {"clinic_id": "b"})
            self.db.add(clinic.ClinicModel(id="legacy-b", name="Legacy"))
            self.db.flush()
            self.db.add(hospital_processing.FacilityCrosswalk(legacy_clinic_id="legacy-b", clinic_id="b", status="RESOLVED", basis_ref="review:alias"))
            self.db.commit()
            self.assertIn("legacy-b", self.db.scalars(hospital_facility_ids()).all())
            from fastapi import FastAPI, Depends
            from fastapi.testclient import TestClient
            from app.api.v1.endpoints import financial_reports, hospital
            app = FastAPI()
            app.include_router(financial_reports.router, prefix="/financial-reports", dependencies=[Depends(hospital_legacy_guard)])
            app.include_router(hospital.router, prefix="/hospital")

            def current_actor():
                return outsider

            def current_db():
                with self.sessions() as db:
                    yield db
            for function in (financial_reports.get_current_user, hospital.get_current_user):
                app.dependency_overrides[function] = current_actor
            for function in (financial_reports.get_db, hospital.get_db, database.get_db):
                app.dependency_overrides[function] = current_db
            self.db.commit()
            with TestClient(app) as client:
                for url in ("/financial-reports/summary?clinic_id=b", "/hospital/summary?clinic_id=b",
                            f"/financial-reports/export/{snapshot_id}/csv"):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 403, response.text)
                self.db.rollback()
                HospitalDirectoryService(self.db).create_department("org", "b", "FIRST", "First", date(1), "fixture:first")
                self.db.commit()
                self.assertEqual(client.get("/financial-reports/summary?clinic_id=b").status_code, 403)

        def test_auditor_parent_currency_correction_never_relabels_line(self):
            self.submit("Claim", self.raw_claim())
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="currency")
            self.assertEqual(worker.run_once(), "APPLIED")
            self.submit("ClaimLine", self.line_raw())
            self.assertEqual(worker.run_once(), "APPLIED")
            scope = resolve_hospital_scope(self.db, self.actor, {"clinic_id": "a"})
            reporting = FinancialReportingService()
            frozen = reporting.freeze_hospital_summary(self.db, scope, date(1), date(10), "AED")
            original = frozen.payload_json
            self.db.commit()
            self.submit("Claim", self.raw_claim(source_version="v2", source_sequence=2, currency="USD"))
            self.assertEqual(worker.run_once(), "APPLIED")
            self.db.expire_all()
            result = reporting.generate_hospital_summary(self.db, scope, date(1), date(10), "USD")
            self.assertEqual(result["metrics"]["line_count"], 0)
            self.assertEqual(result["metrics"]["currency_conflict_lines"], 1)
            self.assertEqual(frozen.payload_json, original)
            self.db.commit()
            self.submit("ClaimLine", self.line_raw(source_version="v2", source_sequence=2, currency="USD"))
            self.assertEqual(worker.run_once(), "APPLIED")
            self.db.expire_all()
            result = reporting.generate_hospital_summary(self.db, scope, date(1), date(10), "USD")
            self.assertEqual(result["metrics"]["line_count"], 1)
            self.assertEqual(result["metrics"]["currency_conflict_lines"], 0)

        def test_auditor_temporary_source_failure_can_reverify_unchanged_profile(self):
            from app.services.hospital_source_service import verify_source, SourceReadFailed
            self.reviewed_source()

            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
                    with self.assertRaises(SourceReadFailed):
                        await verify_source(self.db, self.actor, "conn", client)
                self.db.commit()
                self.assertEqual(self.db.get(connector.ConnectorModel, "conn").state, "DEGRADED")
                calls = []

                def healthy(request):
                    calls.append(request)
                    return httpx.Response(200, json={"records": [], "next": None})
                async with httpx.AsyncClient(transport=httpx.MockTransport(healthy)) as client:
                    await verify_source(self.db, self.actor, "conn", client)
                self.db.commit()
                self.assertEqual(len(calls), len(self.profile.endpoints))
                self.assertEqual(self.db.get(connector.ConnectorModel, "conn").state, "AUTHORIZED_READY")
            asyncio.run(run())

        def test_auditor_revocation_during_failed_verification_is_not_overwritten(self):
            from app.services.hospital_source_service import verify_source
            self.reviewed_source()

            def revoke(request):
                with self.sessions() as db:
                    row = db.get(connector.ConnectorModel, "conn")
                    row.state = "REVOKED"
                    db.commit()
                return httpx.Response(503)

            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(revoke)) as client:
                    with self.assertRaises(PermissionError):
                        await verify_source(self.db, self.actor, "conn", client)
                self.db.rollback()
                self.assertEqual(self.db.get(connector.ConnectorModel, "conn", populate_existing=True).state, "REVOKED")
            asyncio.run(run())

        def test_auditor_blocked_delivery_resume_is_versioned_and_projects_once(self):
            from app.services.hospital_source_service import verify_source
            from app.services.hospital_worker import resume_blocked
            self.reviewed_source()
            self.submit("Claim", self.raw_claim())

            def deny(*args):
                raise PermissionError("Expired authority")
            worker = HospitalWorker(self.sessions, deny, worker_id="blocked")
            self.assertEqual(worker.run_once(), "BLOCKED")

            async def verify():
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"records": [], "next": None}))) as client:
                    await verify_source(self.db, self.actor, "conn", client)
                self.db.commit()
            asyncio.run(verify())
            event_row = self.db.scalars(select(event_model.OperationalEventModel)).one()
            identity, revision = event_row.id, event_row.row_version
            resume_blocked(self.db, self.actor, identity, expected_revision=revision, reason="Read authority restored")
            self.db.commit()
            resume_blocked(self.db, self.actor, identity, expected_revision=revision, reason="Idempotent retry")
            self.db.commit()
            worker.authorize = lambda *args: None
            self.assertEqual(worker.run_once(), "APPLIED")
            self.assertEqual(self.submit("Claim", self.raw_claim())["duplicate"], 1)
            self.assertEqual(worker.run_once(), "IDLE")
            self.db.expire_all()
            self.assertEqual(len(self.db.scalars(select(financial_reporting.FinancialClaimModel)).all()), 1)

        def test_auditor_effective_department_date_is_required(self):
            future = HospitalDirectoryService(self.db).create_department("org", "a", "FUT", "Future", date(10), "fixture:future").id
            self.db.commit()
            self.submit("Claim", self.raw_claim())
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="effective")
            self.assertEqual(worker.run_once(), "APPLIED")
            self.submit("ClaimLine", self.line_raw(department_id=future))
            self.assertEqual(worker.run_once(), "DEFERRED")
            self.submit("Claim", self.raw_claim(id="later", service_date=date(11).isoformat()))
            self.assertEqual(worker.run_once(), "APPLIED")
            self.submit("ClaimLine", self.line_raw(claim_id="later", department_id=future))
            self.assertEqual(worker.run_once(), "APPLIED")
            self.db.expire_all()
            row = self.db.scalars(select(financial_reporting.FinancialClaimLineModel)).one()
            self.assertIsNotNone(row.attribution_version_id)
            self.assertEqual(row.attribution_time, date(11).replace(tzinfo=None))

        def test_auditor_quarantine_page_rows_are_recoverable_and_atomic(self):
            from app.integrations.hospital_read.rest_source import SourcePage
            import hashlib
            binding = self.profile.cursor_binding("Claim")
            adapter = HospitalReadAdapter(self.profile, None, authorize=lambda *args: None, intake=self.intake)

            async def reject(raw, token):
                page = SourcePage((raw,), token, binding, len(json.dumps(raw).encode()))
                return await adapter._submit("Claim", page)
            asyncio.run(reject({"id": "bad-one", "source_version": "v1"}, "page-two"))
            asyncio.run(reject({"id": "bad-two", "source_version": "v1"}, None))
            asyncio.run(reject({"id": "bad-two", "source_version": "v1"}, None))
            with self.sessions() as db:
                rows = db.scalars(select(hospital_processing.HospitalQuarantine)).all()
                self.assertEqual(len(rows), 2)
                self.assertNotEqual(rows[0].page_id, rows[1].page_id)
                self.assertEqual({json.loads(row.payload_json)["id"] for row in rows}, {"bad-one", "bad-two"})
                for row in rows:
                    self.assertEqual(hashlib.sha256(row.payload_json.encode()).hexdigest(), row.payload_digest)
            old = self.intake.get_hospital_checkpoint(self.profile, "Claim")

            def fail(*args):
                raise RuntimeError("Quarantine persistence failed")
            event.listen(hospital_processing.HospitalQuarantine, "before_insert", fail)
            try:
                with self.assertRaises(RuntimeError):
                    asyncio.run(reject({"id": "third"}, "must-not-advance"))
            finally:
                event.remove(hospital_processing.HospitalQuarantine, "before_insert", fail)
            self.assertEqual(self.intake.get_hospital_checkpoint(self.profile, "Claim"), old)

        def test_auditor_rest_csv_same_version_semantics_and_distinct_raw_digests(self):
            import csv
            raw = self.raw_claim(paid_amount="0", deleted=False)
            self.assertEqual(self.submit("Claim", raw)["accepted"], 1)
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(raw))
            writer.writeheader()
            writer.writerow({key: str(value).lower() if type(value) is bool else value for key, value in raw.items()})

            async def run():
                adapter = HospitalReadAdapter(self.profile, None, authorize=lambda *args: None, intake=self.intake)
                return await adapter.import_csv(io.StringIO(output.getvalue()), "Claim")
            self.assertEqual(asyncio.run(run())["duplicate"], 1)
            with self.sessions() as db:
                rows = db.scalars(select(hospital_processing.HospitalTransportObservation)).all()
                self.assertEqual({row.transport for row in rows}, {"rest", "csv"})
                self.assertEqual(len({row.raw_digest for row in rows}), 2)
            self.assertEqual(self.submit("Claim", self.raw_claim(paid_amount="0", deleted=False, billed_amount="100.1000"))["duplicate"], 1)
            self.assertEqual(self.submit("Claim", self.raw_claim(paid_amount=None, deleted=False))["quarantined"], 1)

        def test_identity_parent_and_delimiter_isolation(self):
            def key(clinic="a", connector="conn", parent="A"):
                return entity_key(org_id="org", clinic_id=clinic, connector_id=connector, resource_kind="ClaimLine", external_id="1", parent_id=parent)
            self.assertNotEqual(key(parent="A"), key(parent="B"))
            self.assertNotEqual(key("unit_a", "conn"), key("unit", "a_conn"))
            self.assertNotEqual(key("a"), key("b"))
            with self.assertRaises(ValueError):
                entity_key(org_id="org", clinic_id="a", connector_id="conn", resource_kind="ClaimLine", external_id="1")

        def test_scope_narrowing_revocation_and_unattributed(self):
            scope = resolve_hospital_scope(self.db, self.actor, {"clinic_id": "a"})
            self.assertEqual(scope.allowed_department_ids, {self.a})
            self.assertFalse(scope.allow_unattributed)
            for selection in ({"clinic_id": "a", "department_ids": [self.b]}, {"clinic_id": "b"}):
                with self.assertRaises(HospitalAccessDenied):
                    resolve_hospital_scope(self.db, self.actor, selection)
            self.grant.is_active = False
            self.db.commit()
            with self.assertRaises(HospitalAccessDenied):
                resolve_hospital_scope(self.db, self.actor, {"clinic_id": "a"})

        def test_explicit_hospital_access_can_be_narrowed(self):
            self.grant.all_departments = True
            self.grant.allow_unattributed = True
            self.grant.full_record = True
            self.db.commit()
            full = resolve_hospital_scope(self.db, self.actor, {"clinic_id": "a"})
            narrow = resolve_hospital_scope(self.db, self.actor, {"clinic_id": "a", "department_ids": [self.a]})
            self.assertTrue(full.full_record)
            self.assertTrue(full.allow_unattributed)
            self.assertFalse(narrow.full_record)
            self.assertFalse(narrow.allow_unattributed)

        def test_zero_null_absent_and_money_validation(self):
            raw = self.raw_claim(paid_amount="0", allowed_amount=None)
            facts = normalize_record(raw, resource_kind="Claim", context=self.profile)[0]["detail"]
            self.assertEqual(facts["paid_amount"], "0")
            self.assertIsNone(facts["allowed_amount"])
            self.assertNotIn("patient_share", facts)
            for value in (0.1, True, "NaN", "Infinity", "1e30", "0.000000001"):
                with self.assertRaises(ValueError):
                    exact_money(value)

        def test_source_department_crosswalk_and_unknown_are_explicit(self):
            from dataclasses import replace
            profile = replace(self.profile, department_ids_are_canonical=False, department_map={"CARD-SOURCE": self.a})
            raw = {"id": "1", "claim_id": "claim", "source_version": "v1", "status": "DEN", "department_code": "CARD-SOURCE"}
            mapped = normalize_record(raw, resource_kind="ClaimLine", context=profile)[0]["detail"]
            self.assertEqual(mapped["department_id"], self.a)
            self.assertEqual(mapped["source_department_code"], "CARD-SOURCE")
            raw["department_code"] = "UNMAPPED"
            unknown = normalize_record(raw, resource_kind="ClaimLine", context=profile)[0]["detail"]
            self.assertIsNone(unknown["department_id"])
            self.assertEqual(unknown["department_mapping_state"], "UNMAPPED")

        def test_economic_receipt_crosswalk_prevents_double_cash(self):
            for source_id, confirmed in (("billing-observation", False), ("bank-observation", True)):
                self.submit("Receipt", {"id": source_id, "source_version": "v1", "status": "REC",
                                        "amount": "1000.00", "currency": "AED", "received_date": date(2).isoformat(),
                                        "payment_reference": "fixture-payment-1", "receipt_type": "payer_payment", "bank_confirmed": confirmed})
            with self.sessions() as db:
                keys = db.scalars(select(hospital_processing.SourceIdentityCrosswalk.entity_key).where(
                    hospital_processing.SourceIdentityCrosswalk.resource_kind == "Receipt")).all()
                for key in keys:
                    db.add(hospital_processing.HospitalSettlementLink(source_entity_key=key, clinic_id="a",
                                                                      economic_receipt_key="fixture-economic-receipt", basis_ref="fixture:verified-settlement-link"))
                db.commit()
            projection_worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="receipt-test")
            self.assertEqual(projection_worker.run_once(), "APPLIED")
            self.assertEqual(projection_worker.run_once(), "APPLIED")
            with self.sessions() as db:
                receipts = db.scalars(select(financial_reporting.FinancialPaymentReceiptModel)).all()
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0].amount, Decimal("1000"))
                self.assertTrue(receipts[0].bank_confirmed)
                self.assertIsNone(receipts[0].department_id)
                self.assertEqual(len(db.scalars(select(hospital_processing.HospitalSettlementObservation)).all()), 2)

        def test_grant_delegation_and_atomic_audit(self):
            from app.services.hospital_scope_service import configure_membership
            self.grant.capabilities = [*self.grant.capabilities, "access.manage"]
            target = user.User(id=2, email="target@example.invalid", hashed_password="test-only", org_id="org", is_active=True)
            self.db.add(target)
            self.db.commit()
            with self.assertRaises(HospitalAccessDenied):
                configure_membership(self.db, self.actor, "a", 2, expected_revision=0,
                                     department_ids=[self.b], capabilities=["report.read"], reason="invalid expansion")
            result = configure_membership(self.db, self.actor, "a", 2, expected_revision=0,
                                          department_ids=[self.a], capabilities=["report.read"], reason="authorized delegation")
            self.assertEqual(result.grant_revision, 1)
            self.assertEqual(len(self.db.scalars(select(audit.AuditLogModel)).all()), 1)
            self.db.rollback()
            self.assertFalse(self.db.scalars(select(hospital_membership.HospitalMembership).where(
                hospital_membership.HospitalMembership.actor_id == 2)).all())
            self.assertFalse(self.db.scalars(select(audit.AuditLogModel)).all())

        def test_legacy_replay_is_quarantined_without_new_identity(self):
            with self.sessions() as db:
                db.add(evidence.IngestionLedgerModel(id="old-ledger", clinic_id="a", connector_instance_id="conn",
                                                     resource_kind="Claim", external_record_id="claim_1", source_version="vid:v1", status="COMMITTED"))
                db.commit()
            self.assertEqual(self.submit("Claim", self.raw_claim())["quarantined"], 1)
            with self.sessions() as db:
                self.assertEqual(len(db.scalars(select(evidence.IngestionLedgerModel)).all()), 1)
                self.assertFalse(db.scalars(select(event_model.OperationalEventModel)).all())

        def test_persisted_readiness_actual_read_and_revocation(self):
            from app.services.hospital_source_service import configure_source, verify_source, assert_profile_ready
            self.grant.all_departments = True
            self.grant.capabilities = [*self.grant.capabilities, "source.configure", "source.read"]
            self.db.commit()
            connection = configure_source(self.db, self.actor, self.profile, secret_ref="env://HSP01_FIXTURE_SECRET",
                                          auth_header="Authorization", auth_prefix="Bearer ", reason="Synthetic reviewed profile")
            self.db.commit()
            with self.assertRaises(PermissionError):
                assert_profile_ready(self.db, self.profile, "Claim")
            requested = []

            def source(request):
                self.assertEqual(request.headers["Authorization"], "Bearer synthetic-test-only")
                requested.append(request.url.path)
                return httpx.Response(200, json={"records": [], "next": None})
            old_secret = os.environ.get("HSP01_FIXTURE_SECRET")
            os.environ["HSP01_FIXTURE_SECRET"] = "synthetic-test-only"
            try:
                async def verify():
                    async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as client:
                        return await verify_source(self.db, self.actor, "conn", client)
                proof = asyncio.run(verify())
                self.db.commit()
            finally:
                if old_secret is None:
                    os.environ.pop("HSP01_FIXTURE_SECRET", None)
                else:
                    os.environ["HSP01_FIXTURE_SECRET"] = old_secret
            self.assertEqual(set(proof["bindings"]), set(self.profile.endpoints))
            self.assertNotIn("/Patient", requested)
            assert_profile_ready(self.db, self.profile, "Claim")
            actor_id = self.actor.id
            self.db.rollback()
            del self.intake.hospital_authority_override
            self.intake.hospital_actor_id = actor_id
            self.assertEqual(self.submit("Claim", self.raw_claim())["accepted"], 1)
            connection = self.db.get(connector.ConnectorModel, "conn", populate_existing=True)
            connection.state = "REVOKED"
            self.db.commit()
            with self.assertRaises(PermissionError):
                self.submit("Claim", self.raw_claim(source_version="v2"))

        def test_projection_failure_rolls_back_effect_and_ack(self):
            import app.services.hospital_worker as worker_module
            self.submit("Claim", self.raw_claim())
            projection_worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="rollback-test")
            real_apply = worker_module.apply_committed

            def fail_after_effect(*args, **kwargs):
                real_apply(*args, **kwargs)
                raise RuntimeError("Injected failure before acknowledgement")
            worker_module.apply_committed = fail_after_effect
            try:
                self.assertEqual(projection_worker.run_once(), "RETRY_SCHEDULED")
            finally:
                worker_module.apply_committed = real_apply
            with self.sessions() as db:
                self.assertFalse(db.scalars(select(financial_reporting.FinancialClaimModel)).all())
                self.assertFalse(db.scalars(select(hospital_processing.HospitalProjectionReceipt)).all())
                job = db.scalars(select(event_model.OperationalEventModel)).one()
                job.next_attempt_at = date(1)
                db.commit()
            self.assertEqual(projection_worker.run_once(), "APPLIED")

        def test_intake_failure_rolls_back_evidence_ledger_and_cursor(self):
            def fail_delivery(mapper, connection, target):
                raise RuntimeError("Injected delivery failure before intake commit")
            event.listen(event_model.OperationalEventModel, "before_insert", fail_delivery)
            try:
                with self.assertRaises(RuntimeError):
                    self.submit("Claim", self.raw_claim())
            finally:
                event.remove(event_model.OperationalEventModel, "before_insert", fail_delivery)
            with self.sessions() as db:
                self.assertFalse(db.scalars(select(evidence.EvidenceModel)).all())
                self.assertFalse(db.scalars(select(evidence.IngestionLedgerModel)).all())
                self.assertIsNone(db.get(hospital_processing.HospitalCheckpoint, self.profile.cursor_binding("Claim")))

        def test_unknown_source_order_does_not_replace_current_fact(self):
            raw = self.raw_claim()
            raw.pop("source_sequence")
            self.submit("Claim", raw)
            projection_worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="ordering-test")
            self.assertEqual(projection_worker.run_once(), "APPLIED")
            raw.update(source_version="v2", billed_amount="999")
            self.submit("Claim", raw)
            self.assertEqual(projection_worker.run_once(), "DEFERRED")
            with self.sessions() as db:
                self.assertEqual(db.scalars(select(financial_reporting.FinancialClaimModel)).one().billed_amount, Decimal("100.10"))

        def test_existing_clinic_financial_regression_suite(self):
            import pytest
            import app.services.financial_reporting_service as reporting_module
            previous = reporting_module.SessionLocal
            reporting_module.SessionLocal = self.sessions
            try:
                code = pytest.main(["--noconftest", "-p", "no:cacheprovider", "-q",
                                    str(Path(__file__).with_name("test_financial_reporting_pack.py"))])
                self.assertEqual(code, 0)
            finally:
                reporting_module.SessionLocal = previous

        def test_rest_csv_equivalent_facts(self):
            raw = self.raw_claim(paid_amount="0")
            output = io.StringIO()
            import csv
            writer = csv.DictWriter(output, fieldnames=list(raw))
            writer.writeheader()
            writer.writerow(raw)
            page = next(CsvSource(self.profile).iter_pages(io.StringIO(output.getvalue()), "Claim"))
            rest = normalize_record(raw, resource_kind="Claim", context=self.profile)[0]["detail"]
            exported = normalize_record(page.records[0], resource_kind="Claim", context=self.profile)[0]["detail"]
            for facts in (rest, exported):
                facts.pop("source_digest")
            self.assertEqual(rest, exported)

        def test_csv_bounds_and_invalid_headers(self):
            with self.assertRaises(ValueError):
                list(CsvSource(self.profile).iter_pages(io.StringIO("id,id,status\na,b,SUB\n"), "Claim"))
            from dataclasses import replace
            tiny = replace(self.profile, max_bytes=80)
            with self.assertRaises(ValueError):
                list(CsvSource(tiny).iter_pages(io.StringIO('id,source_version,status\n"' + 'x\n' * 100 + '",v1,SUB\n'), "Claim"))

        def test_duplicate_collision_and_durable_checkpoint(self):
            self.assertEqual(self.submit("Claim", self.raw_claim())["accepted"], 1)
            self.assertEqual(self.submit("Claim", self.raw_claim())["duplicate"], 1)
            self.assertEqual(self.submit("Claim", self.raw_claim(billed_amount="999"))["quarantined"], 1)
            with self.sessions() as db:
                self.assertEqual(len(db.scalars(select(evidence.IngestionLedgerModel)).all()), 1)
                self.assertEqual(len(db.scalars(select(event_model.OperationalEventModel)).all()), 1)
                self.assertIsNotNone(db.get(hospital_processing.HospitalCheckpoint, self.profile.cursor_binding("Claim")))

        def test_projection_replay_and_scoped_snapshot(self):
            self.submit("Claim", self.raw_claim())
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="test")
            self.assertEqual(worker.run_once(), "APPLIED")
            for index, department in enumerate((self.a, self.b), 1):
                self.submit("ClaimLine", {"id": str(index), "claim_id": "claim_1", "source_version": "v1", "status": "DEN",
                                          "source_sequence": 1, "department_id": department, "attribution_basis": "source:service", "procedure_code": "TEST",
                                          "code_system": "test", "code_version": "1", "units": 1, "billed_amount": "50.05", "paid_amount": "0", "currency": "AED"})
                self.assertEqual(worker.run_once(), "APPLIED")
            self.assertEqual(worker.run_once(), "IDLE")
            scope = resolve_hospital_scope(self.db, self.actor, {"clinic_id": "a"})
            report = FinancialReportingService().generate_hospital_summary(self.db, scope, date(1), date(10), "AED")
            self.assertEqual(report["metrics"]["line_count"], 1)
            self.assertEqual(Decimal(report["metrics"]["known_billed_amount"]), Decimal("50.05"))
            self.assertNotIn("claim_id", report["rows"][0])
            self.assertIsNone(report["metrics"]["facility_total"])
            snapshot = FinancialReportingService().freeze_hospital_summary(self.db, scope, date(1), date(10), "AED")
            again = FinancialReportingService().freeze_hospital_summary(self.db, scope, date(1), date(10), "AED")
            self.assertEqual(snapshot.snapshot_id, again.snapshot_id)
            self.db.commit()

        def test_expired_lease_cannot_commit(self):
            self.submit("Claim", self.raw_claim())
            one = HospitalWorker(self.sessions, lambda *args: None, worker_id="one")
            claim = one.claim()
            with self.sessions() as db:
                row = db.get(event_model.OperationalEventModel, claim[0])
                row.lease_until = date(1)
                db.commit()
            two = HospitalWorker(self.sessions, lambda *args: None, worker_id="two")
            second = two.claim()
            self.assertEqual(one.process(claim), "STALE_LEASE")
            self.assertEqual(two.process(second), "APPLIED")

        @unittest.skipUnless(os.getenv("HSP01_TEST_DATABASE_URL"), "Requires dedicated disposable PostgreSQL")
        def test_postgresql_competing_deliveries_and_workers(self):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self.submit("Claim", self.raw_claim()), range(2)))
            self.assertEqual(sum(item["accepted"] for item in results), 1)
            self.assertEqual(sum(item["duplicate"] for item in results), 1)
            workers = [HospitalWorker(self.sessions, lambda *args: None, worker_id=f"worker-{index}") for index in range(2)]
            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(executor.map(lambda worker: worker.claim(), workers))
            self.assertEqual(sum(claim is not None for claim in claims), 1)
            for worker_instance, claim in zip(workers, claims):
                if claim is not None:
                    self.assertEqual(worker_instance.process(claim), "APPLIED")
            with self.sessions() as db:
                self.assertEqual(len(db.scalars(select(hospital_processing.HospitalProjectionReceipt)).all()), 1)

        @unittest.skipUnless(os.getenv("HSP01_TEST_DATABASE_URL"), "Requires dedicated disposable PostgreSQL")
        def test_postgresql_expiry_during_projection_never_publishes(self):
            from concurrent.futures import ThreadPoolExecutor
            import app.services.hospital_worker as worker_module
            import time
            self.submit("Claim", self.raw_claim())
            one = HospitalWorker(self.sessions, lambda *args: None, worker_id="slow-worker", lease_seconds=1)
            two = HospitalWorker(self.sessions, lambda *args: None, worker_id="takeover-worker")
            claim = one.claim()
            real_apply = worker_module.apply_committed

            def slow(*args, **kwargs):
                result = real_apply(*args, **kwargs)
                time.sleep(1.2)
                return result
            worker_module.apply_committed = slow
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(one.process, claim)
                    self.assertEqual(future.result(timeout=10), "STALE_LEASE")
            finally:
                worker_module.apply_committed = real_apply
            self.assertEqual(two.run_once(), "APPLIED")

        def test_out_of_order_source_does_not_regress(self):
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="test")
            self.submit("Claim", self.raw_claim(source_version="v2", source_sequence=2, billed_amount="200"))
            self.assertEqual(worker.run_once(), "APPLIED")
            self.submit("Claim", self.raw_claim())
            self.assertEqual(worker.run_once(), "HISTORICAL")
            with self.sessions() as db:
                self.assertEqual(db.scalars(select(financial_reporting.FinancialClaimModel)).one().billed_amount, Decimal("200"))

        def test_missing_parent_defers_without_receipt(self):
            self.submit("ClaimLine", {"id": "1", "claim_id": "missing", "source_version": "v1", "status": "DEN"})
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="test")
            self.assertEqual(worker.run_once(), "DEFERRED")
            with self.sessions() as db:
                self.assertFalse(db.scalars(select(hospital_processing.HospitalProjectionReceipt)).all())

        def test_assignment_versions_and_no_authority_change(self):
            rec = governance_storage.RecommendationModel(recommendation_id="rec", decision_context_id="ctx", rule_evaluation_id="eval",
                                                         journey_id="journey", mapping_id="m", mapping_version="1", status="ACTIVE")
            self.db.add(rec)
            self.db.commit()
            service = WorkAssignmentService(self.db, lambda db, rec: {"org_id": "org", "clinic_id": "a", "department_id": self.a})
            self.assertEqual(service.assign(self.actor, {"clinic_id": "a"}, "rec", expected_version=0, assignee="team-user", reason="triage"), 1)
            self.db.commit()
            self.assertEqual(service.assign(self.actor, {"clinic_id": "a"}, "rec", expected_version=1, team="billing", reason="reassign"), 2)
            with self.assertRaises(AssignmentConflict):
                service.assign(self.actor, {"clinic_id": "a"}, "rec", expected_version=1, reason="stale")
            self.assertEqual(rec.status, "ACTIVE")
            self.assertEqual(len(self.db.scalars(select(work_assignment.WorkAssignmentHistory)).all()), 2)

        def test_api_scope_snapshot_export_and_revocation(self):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            # Substitute authentication only. Route logic, models and report
            # service are the actual HSP01 implementation.
            dependencies = types.ModuleType("app.api.deps")

            def actor_dependency():
                return self.actor

            def db_dependency():
                with self.sessions() as db:
                    yield db
            dependencies.get_current_user = actor_dependency
            sys.modules["app.api.deps"] = dependencies
            database.get_db = db_dependency
            import importlib
            hospital_api = importlib.import_module("app.api.v1.endpoints.hospital")
            app = FastAPI()
            app.include_router(hospital_api.router, prefix="/hospital")
            app.dependency_overrides[hospital_api.get_current_user] = actor_dependency
            app.dependency_overrides[hospital_api.get_db] = db_dependency
            # FastAPI imports are cached across tests; override exact callables.
            with TestClient(app) as client:
                directory_response = client.get("/hospital/directory")
                self.assertEqual(directory_response.status_code, 200)
                self.assertEqual(len(directory_response.json()["facilities"][0]["departments"]), 1)
                self.assertEqual(directory_response.json()["facilities"][0]["departments"][0]["name"], "Cardiology")
                denied = client.get("/hospital/summary", params={"clinic_id": "a", "department_id": self.b})
                self.assertEqual(denied.status_code, 403)
                body = {"clinic_id": "a", "currency": "AED", "start": date(1).isoformat(), "end": date(10).isoformat()}
                frozen = client.post("/hospital/snapshots", json=body)
                self.assertEqual(frozen.status_code, 200, frozen.text)
                snapshot_id = frozen.json()["snapshot_id"]
                exported = client.get(f"/hospital/snapshots/{snapshot_id}/csv")
                self.assertEqual(exported.status_code, 200)
                self.assertIn("snapshot_id,currency,line_id", exported.text)
                self.grant.is_active = False
                self.db.commit()
                self.assertEqual(client.get(f"/hospital/snapshots/{snapshot_id}/csv").status_code, 403)

        def test_forward_migration_preserves_legacy_ids_and_audits_money(self):
            import importlib.util
            from sqlalchemy import MetaData, Table, Column, Float, Numeric, inspect, ForeignKey
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            migration_path = Path(__file__).resolve().parents[2] / "alembic/versions/hsp01_hospital_extension.py"
            spec = importlib.util.spec_from_file_location("hsp01_migration_under_test", migration_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            engine = create_engine("sqlite://")

            @event.listens_for(engine, "connect")
            def enforce_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.isolation_level = None

            @event.listens_for(engine, "begin")
            def explicit_begin(connection):
                connection.exec_driver_sql("BEGIN")
            old_meta = MetaData()
            current = financial_reporting.FinancialClaimModel.__table__
            added = {"org_id", "source_evidence_id", "source_sequence", "tombstone"}
            old_claims = Table("financial_claims", old_meta, *[
                Column(column.name, Float() if isinstance(column.type, Numeric) else column.type,
                       nullable=column.nullable, primary_key=column.primary_key)
                for column in current.columns if column.name not in added])
            excluded = {"clinic_id", "department_id", "attribution_basis", "attribution_version_id", "attribution_time",
                        "currency", "source_evidence_id", "source_sequence", "tombstone"}
            for source in (financial_reporting.FinancialClaimLineModel.__table__, financial_reporting.FinancialSubmissionAttemptModel.__table__):
                columns = []
                for column in source.columns:
                    if column.name in excluded:
                        continue
                    args = [ForeignKey("financial_claims.claim_id")] if column.name == "claim_id" else []
                    columns.append(Column(column.name, Float() if isinstance(column.type, Numeric) else column.type,
                                          *args, nullable=column.nullable, primary_key=column.primary_key))
                Table(source.name, old_meta, *columns)
            with engine.begin() as connection:
                old_meta.create_all(connection)
                database.Base.metadata.create_all(connection)
                connection.execute(old_claims.insert().values(claim_id="legacy-claim", clinic_id="legacy-clinic",
                                                              patient_id="p", provider_id="provider", payer_id="payer", status="paid", billed_amount=100.125,
                                                              currency="AED", service_date=date(1)))
                connection.execute(old_meta.tables["financial_claim_lines"].insert().values(
                    line_id="legacy-line", claim_id="legacy-claim", procedure_code="TEST", code_system="TEST",
                    code_version="1", units=1, billed_amount=100.125, paid_amount=0, line_status="submitted"))
                connection.execute(old_meta.tables["financial_submission_attempts"].insert().values(
                    attempt_id="legacy-attempt", claim_id="legacy-claim", attempt_number=1,
                    submission_date=date(2), outcome="accepted"))
                connection.execute(clinic.ClinicModel.__table__.insert().values(id="legacy-clinic", name="Same Name"))
                connection.execute(organization.OrganizationModel.__table__.insert().values(id="org", name="Org"))
                connection.execute(organization.OrganizationClinicModel.__table__.insert().values(id="different-id", organization_id="org", name="Same Name"))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
                value = connection.execute(select(current.c.claim_id, current.c.billed_amount)).one()
                self.assertEqual(value.claim_id, "legacy-claim")
                self.assertEqual(value.billed_amount, Decimal("100.125"))
                crosswalk = connection.execute(select(hospital_processing.FacilityCrosswalk.__table__)).one()
                self.assertEqual(crosswalk.status, "UNRESOLVED")
                self.assertIsNone(crosswalk.clinic_id)
                audit = Table("hospital_legacy_money_audit", MetaData(), autoload_with=connection)
                self.assertIn("100.125", connection.execute(select(audit.c.legacy_value_text)).scalars().all())
                self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
                self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
                self.assertEqual(connection.exec_driver_sql("SELECT claim_id FROM financial_claim_lines WHERE line_id='legacy-line'").scalar(), "legacy-claim")
                self.assertEqual(connection.exec_driver_sql("SELECT claim_id FROM financial_submission_attempts WHERE attempt_id='legacy-attempt'").scalar(), "legacy-claim")
                self.assertTrue(any(item["name"] == "source_sequence" for item in inspect(connection).get_columns("financial_claims")))
            engine.dispose()

        def test_auditor_currency_backfill_uses_line_evidence_not_parent(self):
            import importlib.util
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            self.submit("Claim", self.raw_claim())
            worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="backfill")
            self.assertEqual(worker.run_once(), "APPLIED")
            self.submit("ClaimLine", self.line_raw())
            self.assertEqual(worker.run_once(), "APPLIED")
            row = self.db.scalars(select(financial_reporting.FinancialClaimLineModel)).one()
            row.currency = None
            parent = self.db.get(financial_reporting.FinancialClaimModel, row.claim_id)
            parent.currency = "USD"
            line_id = row.line_id
            self.db.commit()
            migration_path = Path(__file__).resolve().parents[2] / "alembic/versions/hsp01_auditor_corrections.py"
            spec = importlib.util.spec_from_file_location("hsp01_auditor_migration", migration_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with self.engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            self.db.expire_all()
            self.assertEqual(self.db.get(financial_reporting.FinancialClaimLineModel, line_id).currency, "AED")
            self.assertEqual(self.db.get(financial_reporting.FinancialClaimModel, parent.claim_id).currency, "USD")

        def test_rest_empty_pages_tokens_and_profile_change(self):
            calls = []

            def respond(request):
                calls.append(request)
                return httpx.Response(200, json={"records": [], "next": "opaque" if len(calls) == 1 else None})

            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    source = RestSource(self.profile, client)
                    pages = [page async for page in source.iter_pages("Claim")]
                    self.assertEqual(len(pages), 2)
                    with self.assertRaises(ValueError):
                        _ = [page async for page in source.iter_pages("Claim", checkpoint={"binding": "wrong", "token": "x"})]
            asyncio.run(run())

        def test_rest_redirect_and_repeated_token_fail(self):
            async def run():
                for response in (httpx.Response(302, headers={"Location": "https://other.invalid"}),
                                 httpx.Response(200, json={"records": [], "next": "same"})):
                    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                        with self.assertRaises(ValueError):
                            _ = [page async for page in RestSource(self.profile, client).iter_pages("Claim")]
            asyncio.run(run())

        def test_page_window_is_partial_and_cursors_are_transport_separate(self):
            from dataclasses import replace
            bounded = replace(self.profile, max_pages_per_run=1)
            self.assertNotEqual(bounded.cursor_binding("Claim", "rest"), bounded.cursor_binding("Claim", "csv"))

            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                                                                           httpx.Response(200, json={"records": [], "next": "opaque-more"}))) as client:
                    adapter = HospitalReadAdapter(bounded, client, authorize=self.intake._hospital_authority, intake=self.intake)
                    result = await adapter.sync()
                    self.assertEqual(result["status"], "PARTIAL")
                    self.assertEqual(self.intake.get_hospital_checkpoint(bounded, "Claim")["token"], "opaque-more")
            asyncio.run(run())

        def test_transport_failure_retains_actual_counts_and_checkpoint(self):
            calls = []

            def source(request):
                calls.append(request)
                if len(calls) == 1:
                    return httpx.Response(200, json={"records": [self.raw_claim()], "next": "next-page"})
                return httpx.Response(503)

            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as client:
                    adapter = HospitalReadAdapter(self.profile, client, authorize=self.intake._hospital_authority, intake=self.intake)
                    result = await adapter.sync()
                    self.assertEqual(result["status"], "PARTIAL")
                    self.assertEqual(result["accepted"], 1)
                    self.assertTrue(result["unaccounted_range"])
                    self.assertEqual(self.intake.get_hospital_checkpoint(self.profile, "Claim")["token"], "next-page")
            asyncio.run(run())

        def test_csv_checkpoint_cannot_hide_changed_rest_profile(self):
            from dataclasses import replace
            for transport in ("csv", "rest"):
                self.intake.submit_hospital_page(self.profile, "Claim", [], [], {
                    "binding": self.profile.cursor_binding("Claim", transport), "token": None, "transport": transport})
            changed = replace(self.profile, revision="test-v2")
            with self.assertRaises(ValueError):
                self.intake.get_hospital_checkpoint(changed, "Claim")
            approved = replace(changed, resync_basis_ref="fixture:approved-resync")
            self.assertIsNone(self.intake.get_hospital_checkpoint(approved, "Claim"))

        def test_paged_snapshot_csv_matches_all_frozen_rows(self):
            self.submit("Claim", self.raw_claim(billed_amount="150"))
            projection_worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="paging")
            self.assertEqual(projection_worker.run_once(), "APPLIED")
            records = []
            for index in range(150):
                records.extend(normalize_record({"id": str(index), "claim_id": "claim_1", "source_version": "v1", "status": "DEN",
                                                 "source_sequence": 1, "department_id": self.a, "attribution_basis": "fixture:line",
                                                 "procedure_code": "T", "code_system": "test", "code_version": "1", "units": 1,
                                                 "billed_amount": "1", "currency": "AED"}, resource_kind="ClaimLine", context=self.profile))
            self.intake.submit_hospital_page(self.profile, "ClaimLine", records, [],
                                             {"binding": self.profile.cursor_binding("ClaimLine"), "token": None})
            for _ in range(150):
                self.assertEqual(projection_worker.run_once(), "APPLIED")
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from app.api.v1.endpoints import hospital as hospital_api

            def current_actor():
                return self.actor

            def current_db():
                with self.sessions() as db:
                    yield db
            app = FastAPI()
            app.include_router(hospital_api.router, prefix="/hospital")
            app.dependency_overrides[hospital_api.get_current_user] = current_actor
            app.dependency_overrides[hospital_api.get_db] = current_db
            with TestClient(app) as client:
                result = client.post("/hospital/snapshots", json={"clinic_id": "a", "currency": "AED",
                                                                  "start": date(1).isoformat(), "end": date(10).isoformat()})
                self.assertEqual(result.status_code, 200, result.text)
                payload = result.json()
                self.assertEqual(len(payload["rows"]), 100)
                self.assertTrue(payload["has_more"])
                self.assertEqual(payload["metrics"]["line_count"], 150)
                exported = client.get(f'/hospital/snapshots/{payload["snapshot_id"]}/csv')
                self.assertEqual(exported.status_code, 200, exported.text)
                import csv
                rows = list(csv.DictReader(io.StringIO(exported.text)))
                self.assertEqual(len(rows), 150)
                self.assertEqual(sum(Decimal(row["billed_amount"]) for row in rows), Decimal("150"))

        @unittest.skipUnless(os.getenv("HSP01_RUN_LOAD") == "1", "Run explicitly with HSP01_RUN_LOAD=1")
        def test_bounded_hospital_load_replay_and_changed_records(self):
            import time
            import tracemalloc
            import csv
            import platform
            from sqlalchemy import func
            third = HospitalDirectoryService(self.db).create_department("org", "a", "ORTH", "Orthopedics", date(1), "fixture:v1").id
            self.db.commit()
            departments = [self.a, self.b, third]
            measurements = {"pages": 0, "largest_page_rows": 0, "largest_page_bytes": 0, "sql_statements": 0}

            def sql_count(*args):
                measurements["sql_statements"] += 1
            event.listen(self.engine, "before_cursor_execute", sql_count)

            def line_raw(index, version="v1", sequence=1):
                return {"id": str(index % 5 + 1), "claim_id": f"load-{index // 5}", "source_version": version,
                        "status": "DEN", "source_sequence": sequence, "department_id": departments[index % 3],
                        "attribution_basis": "fixture:service-line", "procedure_code": "TEST", "code_system": "test",
                        "code_version": "1", "units": 1, "billed_amount": "20.02", "currency": "AED"}

            def source(request):
                kind = request.url.path.strip("/")
                start = int(request.url.params.get("cursor", "0"))
                size = int(request.url.params["limit"])
                count = 1000 if kind == "Claim" else 5000 if kind == "ClaimLine" else 0
                stop = min(start + size, count)
                records = [self.raw_claim(id=f"load-{index}") if kind == "Claim" else line_raw(index)
                           for index in range(start, stop)]
                payload = {"records": records, "next": str(stop) if stop < count else None}
                byte_count = len(json.dumps(payload).encode())
                measurements["pages"] += 1
                measurements["largest_page_rows"] = max(measurements["largest_page_rows"], len(records))
                measurements["largest_page_bytes"] = max(measurements["largest_page_bytes"], byte_count)
                return httpx.Response(200, json=payload)

            async def import_source():
                async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as client:
                    adapter = HospitalReadAdapter(self.profile, client, authorize=self.intake._hospital_authority, intake=self.intake)
                    return await adapter.sync()
            tracemalloc.start()
            started = time.perf_counter()
            receipt = asyncio.run(import_source())
            self.assertEqual(receipt["accepted"], 6000)
            projection_worker = HospitalWorker(self.sessions, lambda *args: None, worker_id="load-worker")
            projected = 0
            while True:
                result = projection_worker.run_once()
                if result == "IDLE":
                    break
                self.assertEqual(result, "APPLIED")
                projected += 1
            self.assertEqual(projected, 6000)
            replay = asyncio.run(import_source())
            self.assertEqual(replay["duplicate"], 6000)
            changed = [line_raw(index, "v2", 2) for index in range(50)]
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(changed[0]))
            writer.writeheader()
            writer.writerows(changed)

            async def import_changes():
                async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as client:
                    adapter = HospitalReadAdapter(self.profile, client, authorize=self.intake._hospital_authority, intake=self.intake)
                    return await adapter.import_csv(io.StringIO(output.getvalue()), "ClaimLine")
            changed_at = time.perf_counter()
            self.assertEqual(asyncio.run(import_changes())["accepted"], 50)
            delays = []
            for index in range(50):
                self.assertEqual(projection_worker.run_once(), "APPLIED")
                delays.append(time.perf_counter() - changed_at)
            self.assertEqual(projection_worker.run_once(), "IDLE")
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(financial_reporting.FinancialClaimModel)), 1000)
                self.assertEqual(db.scalar(select(func.count()).select_from(financial_reporting.FinancialClaimLineModel)), 5000)
                failed = db.scalar(select(func.count()).select_from(event_model.OperationalEventModel).where(
                    event_model.OperationalEventModel.state != "Completed"))
                self.assertEqual(failed, 0)
            event.remove(self.engine, "before_cursor_execute", sql_count)
            self.assertLessEqual(measurements["largest_page_rows"], 500)
            self.assertLessEqual(measurements["largest_page_bytes"], 2 * 1024 * 1024)
            measurements.update({"claims": 1000, "lines": 5000, "changed_records": 50,
                                 "elapsed_seconds": time.perf_counter() - started, "peak_python_bytes": peak,
                                 "burst_to_projection_p95_seconds": sorted(delays)[47], "failed_deferred_unfinished": failed,
                                 "database": "PostgreSQL disposable" if self.pg_schema else "SQLite isolated",
                                 "platform": platform.platform(), "commit_to_ui_p95": "NOT_MEASURED",
                                 "capacity_claim": False})
            output_path = os.getenv("HSP01_METRICS_PATH")
            if output_path:
                Path(output_path).write_text(json.dumps(measurements, indent=2), encoding="utf-8")
            print(json.dumps(measurements, sort_keys=True))

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HospitalTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use pytest or --worker")
    worker()
