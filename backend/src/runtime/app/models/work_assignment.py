"""H06 assignment metadata. Never grants decision authority."""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint, Text
from app.db.database import Base
from app.models.hospital_dimensions import new_id


class WorkAssignment(Base):
    __tablename__ = "hospital_work_assignments"
    __table_args__ = (ForeignKeyConstraint(["department_id", "clinic_id"],
        ["hospital_departments.id", "hospital_departments.clinic_id"], name="fk_hsp01_assignment_department"),)
    recommendation_id = Column(String, ForeignKey("governed_recommendations.recommendation_id"), primary_key=True)
    journey_id = Column(String, nullable=False)
    clinic_id = Column(String, ForeignKey("org_clinics.id"), nullable=False, index=True)
    department_id = Column(String, nullable=True, index=True)
    assignee = Column(String, nullable=True)
    team = Column(String, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    row_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class WorkAssignmentHistory(Base):
    __tablename__ = "hospital_assignment_history"
    __table_args__ = (UniqueConstraint("recommendation_id", "row_version", name="uq_hsp01_assignment_revision"),)
    id = Column(String, primary_key=True, default=new_id)
    recommendation_id = Column(String, ForeignKey("hospital_work_assignments.recommendation_id"), nullable=False, index=True)
    row_version = Column(Integer, nullable=False)
    actor_id = Column(Integer, nullable=False)
    grant_revision = Column(Integer, nullable=False)
    assignee = Column(String, nullable=True)
    team = Column(String, nullable=True)
    reason = Column(Text, nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
