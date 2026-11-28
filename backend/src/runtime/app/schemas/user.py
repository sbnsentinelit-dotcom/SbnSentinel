from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    role: str = "admin"


class UserCreate(UserBase):
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    # MS-001 profile and scope (all optional, so older rows serialise unchanged)
    org_id: Optional[str] = None
    clinic_id: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    updated_at: Optional[datetime] = None
    password_changed_at: Optional[datetime] = None
    terms_accepted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse
