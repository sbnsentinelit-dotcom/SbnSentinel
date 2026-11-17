"""Optimistic H06 reassignment with append-only history in the caller transaction."""
from datetime import datetime, timezone
from sqlalchemy import select, update
from app.models.work_assignment import WorkAssignment, WorkAssignmentHistory
from app.models.governance_storage import RecommendationModel
from app.services.hospital_scope_service import (
    resolve_hospital_scope, require_capability, permits_department, HospitalAccessDenied,
)


class AssignmentConflict(ValueError):
    pass


class WorkAssignmentService:
    def __init__(self, db, trusted_basis_resolver):
        self.db = db
        self.resolve_basis = trusted_basis_resolver

    def assign(self, actor, selection, recommendation_id, *, expected_version, assignee=None, team=None, reason):
        scope = resolve_hospital_scope(self.db, actor, selection, for_update=True)
        require_capability(scope, "assignment.write")
        if type(expected_version) is not int or expected_version < 0 or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Expected version and reason required")
        for value in (assignee, team):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
                raise ValueError("Invalid assignment metadata")
        recommendation = self.db.scalars(select(RecommendationModel).where(
            RecommendationModel.recommendation_id == recommendation_id).with_for_update()).first()
        if recommendation is None:
            raise HospitalAccessDenied("Recommendation unavailable")
        basis = self.resolve_basis(self.db, recommendation)
        if (basis["org_id"] != scope.org_id or basis["clinic_id"] != scope.clinic_id
                or not permits_department(scope, basis.get("department_id"))):
            raise HospitalAccessDenied("Recommendation out of scope")
        existing = self.db.get(WorkAssignment, recommendation_id)
        if existing and (existing.clinic_id != scope.clinic_id or
            existing.department_id != basis.get("department_id") or existing.journey_id != recommendation.journey_id):
            raise AssignmentConflict("Recommendation attribution changed; refresh required")
        now = datetime.now(timezone.utc)
        values = dict(assignee=assignee, team=team, actor_id=actor.id, updated_at=now, row_version=expected_version + 1)
        with self.db.begin_nested():
            if expected_version == 0:
                existing = self.db.get(WorkAssignment, recommendation_id)
                if existing:
                    raise AssignmentConflict("Assignment changed; refresh required")
                row = WorkAssignment(recommendation_id=recommendation_id, journey_id=recommendation.journey_id,
                    clinic_id=scope.clinic_id, department_id=basis.get("department_id"), **values)
                self.db.add(row)
                self.db.flush()
            else:
                result = self.db.execute(update(WorkAssignment).where(
                    WorkAssignment.recommendation_id == recommendation_id,
                    WorkAssignment.clinic_id == scope.clinic_id,
                    WorkAssignment.row_version == expected_version).values(**values))
                if result.rowcount != 1:
                    raise AssignmentConflict("Assignment changed; refresh required")
            self.db.add(WorkAssignmentHistory(recommendation_id=recommendation_id,
                row_version=expected_version + 1, actor_id=actor.id, grant_revision=scope.grant_revision, assignee=assignee, team=team,
                reason=reason, changed_at=now))
            self.db.flush()
            from app.services.hospital_audit import record_hospital_audit
            record_hospital_audit(self.db, scope, actor.id, "HOSPITAL_ASSIGNMENT_CHANGED", recommendation_id,
                reason=reason, new={"assignee": assignee, "team": team, "row_version": expected_version + 1})
        return expected_version + 1
