"""H01 isolated database tests.

Run from backend with:
python -B -m pytest --noconftest -p no:cacheprovider tests/integration/test_hospital_directory.py

The worker substitutes only the side-effectful database module with a fresh
declarative Base before importing the real organization and H01 code. Neither
application startup nor the repository's global seed fixtures are executed.
These tests do not replace PostgreSQL migration/concurrency acceptance.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest
import pytest
from datetime import datetime, timezone, timedelta


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Override the shared seed fixture; this suite owns its subprocess database."""
    return None


def test_isolated_hospital_directory():
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", "-I", str(Path(__file__).resolve()), "--isolated-worker"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def run_worker():
    import types
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import declarative_base, Session
    from sqlalchemy.exc import IntegrityError

    runtime = Path(__file__).resolve().parents[2] / "src" / "runtime"
    sys.path.insert(0, str(runtime))
    database = types.ModuleType("app.db.database")
    database.Base = declarative_base()
    sys.modules["app.db.database"] = database
    from app.models.organization import OrganizationModel, OrganizationClinicModel
    from app.models.hospital_dimensions import (
        HospitalDepartment, HospitalDepartmentVersion, HospitalProviderMembership,
        HospitalLocationMembership,
    )
    from app.services.hospital_directory_service import HospitalDirectoryService

    def day(n):
        return datetime(2026, 1, n, tzinfo=timezone.utc)

    class DirectoryTests(unittest.TestCase):
        def setUp(self):
            self.engine = create_engine("sqlite+pysqlite:///:memory:")

            @event.listens_for(self.engine, "connect")
            def foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
            database.Base.metadata.create_all(self.engine)
            self.db = Session(self.engine)
            self.db.add_all([OrganizationModel(id="org", name="Organization"),
                             OrganizationModel(id="other", name="Other")])
            self.db.flush()
            self.db.add_all([OrganizationClinicModel(id="a", organization_id="org", name="A"),
                             OrganizationClinicModel(id="b", organization_id="org", name="B"),
                             OrganizationClinicModel(id="c", organization_id="other", name="C")])
            self.db.commit()
            self.service = HospitalDirectoryService(self.db)

        def tearDown(self):
            self.db.close()
            self.engine.dispose()

        def department(self, clinic="a", code="CARD"):
            return self.service.create_department("org", clinic, code, "Cardiology", day(1), "source:directory:v1")

        def test_facility_and_organization_isolation(self):
            a = self.department()
            b = self.department("b")
            self.assertNotEqual(a.id, b.id)
            self.assertEqual([a.id], [d.id for d in self.service.list_departments("org", "a")])
            with self.assertRaises(ValueError):
                self.service.list_departments("org", "c")
            with self.assertRaises(ValueError):
                self.service.resolve_department("org", "a", b.id, day(1))

        def test_rename_boundary_and_session_reopen(self):
            department = self.department()
            key = department.id
            self.service.rename_department("org", "a", key, "Heart Care", day(10), "source:rename:v2")
            self.db.commit()
            self.db.close()
            self.db = Session(self.engine)
            self.service = HospitalDirectoryService(self.db)
            old = self.service.resolve_department("org", "a", key, day(9))
            new = self.service.resolve_department("org", "a", key, day(10))
            self.assertEqual(old.records[0].name, "Cardiology")
            self.assertEqual(new.records[0].name, "Heart Care")
            self.assertEqual(old.records[0].basis_ref, "source:directory:v1")
            self.assertEqual(new.records[0].basis_ref, "source:rename:v2")

        def test_location_transfer_and_cross_facility_rejection(self):
            a = self.department()
            second = self.department(code="RAD")
            other = self.department("b")
            room = self.service.create_location("org", "a", "R1", "Room One")
            with self.assertRaises(ValueError):
                self.service.add_location_membership("org", "b", room.id, other.id, day(1), "source:x")
            self.service.add_location_membership("org", "a", room.id, a.id, day(1), "source:old", day(10))
            self.service.add_location_membership("org", "a", room.id, second.id, day(10), "source:new")
            self.assertEqual(self.service.resolve_location("org", "a", room.id, day(9)).records[0].department_id, a.id)
            self.assertEqual(self.service.resolve_location("org", "a", room.id, day(10)).records[0].department_id, second.id)
            with self.assertRaises(ValueError):
                self.service.add_location_membership("org", "a", room.id, a.id, day(5), "source:overlap")

        def test_multiple_provider_memberships_are_not_guessed(self):
            a = self.department()
            b = self.department(code="RAD")
            for department in (a, b):
                self.service.add_provider_membership("org", "a", "qualified:provider:1", department.id, day(1), "source:p")
            result = self.service.resolve_provider("org", "a", "qualified:provider:1", day(2))
            self.assertEqual(result.status, "AMBIGUOUS")
            self.assertEqual(len(result.records), 2)
            self.assertEqual(self.service.resolve_provider("org", "b", "qualified:provider:1", day(2)).status, "UNKNOWN")

        def test_provider_transfer(self):
            a = self.department()
            b = self.department(code="RAD")
            self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), "source:old", day(10))
            self.service.add_provider_membership("org", "a", "qualified:p", b.id, day(10), "source:new")
            self.assertEqual(self.service.resolve_provider("org", "a", "qualified:p", day(9)).records[0].department_id, a.id)
            self.assertEqual(self.service.resolve_provider("org", "a", "qualified:p", day(10)).records[0].department_id, b.id)

        def test_unknown_and_missing_time(self):
            a = self.department()
            self.assertEqual(self.service.resolve_department("org", "a", None, day(2)).status, "UNKNOWN")
            self.assertEqual(self.service.resolve_department("org", "a", a.id, None).status, "UNKNOWN")
            self.assertEqual(self.service.resolve_provider("org", "a", None, day(2)).status, "UNKNOWN")
            self.assertEqual(self.service.resolve_location("org", "a", None, day(2)).status, "UNKNOWN")

        def test_invalid_periods_and_missing_basis(self):
            a = self.department()
            for start, end in ((day(5), day(5)), (day(5), day(4)), (datetime(2026, 1, 1), None)):
                with self.assertRaises(ValueError):
                    self.service.add_provider_membership("org", "a", "qualified:p", a.id, start, "source:p", end)
            with self.assertRaises(ValueError):
                self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), " ")
            with self.assertRaises(ValueError):
                self.service.add_department_version("org", "a", a.id, "Overlapping", day(2), "source:new")

        def test_rollback_and_empty_existing_clinic(self):
            self.assertEqual(self.service.list_departments("org", "a"), ())
            self.department()
            self.db.rollback()
            self.assertEqual(self.service.list_departments("org", "a"), ())
            self.assertIsNotNone(self.db.get(OrganizationClinicModel, "a"))

        def test_database_uniqueness_and_foreign_keys(self):
            self.department()
            with self.assertRaises(IntegrityError):
                self.department()
            self.db.rollback()
            with self.assertRaises(IntegrityError):
                self.db.add(HospitalDepartment(clinic_id="missing", code="X"))
                self.db.flush()
            self.db.rollback()

        def test_ambiguous_history_is_unresolved(self):
            a = self.department()
            self.db.add(HospitalDepartmentVersion(department_id=a.id, name="Conflicting", valid_from=day(2), basis_ref="source:bad"))
            self.db.flush()
            self.assertEqual(self.service.resolve_department("org", "a", a.id, day(3)).status, "AMBIGUOUS")

        def test_open_ended_provider_transfer_preserves_other_memberships(self):
            a = self.department()
            b = self.department(code="RAD")
            c = self.department(code="ORTH")
            old = self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), "source:old")
            self.service.add_provider_membership("org", "a", "qualified:p", c.id, day(1), "source:parallel")
            self.service.transfer_provider("org", "a", "qualified:p", a.id, b.id, day(10), "source:transfer")
            before = self.service.resolve_provider("org", "a", "qualified:p", day(9))
            after = self.service.resolve_provider("org", "a", "qualified:p", day(10))
            self.assertEqual({r.department_id for r in before.records}, {a.id, c.id})
            self.assertEqual({r.department_id for r in after.records}, {b.id, c.id})
            self.assertEqual(old.basis_ref, "source:old")
            self.assertEqual(next(r for r in after.records if r.department_id == b.id).basis_ref, "source:transfer")

        def test_open_ended_location_transfer(self):
            a = self.department()
            b = self.department(code="RAD")
            room = self.service.create_location("org", "a", "R1", "Room")
            self.service.add_location_membership("org", "a", room.id, a.id, day(1), "source:old")
            self.service.transfer_location("org", "a", room.id, a.id, b.id, day(10), "source:new")
            self.assertEqual(self.service.resolve_location("org", "a", room.id, day(9)).records[0].department_id, a.id)
            self.assertEqual(self.service.resolve_location("org", "a", room.id, day(10)).records[0].department_id, b.id)

        def test_transfer_validation_leaves_history_unchanged(self):
            a = self.department()
            b = self.department(code="RAD")
            other = self.department("b")
            old = self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), "source:old")
            for destination, at, basis in ((b.id, day(10), " "), (other.id, day(10), "source:x"),
                                           (b.id, day(1), "source:x"), (a.id, day(10), "source:x")):
                with self.assertRaises(ValueError):
                    self.service.transfer_provider("org", "a", "qualified:p", a.id, destination, at, basis)
                self.assertIsNone(old.valid_to)
            self.service.add_provider_membership("org", "a", "qualified:p", b.id, day(5), "source:existing")
            with self.assertRaises(ValueError):
                self.service.transfer_provider("org", "a", "qualified:p", a.id, b.id, day(10), "source:x")
            self.assertIsNone(old.valid_to)

        def test_transfer_insert_failure_rolls_back_split(self):
            a = self.department()
            b = self.department(code="RAD")
            old = self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), "source:old")
            self.db.commit()

            def fail_insert(mapper, connection, target):
                raise RuntimeError("Injected insert failure")
            event.listen(HospitalProviderMembership, "before_insert", fail_insert)
            try:
                with self.assertRaises(RuntimeError):
                    self.service.transfer_provider("org", "a", "qualified:p", a.id, b.id, day(10), "source:new")
            finally:
                event.remove(HospitalProviderMembership, "before_insert", fail_insert)
            self.db.refresh(old)
            self.assertIsNone(old.valid_to)
            self.assertEqual(self.service.resolve_provider("org", "a", "qualified:p", day(11)).records[0].department_id, a.id)

        def test_transfer_outer_rollback(self):
            a = self.department()
            b = self.department(code="RAD")
            old = self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), "source:old")
            self.db.commit()
            # Force a real outer transaction under sqlite's legacy driver mode.
            self.db.connection().exec_driver_sql("BEGIN")
            self.service.transfer_provider("org", "a", "qualified:p", a.id, b.id, day(10), "source:new")
            self.db.rollback()
            self.assertIsNone(old.valid_to)
            self.assertEqual(len(self.db.scalars(select(HospitalProviderMembership)).all()), 1)

        def test_database_blocks_cross_facility_location_link(self):
            department = self.department("b")
            room = self.service.create_location("org", "a", "R1", "Room")
            self.db.commit()
            with self.assertRaises(IntegrityError):
                self.db.add(HospitalLocationMembership(clinic_id="a", location_id=room.id,
                                                       department_id=department.id, valid_from=day(1), basis_ref="source:invalid"))
                self.db.flush()
            self.db.rollback()

        def test_non_utc_boundary(self):
            a = self.department()
            dubai = timezone(timedelta(hours=4))
            boundary = datetime(2026, 1, 10, 4, tzinfo=dubai)
            self.service.rename_department("org", "a", a.id, "Heart", boundary, "source:rename")
            before = boundary - timedelta(microseconds=1)
            self.assertEqual(self.service.resolve_department("org", "a", a.id, before).records[0].name, "Cardiology")
            self.assertEqual(self.service.resolve_department("org", "a", a.id, day(10)).records[0].name, "Heart")

        def test_transfer_preserves_original_end(self):
            a = self.department()
            b = self.department(code="RAD")
            self.service.add_provider_membership("org", "a", "qualified:p", a.id, day(1), "source:old", day(20))
            self.service.transfer_provider("org", "a", "qualified:p", a.id, b.id, day(10), "source:new")
            self.assertEqual(self.service.resolve_provider("org", "a", "qualified:p", day(19)).records[0].department_id, b.id)
            self.assertEqual(self.service.resolve_provider("org", "a", "qualified:p", day(20)).status, "UNKNOWN")

        def test_rename_insert_failure_retains_original_history(self):
            a = self.department()
            self.db.commit()

            def fail_insert(mapper, connection, target):
                raise RuntimeError("Injected rename insert failure")
            event.listen(HospitalDepartmentVersion, "before_insert", fail_insert)
            try:
                with self.assertRaises(RuntimeError):
                    self.service.rename_department("org", "a", a.id, "Heart", day(10), "source:new")
            finally:
                event.remove(HospitalDepartmentVersion, "before_insert", fail_insert)
            result = self.service.resolve_department("org", "a", a.id, day(11))
            self.assertEqual(result.status, "RESOLVED")
            self.assertEqual(result.records[0].name, "Cardiology")
            self.assertIsNone(result.records[0].valid_to)

        def test_write_lock_sql_targets_canonical_facility(self):
            # SQL compilation checks wiring only, not actual competing-worker behavior.
            from sqlalchemy.dialects import postgresql
            statements = []

            def capture(state):
                if state.is_select:
                    statements.append(str(state.statement.compile(dialect=postgresql.dialect())))
            event.listen(self.db, "do_orm_execute", capture)
            self.department()
            event.remove(self.db, "do_orm_execute", capture)
            self.assertTrue(any("org_clinics" in sql and "FOR UPDATE" in sql for sql in statements))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DirectoryTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    if sys.argv[1:] != ["--isolated-worker"]:
        raise SystemExit("Use the documented pytest command or --isolated-worker")
    run_worker()
