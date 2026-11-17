"""H01 directory. Callers own authorization, transactions and commits.

Effective intervals are half-open UTC periods. All service writes lock the
canonical facility until the caller commits/rolls back. PostgreSQL READ COMMITTED
is the supported concurrent-write mode; SQLite only supports sequential tests.
Direct ORM writes bypass period checks and must not be used by directory callers.
On database errors the caller must roll back its transaction.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select, or_
from app.models.organization import OrganizationClinicModel
from app.models.hospital_dimensions import (
    HospitalDepartment, HospitalDepartmentVersion, HospitalLocation,
    HospitalLocationMembership, HospitalProviderMembership,
)


@dataclass(frozen=True)
class DirectoryResolution:
    status: str
    records: tuple[Any, ...] = ()
    reason: str | None = None


def required(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value


def instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone-aware effective time is required")
    return value.astimezone(timezone.utc)


class HospitalDirectoryService:
    def __init__(self, db):
        self.db = db

    def facility(self, org_id, clinic_id):
        required(org_id, "org_id")
        required(clinic_id, "clinic_id")
        row = self.db.get(OrganizationClinicModel, clinic_id)
        if row is None or row.organization_id != org_id:
            raise ValueError("Facility does not belong to the expected organization")
        return row

    def department(self, org_id, clinic_id, department_id):
        self.facility(org_id, clinic_id)
        row = self.db.get(HospitalDepartment, required(department_id, "department_id"))
        if row is None or row.clinic_id != clinic_id:
            raise ValueError("Department does not belong to the expected facility")
        return row

    def _lock_facility(self, org_id, clinic_id):
        required(org_id, "org_id")
        required(clinic_id, "clinic_id")
        row = self.db.scalars(select(OrganizationClinicModel).where(
            OrganizationClinicModel.id == clinic_id,
            OrganizationClinicModel.organization_id == org_id,
        ).with_for_update()).first()
        if row is None:
            raise ValueError("Facility does not belong to the expected organization")
        return row

    def _save(self, row):
        self.db.add(row)
        self.db.flush()
        return row

    def create_department(self, org_id, clinic_id, code, name, valid_from, basis_ref):
        self._lock_facility(org_id, clinic_id)
        required(name, "name")
        required(basis_ref, "basis_ref")
        start = instant(valid_from)
        row = self._save(HospitalDepartment(clinic_id=clinic_id, code=required(code, "code")))
        self.add_department_version(org_id, clinic_id, row.id, name, start, basis_ref)
        return row

    def list_departments(self, org_id, clinic_id, *, include_inactive=False):
        self.facility(org_id, clinic_id)
        query = select(HospitalDepartment).where(HospitalDepartment.clinic_id == clinic_id)
        if not include_inactive:
            query = query.where(HospitalDepartment.is_active.is_(True))
        return tuple(self.db.scalars(query.order_by(HospitalDepartment.code)).all())

    def _period(self, start, end):
        start = instant(start)
        end = instant(end) if end is not None else None
        if end is not None and end <= start:
            raise ValueError("valid_to must be later than valid_from")
        return start, end

    def _overlap(self, model, filters, start, end):
        query = select(model).where(*filters, or_(model.valid_to.is_(None), model.valid_to > start))
        if end is not None:
            query = query.where(model.valid_from < end)
        if self.db.scalars(query).first() is not None:
            raise ValueError("Overlapping effective periods")

    def add_department_version(self, org_id, clinic_id, department_id, name, valid_from, basis_ref, valid_to=None):
        self._lock_facility(org_id, clinic_id)
        self.department(org_id, clinic_id, department_id)
        start, end = self._period(valid_from, valid_to)
        self._overlap(HospitalDepartmentVersion, [HospitalDepartmentVersion.department_id == department_id], start, end)
        return self._save(HospitalDepartmentVersion(department_id=department_id, name=required(name, "name"),
                                                    valid_from=start, valid_to=end, basis_ref=required(basis_ref, "basis_ref")))

    def rename_department(self, org_id, clinic_id, department_id, name, effective_at, basis_ref):
        self._lock_facility(org_id, clinic_id)
        required(name, "name")
        required(basis_ref, "basis_ref")
        when = instant(effective_at)
        result = self.resolve_department(org_id, clinic_id, department_id, when)
        if result.status != "RESOLVED":
            raise ValueError("Rename requires one resolved historical version")
        previous = result.records[0]
        start = previous.valid_from
        if start.tzinfo is None:  # SQLite returns stored UTC without tzinfo.
            start = start.replace(tzinfo=timezone.utc)
        if when <= start:
            raise ValueError("Rename must follow the previous version start")
        end = previous.valid_to
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        with self.db.begin_nested():
            previous.valid_to = when
            self.db.flush()
            new = self.add_department_version(org_id, clinic_id, department_id, name, when, basis_ref, end)
        return new

    def create_location(self, org_id, clinic_id, code, name):
        self._lock_facility(org_id, clinic_id)
        return self._save(HospitalLocation(clinic_id=clinic_id, code=required(code, "code"), name=required(name, "name")))

    def add_location_membership(self, org_id, clinic_id, location_id, department_id, valid_from, basis_ref, valid_to=None):
        self._lock_facility(org_id, clinic_id)
        self.department(org_id, clinic_id, department_id)
        location = self.db.get(HospitalLocation, required(location_id, "location_id"))
        if location is None or location.clinic_id != clinic_id:
            raise ValueError("Location does not belong to the expected facility")
        start, end = self._period(valid_from, valid_to)
        self._overlap(HospitalLocationMembership, [HospitalLocationMembership.location_id == location_id], start, end)
        return self._save(HospitalLocationMembership(clinic_id=clinic_id, location_id=location_id, department_id=department_id,
                                                     valid_from=start, valid_to=end, basis_ref=required(basis_ref, "basis_ref")))

    def add_provider_membership(self, org_id, clinic_id, provider_ref, department_id, valid_from, basis_ref, valid_to=None):
        self._lock_facility(org_id, clinic_id)
        self.department(org_id, clinic_id, department_id)
        start, end = self._period(valid_from, valid_to)
        provider_ref = required(provider_ref, "provider_ref")
        self._overlap(HospitalProviderMembership, [HospitalProviderMembership.provider_ref == provider_ref,
                                                   HospitalProviderMembership.department_id == department_id], start, end)
        return self._save(HospitalProviderMembership(provider_ref=provider_ref, department_id=department_id,
                                                     valid_from=start, valid_to=end, basis_ref=required(basis_ref, "basis_ref")))

    def _transfer(self, model, org_id, clinic_id, from_department_id, to_department_id,
                  filters, values, effective_at, basis_ref):
        """Split one explicit source membership; never infer a provider's origin."""
        self._lock_facility(org_id, clinic_id)
        self.department(org_id, clinic_id, from_department_id)
        self.department(org_id, clinic_id, to_department_id)
        if from_department_id == to_department_id:
            raise ValueError("Transfer requires a different department")
        required(basis_ref, "basis_ref")
        when = instant(effective_at)
        result = self._resolve(model, [*filters, model.department_id == from_department_id], when)
        if result.status != "RESOLVED":
            raise ValueError("Transfer requires one resolved source membership")
        previous = result.records[0]
        start = previous.valid_from
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if when <= start:
            raise ValueError("Transfer must follow the source membership start")
        end = previous.valid_to
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        # Validate the destination before changing history. Location periods are
        # exclusive across departments; providers may legitimately serve several.
        conflict_filters = [*filters, model.id != previous.id]
        if model is HospitalProviderMembership:
            conflict_filters.append(model.department_id == to_department_id)
        self._overlap(model, conflict_filters, when, end)
        # A savepoint keeps the split atomic if inserting the new row fails.
        # begin_nested flushes caller-pending work before opening its savepoint;
        # the outer transaction remains owned by the caller.
        with self.db.begin_nested():
            previous.valid_to = when
            self.db.flush()
            new = self._save(model(department_id=to_department_id, valid_from=when,
                                   valid_to=end, basis_ref=basis_ref, **values))
        return new

    def transfer_provider(self, org_id, clinic_id, provider_ref, from_department_id,
                          to_department_id, effective_at, basis_ref):
        provider_ref = required(provider_ref, "provider_ref")
        return self._transfer(HospitalProviderMembership, org_id, clinic_id,
                              from_department_id, to_department_id,
                              [HospitalProviderMembership.provider_ref == provider_ref],
                              {"provider_ref": provider_ref}, effective_at, basis_ref)

    def transfer_location(self, org_id, clinic_id, location_id, from_department_id,
                          to_department_id, effective_at, basis_ref):
        self._lock_facility(org_id, clinic_id)
        location = self.db.get(HospitalLocation, required(location_id, "location_id"))
        if location is None or location.clinic_id != clinic_id:
            raise ValueError("Location does not belong to the expected facility")
        return self._transfer(HospitalLocationMembership, org_id, clinic_id,
                              from_department_id, to_department_id,
                              [HospitalLocationMembership.location_id == location_id],
                              {"location_id": location_id, "clinic_id": clinic_id}, effective_at, basis_ref)

    def _resolve(self, model, filters, at):
        if at is None:
            return DirectoryResolution("UNKNOWN", reason="Missing effective time")
        when = instant(at)
        rows = tuple(self.db.scalars(select(model).where(*filters, model.valid_from <= when,
                                                         or_(model.valid_to.is_(None), model.valid_to > when))
                                     .execution_options(populate_existing=True)).all())
        if not rows:
            return DirectoryResolution("UNKNOWN", reason="No source-backed match")
        return DirectoryResolution("RESOLVED" if len(rows) == 1 else "AMBIGUOUS", rows)

    def resolve_department(self, org_id, clinic_id, department_id, at):
        self.facility(org_id, clinic_id)
        if department_id is None:
            return DirectoryResolution("UNKNOWN", reason="Missing department")
        self.department(org_id, clinic_id, department_id)
        return self._resolve(HospitalDepartmentVersion, [HospitalDepartmentVersion.department_id == department_id], at)

    def resolve_provider(self, org_id, clinic_id, provider_ref, at):
        self.facility(org_id, clinic_id)
        if provider_ref is None:
            return DirectoryResolution("UNKNOWN", reason="Missing provider")
        required(provider_ref, "provider_ref")
        return self._resolve(HospitalProviderMembership, [HospitalProviderMembership.provider_ref == provider_ref,
                                                          HospitalProviderMembership.department_id.in_(select(HospitalDepartment.id).where(
                                                              HospitalDepartment.clinic_id == clinic_id))], at)

    def resolve_location(self, org_id, clinic_id, location_id, at):
        self.facility(org_id, clinic_id)
        if location_id is None:
            return DirectoryResolution("UNKNOWN", reason="Missing location")
        location = self.db.get(HospitalLocation, required(location_id, "location_id"))
        if location is None or location.clinic_id != clinic_id:
            raise ValueError("Location does not belong to the expected facility")
        return self._resolve(HospitalLocationMembership, [HospitalLocationMembership.location_id == location_id], at)
