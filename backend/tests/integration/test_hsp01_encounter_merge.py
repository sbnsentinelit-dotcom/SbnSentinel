"""No-commit main/HSP01 integration regression; synthetic in-memory DB only."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.fixture(autouse=True)
def clear_rate_limits():
    return None


def test_isolated_encounter_merge():
    result = subprocess.run([sys.executable, "-B", "-I", str(Path(__file__).resolve()), "--worker"],
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def worker():
    import types
    import unittest
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/runtime"))
    state = {}
    database = types.ModuleType("app.db.database")
    database.Base = declarative_base()
    database.SessionLocal = sessionmaker()

    def get_db():
        with state["sessions"]() as db:
            yield db
    database.get_db = get_db
    sys.modules["app.db.database"] = database
    dependencies = types.ModuleType("app.api.deps")
    dependencies.get_current_user = lambda: types.SimpleNamespace(id=1, clinic_id="clinic-a", org_id="org", is_active=True)
    sys.modules["app.api.deps"] = dependencies
    seed = types.ModuleType("app.services.dubai_seed_service")
    seed.auto_seed_dubai_if_empty = lambda db: state["seed"](db)
    sys.modules["app.services.dubai_seed_service"] = seed
    from app.models import organization, clinic, encounter, hospital_dimensions, hospital_processing
    from app.models import user, evidence, event as events, financial_reporting, hospital_membership  # noqa: F401 -- ORM metadata
    from app.models import signal, intelligence, governance_storage, work_assignment  # noqa: F401 -- ORM metadata
    from app.api.v1.endpoints import encounters
    from app.services.hospital_legacy_guard import hospital_legacy_guard

    class MergeTests(unittest.TestCase):
        def setUp(self):
            self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

            @event.listens_for(self.engine, "connect")
            def foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
            database.Base.metadata.create_all(self.engine)
            self.sessions = sessionmaker(self.engine)
            state.update(sessions=self.sessions, seed=lambda db: None)
            with self.sessions() as db:
                db.add(organization.OrganizationModel(id="org", name="Synthetic"))
                db.flush()
                for ident in ("clinic-a", "clinic-b", "hospital-a", "hospital-empty"):
                    db.add(organization.OrganizationClinicModel(id=ident, organization_id="org", name=ident))
                db.add(clinic.ClinicModel(id="hospital-alias", name="Synthetic alias"))
                db.flush()
                for ident in ("hospital-a", "hospital-empty"):
                    db.add(hospital_dimensions.HospitalFacilityEnrollment(clinic_id=ident, basis_ref="synthetic"))
                db.add(hospital_processing.FacilityCrosswalk(legacy_clinic_id="hospital-alias", clinic_id="hospital-a",
                                                             status="RESOLVED", basis_ref="synthetic reviewed alias"))
                for ident, facility, patient, status, billing, amount in [
                    ("a1", "clinic-a", "Alice", "Waiting", "Paid", 10),
                    ("a2", "clinic-a", "Alice", "Completed", "Pending", 20),
                    ("a3", "clinic-a", "Bob", "Completed", "Paid", 0),
                    ("b1", "clinic-b", "Alice", "Completed", "Denied", 90),
                    ("n1", None, "Unbound", "Completed", "Pending", None),
                    ("h1", "hospital-a", "Alice", "Waiting", "Paid", 999),
                    ("h2", "hospital-alias", "Alice", "Completed", "Paid", 888),
                    ("h3", "hospital-empty", "Alice", "Completed", "Paid", 777),
                ]:
                    self.add(db, ident, facility, patient, status, billing, amount)
                db.commit()
            app = FastAPI()
            app.include_router(encounters.router, prefix="/encounters")
            protected = FastAPI()
            protected.include_router(encounters.router, prefix="/encounters", dependencies=[Depends(hospital_legacy_guard)])
            self.client, self.protected = TestClient(app), TestClient(protected)

        @staticmethod
        def add(db, ident, facility, patient="Alice", status="Waiting", billing="Paid", amount=5):
            db.add(encounter.EncounterModel(id=ident, clinic_id=facility, patient_id=patient, provider_id="Fixture provider",
                                            date="2026-01-02", type="Consultation", diagnosis="Synthetic diagnosis",
                                            status=status, billing_status=billing, billing_amount=amount))

        def tearDown(self):
            self.client.close()
            self.protected.close()
            self.engine.dispose()

        def test_default_full_list_and_both_slash_forms_exclude_hospitals(self):
            for path in ("/encounters", "/encounters/"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual({row["id"] for row in response.json()}, {"a1", "a2", "a3", "b1", "n1"})
                self.assertEqual(response.headers["X-Total-Count"], "5")

        def test_pages_and_count_headers_use_filtered_hospital_safe_query(self):
            response = self.client.get("/encounters", params={"search": "Alice", "page": 2, "page_size": 1, "clinic_id": "clinic-a"})
            self.assertEqual([row["id"] for row in response.json()], ["a2"])
            self.assertEqual(response.headers["X-Total-Count"], "2")
            self.assertEqual(response.headers["X-Total-Pages"], "2")
            self.assertIn("X-Total-Count", response.headers["Access-Control-Expose-Headers"])

        def test_status_billing_and_skip_limit(self):
            response = self.client.get("/encounters", params={"status": "Completed", "billing_status": "Paid"})
            self.assertEqual([row["id"] for row in response.json()], ["a3"])
            response = self.client.get("/encounters/", params={"skip": 1, "limit": 1, "clinic_id": "clinic-a"})
            self.assertEqual([row["id"] for row in response.json()], ["a2"])
            self.assertEqual(response.headers["X-Total-Count"], "3")

        def test_fallback_reapplies_filters_pages_and_hospital_exclusion(self):
            calls = []

            def seed_fixtures(db):
                calls.append(True)
                self.add(db, "z1", "clinic-a", "Fallback", "Completed", "Paid")
                self.add(db, "z2", "clinic-a", "Fallback", "Completed", "Paid")
                self.add(db, "z3", "hospital-a", "Fallback", "Completed", "Paid")
                self.add(db, "z4", "clinic-b", "Fallback", "Completed", "Paid")
                self.add(db, "z5", "clinic-a", "Fallback", "Waiting", "Paid")
                db.commit()
            state["seed"] = seed_fixtures
            response = self.client.get("/encounters", params={"search": "Fallback", "clinic_id": "clinic-a",
                                       "status": "Completed", "billing_status": "Paid", "page": 2, "page_size": 1})
            self.assertEqual(len(calls), 1)
            self.assertEqual([row["id"] for row in response.json()], ["z2"])
            self.assertEqual(response.headers["X-Total-Count"], "2")
            self.assertEqual(response.headers["X-Total-Pages"], "2")

        def test_missing_search_and_out_of_range_pages_do_not_expand_scope(self):
            for params in ({"search": "Absent"}, {"clinic_id": "missing"}, {"clinic_id": "clinic-a", "page": 99}):
                response = self.client.get("/encounters", params=params)
                self.assertEqual(response.json(), [])
            self.assertEqual(response.headers["X-Total-Count"], "3")

        def test_summary_default_fallback_uses_same_safe_cohort_for_amounts_counts(self):
            data = self.client.get("/encounters/summary-stats").json()
            self.assertEqual(data, {"total_encounters": 5, "total_charges": 120.0, "total_paid": 10.0,
                                    "paid_count": 2, "pending_count": 2, "denied_count": 1})

        def test_explicit_summary_filter_zero_and_missing_do_not_use_demo_amounts(self):
            data = self.client.get("/encounters/summary-stats", params={"clinic_id": "clinic-a"}).json()
            self.assertEqual(data["total_encounters"], 3)
            self.assertEqual(data["total_charges"], 30.0)
            for target in ("missing", "hospital-a", "hospital-empty", "hospital-alias"):
                data = self.client.get("/encounters/summary-stats", params={"clinic_id": target}).json()
                self.assertTrue(all(value == 0 for value in data.values()))
            with self.sessions() as db:
                db.query(encounter.EncounterModel).filter(encounter.EncounterModel.clinic_id == "clinic-a").update({"billing_amount": 0})
                db.commit()
            data = self.client.get("/encounters/summary-stats", params={"clinic_id": "clinic-a"}).json()
            self.assertEqual(data["total_charges"], 0)
            self.assertEqual(data["total_paid"], 0)

        def test_hospital_guard_blocks_list_summary_and_alias(self):
            for target in ("hospital-a", "hospital-empty", "hospital-alias"):
                for path in ("/encounters/", "/encounters/summary-stats"):
                    response = self.protected.get(path, params={"clinic_id": target})
                    self.assertEqual(response.status_code, 403, response.text)
            response = self.protected.get("/encounters/", params={"clinic_id": "clinic-a", "limit": 1})
            self.assertEqual(response.status_code, 200)

        def test_invalid_pagination_is_rejected(self):
            for params in ({"page": 0}, {"page_size": 501}, {"limit": 501}, {"skip": -1}):
                self.assertEqual(self.client.get("/encounters/", params=params).status_code, 422)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MergeTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use pytest or --worker")
    worker()
