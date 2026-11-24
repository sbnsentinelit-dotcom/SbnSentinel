"""
Severity checks for the billing dashboard: review capacity (the clinic's open claims and review roster)
and payer filing risk (each open claim evaluated on its own). Reads never write; a backup preview never
changes the live result. Assigning or releasing a roster reviewer is a recorded change.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import RoleChecker
from app.db.database import get_db
from app.models.review_roster import ReviewRosterModel
from app.models.user import User, UserRole
from app.services import severity_checks as checks

router = APIRouter()

_READ_ROLES = [UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
               UserRole.CLINIC_MANAGER.value, UserRole.READ_ONLY_AUDITOR.value]
_WRITE_ROLES = [UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value,
                UserRole.CLINIC_MANAGER.value]


def _clinic_ids(db: Session, user: User):
    """Clinics in the user's scope (None = all, for a system administrator)."""
    from app.models.signal import SignalModel
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        return None
    own = getattr(user, "clinic_id", None)
    if user.role in (UserRole.CLINIC_MANAGER.value, UserRole.READ_ONLY_AUDITOR.value) and own:
        return [own]
    org = getattr(user, "org_id", None)
    ids = {c for (c,) in db.query(SignalModel.clinic_id).filter(SignalModel.org_id == org).distinct() if c} if org else set()
    if own:
        ids.add(own)
    return sorted(ids)


def _open_claims(db: Session, user: User):
    """Open claim signals awaiting a decision in the user's scope, each with its own evaluation."""
    from app.api.v1.endpoints.signals import (
        _actionable, _is_open_at_cutoff, _recorded_decisions_by_journey, _scoped_signal_query)
    from app.services.risk_level_evaluator import CLAIM_DENIAL, current_evaluation
    rows = [s for s in _scoped_signal_query(db, user).all()
            if (s.metadata_data or {}).get("risk_kind") == CLAIM_DENIAL and not (s.metadata_data or {}).get("notification")]
    decisions = _recorded_decisions_by_journey(db, rows)
    now = datetime.now(timezone.utc)
    out = []
    for s in rows:
        if not (_is_open_at_cutoff(s, decisions, now) and _actionable(s)):
            continue
        meta = s.metadata_data or {}
        out.append({"signal_id": s.id, "claim_id": (meta.get("risk_inputs") or {}).get("claim_id") or meta.get("claim_id"),
                    "evaluation": current_evaluation(meta)})
    return out


def _roster(db: Session, user: User):
    q = db.query(ReviewRosterModel)
    clinics = _clinic_ids(db, user)
    if clinics is not None:
        q = q.filter(ReviewRosterModel.clinic_id.in_(clinics or ["-"]))
    return q.order_by(ReviewRosterModel.id).all()


@router.get("/checks")
def get_checks(preview_backup: bool = False, db: Session = Depends(get_db),
               current_user: User = Depends(RoleChecker(_READ_ROLES))):
    return checks.live_checks(_open_claims(db, current_user), checks.roster_rows(_roster(db, current_user)),
                              preview_backup=preview_backup)


class AssignIn(BaseModel):
    assigned: bool


@router.post("/roster/{entry_id}")
def assign_reviewer(entry_id: int, body: AssignIn, db: Session = Depends(get_db),
                    current_user: User = Depends(RoleChecker(_WRITE_ROLES))):
    """Assigns (or releases) a qualified reviewer from the clinic roster; the live result is recalculated."""
    entry = next((e for e in _roster(db, current_user) if e.id == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Roster entry not found in your clinic.")
    if body.assigned and not entry.qualified:
        raise HTTPException(status_code=409, detail="Only a qualified reviewer can be assigned to claim reviews.")
    entry.assigned = body.assigned
    entry.updated_at = datetime.utcnow()
    entry.updated_by = getattr(current_user, "email", None)
    db.commit()
    return {"id": entry.id, "reviewer": entry.reviewer, "assigned": entry.assigned,
            "updated_at": entry.updated_at.isoformat() + "Z", "updated_by": entry.updated_by}
