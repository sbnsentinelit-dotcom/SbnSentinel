"""
Claim review roster: who reviews claims for a clinic and when. The review-capacity check reads it;
nothing about staffing is fixed in code. A backup is assigned (or released) from the dashboard, and
that confirmed assignment is what changes the live result; a preview never writes here.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.db.database import Base


class ReviewRosterModel(Base):
    __tablename__ = "review_roster"

    id = Column(Integer, primary_key=True, autoincrement=True)
    clinic_id = Column(String(64), nullable=False, index=True)
    reviewer = Column(String(160), nullable=False)
    role = Column(String(64), nullable=True)            # e.g. "Claims reviewer", "Backup reviewer"
    qualified = Column(Boolean, nullable=False, default=True)
    shift_start = Column(String(5), nullable=False)     # local "HH:MM"
    shift_end = Column(String(5), nullable=False)
    assigned = Column(Boolean, nullable=False, default=False)
    source = Column(String(64), nullable=False, default="clinic roster")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_by = Column(String(160), nullable=True)
