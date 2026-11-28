from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.db.database import Base

from enum import Enum


class UserRole(str, Enum):
    """The six approved roles (Build Pack 03), plus a read-only auditor and the unassigned state.

    "Clinic Manager" and "Front Desk" were the earlier names of Clinic Administrator and Staff User.
    The old member names stay as aliases so existing code keeps working; stored rows are renamed by
    migration r1_ms001_foundation, and canonical_role() maps an old name read from anywhere else."""
    SYSTEM_ADMINISTRATOR = "System Administrator"          # shown as Platform Administrator
    ORGANIZATION_ADMINISTRATOR = "Organization Administrator"
    CLINIC_ADMINISTRATOR = "Clinic Administrator"
    OPERATIONS_MANAGER = "Operations Manager"
    PRACTICE_MANAGER = "Practice Manager"
    STAFF_USER = "Staff User"
    READ_ONLY_AUDITOR = "Read-Only Auditor"
    UNASSIGNED = "Unassigned"
    # Aliases (same value, therefore the same member)
    CLINIC_MANAGER = "Clinic Administrator"
    FRONT_DESK = "Staff User"


LEGACY_ROLE_NAMES = {"Clinic Manager": UserRole.CLINIC_ADMINISTRATOR.value, "Front Desk": UserRole.STAFF_USER.value}

# Display name of each stored role, as the screens show it.
ROLE_LABELS = {
    UserRole.SYSTEM_ADMINISTRATOR.value: "Platform Administrator",
    UserRole.ORGANIZATION_ADMINISTRATOR.value: "Organization Administrator",
    UserRole.CLINIC_ADMINISTRATOR.value: "Clinic Administrator",
    UserRole.OPERATIONS_MANAGER.value: "Operations Manager",
    UserRole.PRACTICE_MANAGER.value: "Practice Manager",
    UserRole.STAFF_USER.value: "Staff User",
    UserRole.READ_ONLY_AUDITOR.value: "Read-Only Auditor",
    UserRole.UNASSIGNED.value: "Unassigned",
}

# Rank used for "cannot assign or manage a role above your own".
ROLE_RANK = {
    UserRole.SYSTEM_ADMINISTRATOR.value: 100,
    UserRole.ORGANIZATION_ADMINISTRATOR.value: 80,
    UserRole.CLINIC_ADMINISTRATOR.value: 60,
    UserRole.OPERATIONS_MANAGER.value: 40,
    UserRole.PRACTICE_MANAGER.value: 40,
    UserRole.STAFF_USER.value: 20,
    UserRole.READ_ONLY_AUDITOR.value: 10,
    UserRole.UNASSIGNED.value: 0,
}


def canonical_role(role):
    """The current stored name of a role; an old name maps to its new name, anything else is returned as is."""
    if role is None:
        return None
    value = role.value if isinstance(role, UserRole) else str(role)
    return LEGACY_ROLE_NAMES.get(value, value)


def role_rank(role) -> int:
    return ROLE_RANK.get(canonical_role(role), 0)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, index=True)
    role = Column(String, default=UserRole.UNASSIGNED.value)
    org_id = Column(String, nullable=True)  # Scope context
    clinic_id = Column(String, nullable=True)  # Fine-grained clinic scope
    is_active = Column(Boolean, default=True)
    token_invalid_before = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Profile and identity lifecycle (MS-001)
    phone = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    terms_version = Column(String, nullable=True)
    invited_by = Column(String, nullable=True)
