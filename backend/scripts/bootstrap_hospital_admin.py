"""Explicit initial grant only; never defaults to or creates a database."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor-id", type=int, required=True)
    parser.add_argument("--clinic-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--allow-hospital-wide-access", action="store_true", required=True)
    args = parser.parse_args()
    target = os.getenv("HSP01_ADMIN_DATABASE_URL", "")
    if not target.startswith(("postgresql://", "postgresql+psycopg2://")) or not args.reason.strip():
        parser.error("Explicit approved PostgreSQL HSP01_ADMIN_DATABASE_URL and reason are required")
    os.environ["DATABASE_URL"] = target
    os.environ["SENTINEL_DISABLE_SCHEMA_AUTOCREATE"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/runtime"))
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.models.organization import OrganizationClinicModel
    from app.models.hospital_dimensions import HospitalDepartment
    from app.models.hospital_membership import HospitalMembership
    from app.integrations.core.source_contracts import VerifiedHospitalScope
    from app.services.hospital_audit import record_hospital_audit
    capabilities = {"directory.write", "access.manage", "source.configure", "source.read",
        "report.read", "report.export", "review.read", "assignment.write", "audit.read"}
    with SessionLocal() as db:
        facility = db.scalars(select(OrganizationClinicModel).where(
            OrganizationClinicModel.id == args.clinic_id).with_for_update()).first()
        actor = db.get(User, args.actor_id)
        if (not facility or not actor or not actor.is_active or actor.org_id != facility.organization_id
            or actor.role not in {UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value}):
            parser.error("An existing active administrator and matching canonical organization/facility are required")
        if db.scalars(select(HospitalMembership).where(HospitalMembership.actor_id == actor.id,
                HospitalMembership.clinic_id == facility.id)).first():
            parser.error("A grant already exists; use the revision-checked membership API")
        grant = HospitalMembership(actor_id=actor.id, clinic_id=facility.id, department_ids=[],
            all_departments=True, allow_unattributed=True, full_record=True,
            capabilities=sorted(capabilities), grant_revision=1, is_active=True)
        db.add(grant); db.flush()
        departments = frozenset(db.scalars(select(HospitalDepartment.id).where(
            HospitalDepartment.clinic_id == facility.id)).all())
        scope = VerifiedHospitalScope(org_id=facility.organization_id, clinic_id=facility.id,
            allowed_department_ids=departments, all_departments=True, allow_unattributed=True,
            full_record=True, capabilities=frozenset(capabilities), grant_revision=1)
        record_hospital_audit(db, scope, "bootstrap-control-plane", "HOSPITAL_INITIAL_GRANT", grant.id,
            reason=args.reason, new={"target_actor_id": actor.id, "scope": scope.model_dump(mode="json")})
        db.commit()
    print("Initial hospital administrator grant recorded; review approval capability remains disabled.")


if __name__ == "__main__":
    main()
