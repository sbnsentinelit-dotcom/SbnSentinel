"""H02: resolve current explicit grants on each operation, with no admin bypass."""
from sqlalchemy import select
from app.models.organization import OrganizationClinicModel
from app.models.hospital_dimensions import HospitalDepartment
from app.models.hospital_membership import HospitalMembership
from app.integrations.core.source_contracts import VerifiedHospitalScope


class HospitalAccessDenied(PermissionError):
    pass


def resolve_hospital_scope(db, actor, selection, *, for_update=False):
    from app.models.user import User
    actor = db.get(User, actor.id, populate_existing=True) if actor is not None else None
    if not actor or not actor.is_active or not actor.org_id:
        raise HospitalAccessDenied("Active organization membership required")
    clinic_id = selection.get("clinic_id")
    if not clinic_id:
        raise HospitalAccessDenied("Select an explicit facility")
    facility = db.get(OrganizationClinicModel, clinic_id)
    if not facility or facility.organization_id != actor.org_id:
        raise HospitalAccessDenied("Facility out of scope")
    if for_update:
        # A single facility-first lock order avoids grant/configuration deadlocks.
        db.scalars(select(OrganizationClinicModel).where(
            OrganizationClinicModel.id == clinic_id).with_for_update()).one()
    query = select(HospitalMembership).where(
        HospitalMembership.actor_id == actor.id,
        HospitalMembership.clinic_id == clinic_id,
        HospitalMembership.is_active.is_(True),
    ).execution_options(populate_existing=True)
    if for_update:
        query = query.with_for_update()
    grant = db.scalars(query).first()
    if not grant:
        raise HospitalAccessDenied("No active hospital grant")
    available = frozenset(db.scalars(select(HospitalDepartment.id).where(
        HospitalDepartment.clinic_id == clinic_id)).all())
    allowed = available if grant.all_departments else available.intersection(grant.department_ids or [])
    selected = selection.get("department_ids")
    narrowed = selected is not None
    if narrowed:
        selected = frozenset(selected)
        if not selected.issubset(allowed):
            raise HospitalAccessDenied("Department selection expands access")
        allowed = selected
    return VerifiedHospitalScope(org_id=facility.organization_id, clinic_id=clinic_id,
        allowed_department_ids=allowed, all_departments=bool(grant.all_departments and not narrowed),
        capabilities=frozenset(grant.capabilities or []), grant_revision=grant.grant_revision,
        allow_unattributed=bool(grant.allow_unattributed and not narrowed),
        full_record=bool(grant.full_record and not narrowed))


def require_capability(scope, capability):
    if capability not in scope.capabilities:
        raise HospitalAccessDenied("Required capability unavailable")


def hospital_facility_ids():
    """Canonical hospital IDs plus explicitly reconciled legacy aliases."""
    from app.models.hospital_processing import FacilityCrosswalk
    canonical = select(HospitalDepartment.clinic_id).distinct()
    return canonical.union(select(FacilityCrosswalk.legacy_clinic_id).where(
        FacilityCrosswalk.status == "RESOLVED", FacilityCrosswalk.clinic_id.in_(canonical)))


def permits_department(scope, department_id):
    return scope.allow_unattributed if department_id is None else department_id in scope.allowed_department_ids


def scope_predicate(scope, clinic_column, department_column):
    from sqlalchemy import or_
    departments = department_column.in_(scope.allowed_department_ids)
    if scope.allow_unattributed:
        departments = or_(departments, department_column.is_(None))
    return (clinic_column == scope.clinic_id) & departments


def configure_membership(db, actor, clinic_id, target_actor_id, *, expected_revision,
                         department_ids, capabilities, all_departments=False,
                         allow_unattributed=False, full_record=False, is_active=True, reason):
    """Explicit delegated grants with optimistic revision and no scope escalation."""
    from app.models.user import User
    admin = resolve_hospital_scope(db, actor, {"clinic_id": clinic_id}, for_update=True)
    require_capability(admin, "access.manage")
    target = db.get(User, target_actor_id)
    if not target or target.org_id != admin.org_id:
        raise HospitalAccessDenied("Target actor belongs to another organization")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid expected revision")
    if (not set(department_ids).issubset(admin.allowed_department_ids)
        or not set(capabilities).issubset(admin.capabilities)
        or (all_departments and not admin.all_departments)
        or (allow_unattributed and not admin.allow_unattributed)
        or (full_record and not admin.full_record)):
        raise HospitalAccessDenied("Delegation expands the administrator's grant")
    # Facility lock also serializes creation of a previously absent actor grant.
    db.scalars(select(OrganizationClinicModel).where(
        OrganizationClinicModel.id == clinic_id).with_for_update()).one()
    grant = db.scalars(select(HospitalMembership).where(HospitalMembership.actor_id == target_actor_id,
        HospitalMembership.clinic_id == clinic_id).with_for_update()
        .execution_options(populate_existing=True)).first()
    if (grant.grant_revision if grant else 0) != expected_revision:
        raise ValueError("Grant changed; refresh required")
    fields = ("department_ids", "capabilities", "all_departments", "allow_unattributed", "full_record", "is_active", "grant_revision")
    before = {field: getattr(grant, field) for field in fields} if grant else None
    if grant is None:
        grant = HospitalMembership(actor_id=target_actor_id, clinic_id=clinic_id)
        db.add(grant)
    grant.department_ids = sorted(set(department_ids))
    grant.capabilities = sorted(set(capabilities))
    grant.all_departments, grant.allow_unattributed = all_departments, allow_unattributed
    grant.full_record, grant.is_active = full_record, is_active
    grant.grant_revision = expected_revision + 1
    db.flush()
    from app.services.hospital_audit import record_hospital_audit
    record_hospital_audit(db, admin, actor.id, "HOSPITAL_GRANT_CHANGED", grant.id, reason=reason,
        old=before, new={field: getattr(grant, field) for field in fields})
    return grant
