from datetime import timedelta, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.user import User, ROLE_LABELS, canonical_role
from pydantic import BaseModel, EmailStr
from app.schemas.user import UserLogin, Token, UserResponse
from app.core.security import verify_password, get_password_hash
from app.core.config import settings
from app.core import passwords
from app.core.access import record, org_is_suspended, is_platform
from app.services.data_audit_engine import data_audit_engine
from app.services import session_service
from app.api.deps import get_current_user
from fastapi import Request

router = APIRouter()

_ip_rate_limits = {}


def check_ip_abuse(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = datetime.utcnow()
    global _ip_rate_limits

    _ip_rate_limits = {ip: times for ip, times in _ip_rate_limits.items() if times[-1] > now - timedelta(minutes=5)}

    if client_ip not in _ip_rate_limits:
        _ip_rate_limits[client_ip] = []

    times = _ip_rate_limits[client_ip]
    times = [t for t in times if t > now - timedelta(minutes=1)]

    if len(times) >= 10:
        raise HTTPException(status_code=429, detail="Too many requests from this IP. Please try again later.")

    times.append(now)
    _ip_rate_limits[client_ip] = times
    return client_ip


class RegisterInitiateRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "clinic_admin"


@router.post("/register/initiate")
def initiate_registration(user_in: RegisterInitiateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), ip: str = Depends(check_ip_abuse)):
    # The same answer whether or not the address already has an account (MS-001 account privacy).
    answer = {"message": "If this email can be registered, a verification code has been sent."}
    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        return answer

    import secrets
    from app.models.otp import OTPModel
    from app.core.email import send_email

    recent_otp = db.query(OTPModel).filter(OTPModel.email == user_in.email, OTPModel.created_at >= datetime.utcnow() - timedelta(minutes=1)).first()
    if recent_otp:
        return answer

    otp = str(secrets.randbelow(900000) + 100000)
    db_otp = OTPModel(email=user_in.email, otp_code=otp, purpose="signup")
    db.add(db_otp)
    db.commit()

    html_body = f"""
    <h2>SBN Sentinel Clinic Registration</h2>
    <p>Your verification code is: <strong>{otp}</strong></p>
    <p>Please enter this code in the app to complete your registration.</p>
    """
    background_tasks.add_task(send_email, user_in.email, "SBN Sentinel - Verification Code", html_body, is_html=True)

    return answer


def validate_password_policy(password: str):
    """Kept for callers of the earlier name; the rule lives in app.core.passwords."""
    passwords.validate(password)


@router.get("/password-policy")
def password_policy():
    """The one password rule every form checks against (signup, activation, reset, change)."""
    return passwords.policy()


class RegisterVerifyRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    otp: str
    role: Optional[str] = None


@router.post("/register", response_model=UserResponse)
def register_user(user_in: RegisterVerifyRequest, db: Session = Depends(get_db), ip: str = Depends(check_ip_abuse)):
    passwords.validate(user_in.password, email=user_in.email)

    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    from app.models.otp import OTPModel
    otp_record = db.query(OTPModel).filter(
        OTPModel.email == user_in.email,
        OTPModel.purpose == "signup",
        OTPModel.is_used == False  # noqa: E712
    ).order_by(OTPModel.created_at.desc()).first()

    if not otp_record or datetime.utcnow() - otp_record.created_at > timedelta(minutes=settings.OTP_EXPIRE_MINUTES):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    otp_record.attempts = getattr(otp_record, 'attempts', 0) + 1
    if otp_record.attempts > 3:
        otp_record.is_used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Maximum OTP attempts exceeded. Please request a new one.")

    if not passwords.same_text(otp_record.otp_code, user_in.otp):
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    from app.models.user import UserRole
    # BUG-025: Keep new accounts pending until approved or invited
    # Default public signup cannot reach administrative functions
    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role=UserRole.STAFF_USER.value,
        is_active=False
    )
    db.add(user)
    otp_record.is_used = True
    db.commit()
    db.refresh(user)
    return user


def _client(request: Request):
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.post("/login", response_model=Token)
def login_access_token(user_in: UserLogin, request: Request, db: Session = Depends(get_db), ip: str = Depends(check_ip_abuse)):
    user = db.query(User).filter(User.email == user_in.email).first()
    generic = HTTPException(status_code=400, detail="Incorrect email or password")
    if not user or not passwords.matches(user_in.password, user.hashed_password):
        # SIAME / SES-008: Log failed authentication attempt (no account, wrong password, or an invited
        # account that has not chosen a password yet: the caller cannot tell these apart).
        data_audit_engine._log_internal(
            db,
            user_system=user_in.email,
            action="SECURITY:FAILED_LOGIN" if not user or user.hashed_password else "SECURITY:FAILED_LOGIN_PENDING_ACTIVATION",
            module="Authentication",
            correlation_id="Auth")
        raise generic
    elif not user.is_active:
        record(db, user, "SECURITY:FAILED_LOGIN_INACTIVE", "Authentication", correlation_id="Auth")
        db.commit()
        raise generic
    if not is_platform(user) and org_is_suspended(db, user.org_id):
        record(db, user, "SECURITY:FAILED_LOGIN_ORG_SUSPENDED", "Authentication", correlation_id="Auth")
        db.commit()
        raise HTTPException(status_code=403, detail="Your organization is suspended. Contact your platform administrator.")

    if canonical_role(user.role) != user.role:
        user.role = canonical_role(user.role)
    client_ip, agent = _client(request)
    session, access_token = session_service.start(db, user, client_ip, agent)
    # SIAME / SES-008: Log successful authentication, with the session it opened
    record(db, user, "SECURITY:USER_LOGIN", "Authentication", correlation_id="Auth",
           target_type="session", target_id=session.id)
    db.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@router.post("/logout")
def logout(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Ends the caller's session on the server. The token stops working at once."""
    sid = getattr(current_user, "_session_id", None)
    if sid:
        from app.models.identity import UserSessionModel
        row = db.query(UserSessionModel).filter(UserSessionModel.id == sid).first()
        if row is not None:
            session_service.end(db, row, "logout")
    else:
        # A token from before server sessions: retire every such token of this user.
        current_user.token_invalid_before = datetime.utcnow()
    record(db, current_user, "SECURITY:USER_LOGOUT", "Authentication", target_type="session", target_id=sid)
    db.commit()
    return {"message": "Signed out", "session_id": sid}


@router.post("/activity")
def record_activity(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The person is using the screen (typing, clicking). Only this call extends a session's idle time;
    polling and other reads never do. At most one write a minute."""
    from app.models.identity import UserSessionModel
    sid = getattr(current_user, "_session_id", None)
    row = db.query(UserSessionModel).filter(UserSessionModel.id == sid).first() if sid else None
    moved = session_service.record_activity(db, row) if row is not None else False
    return {"recorded": moved, "last_activity_at": row.last_seen_at.isoformat() + "Z" if row is not None else None,
            "idle_timeout_minutes": int(session_service.idle_limit().total_seconds() // 60)}


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The caller's sessions, newest first: when each started, was last used, and how it ended."""
    from app.models.identity import UserSessionModel
    rows = db.query(UserSessionModel).filter(UserSessionModel.user_id == current_user.id) \
        .order_by(UserSessionModel.created_at.desc()).limit(20).all()
    sid = getattr(current_user, "_session_id", None)
    return {"idle_timeout_minutes": int(session_service.idle_limit().total_seconds() // 60),
            "sessions": [session_service.view(r, sid) for r in rows]}


@router.delete("/sessions/{session_id}")
def end_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.models.identity import UserSessionModel
    row = db.query(UserSessionModel).filter(UserSessionModel.id == session_id,
                                            UserSessionModel.user_id == current_user.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session_service.end(db, row, "revoked")
    record(db, current_user, "SECURITY:SESSION_REVOKED", "Authentication", target_type="session", target_id=row.id)
    db.commit()
    return session_service.view(row, getattr(current_user, "_session_id", None))


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """Signed-in password change: the current password is verified, the new one follows the password
    rule and its confirmation, and every other session of this user ends."""
    if not verify_password(payload.current_password, current_user.hashed_password or ""):
        record(db, current_user, "SECURITY:PASSWORD_CHANGE_FAILED", "Authentication", detail="current password did not match")
        db.commit()
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    passwords.validate(payload.new_password, payload.confirm_password, current_user.email)
    if verify_password(payload.new_password, current_user.hashed_password or ""):
        raise HTTPException(status_code=400, detail="New password must be different from the current one")
    now = datetime.utcnow()
    current_user.hashed_password = get_password_hash(payload.new_password)
    current_user.password_changed_at = now
    current_user.updated_at = now
    ended = session_service.end_all(db, current_user.id, "password_changed", keep=getattr(current_user, "_session_id", None))
    record(db, current_user, "SECURITY:PASSWORD_CHANGED", "Authentication", target_type="user", target_id=current_user.id,
           detail=f"{ended} other session(s) ended")
    db.commit()
    return {"message": "Password changed", "other_sessions_ended": ended}


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None


@router.patch("/me", response_model=UserResponse)
def update_me(payload: ProfileUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Saves the caller's own profile details. Email, role and scope are changed by an administrator."""
    fields = ("full_name", "phone", "job_title")
    before = {f: getattr(current_user, f) for f in fields}
    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data and not (data["full_name"] or "").strip():
        raise HTTPException(status_code=400, detail="Name is required")
    for k, v in data.items():
        setattr(current_user, k, (v or "").strip() or None)
    after = {f: getattr(current_user, f) for f in fields}
    from app.core.access import changed_fields
    diff = changed_fields(before, after, fields)
    if diff:
        current_user.updated_at = datetime.utcnow()
        record(db, current_user, "PROFILE_UPDATED", "Profile", target_type="user", target_id=current_user.id, detail=diff)
    db.commit()
    db.refresh(current_user)
    return current_user


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db), ip: str = Depends(check_ip_abuse)):
    """Sends a reset link (and the same one-time code) to a registered, active account. The answer is
    the same whether or not the email is registered."""
    answer = {"message": "If that email is registered, a password reset link has been sent."}
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not user.is_active:
        return answer

    from app.models.otp import OTPModel
    from app.core.email import send_email

    recent_otp = db.query(OTPModel).filter(OTPModel.email == payload.email, OTPModel.purpose == "reset_password",
                                           OTPModel.created_at >= datetime.utcnow() - timedelta(minutes=1)).first()
    if recent_otp:
        return answer

    row, otp, token = passwords.issue(db, user.email, "reset_password", timedelta(minutes=settings.OTP_EXPIRE_MINUTES))
    url = passwords.link("/", reset_token=token)
    record(db, user, "SECURITY:PASSWORD_RESET_REQUESTED", "Authentication", target_type="user", target_id=user.id)
    db.commit()

    html_body = f"""
    <h2>SBN Sentinel Reset Password</h2>
    <p><a href="{url}">Reset your password</a> (the link works once and expires in {settings.OTP_EXPIRE_MINUTES} minutes).</p>
    <p>Or enter this one-time code in the app: <strong>{otp}</strong></p>
    <p>If you did not ask for this, ignore this email; your password stays the same.</p>
    """
    send_email(user.email, "SBN Sentinel - Reset Password", html_body, is_html=True)
    return answer


class ResetPasswordRequest(BaseModel):
    email: Optional[EmailStr] = None
    otp: Optional[str] = None
    token: Optional[str] = None
    new_password: str
    confirm_password: str


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db), ip: str = Depends(check_ip_abuse)):
    row = passwords.redeem(db, payload.email, "reset_password", code=payload.otp, token=payload.token)
    user = db.query(User).filter(User.email == row.email).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired link or code")
    passwords.validate(payload.new_password, payload.confirm_password, user.email)

    passwords.consume(db, row)        # atomic: a second submission of the same link or code fails here
    now = datetime.utcnow()
    user.hashed_password = get_password_hash(payload.new_password)
    user.token_invalid_before = now
    user.password_changed_at = now
    passwords.retire_all(db, user.email, "reset_password")      # and every earlier reset credential
    ended = session_service.end_all(db, user.id, "password_reset")
    record(db, user, "SECURITY:PASSWORD_RESET", "Authentication", target_type="user", target_id=user.id,
           detail=f"{ended} session(s) ended")
    db.commit()
    return {"message": "Password has been successfully reset."}


class AcceptInviteRequest(BaseModel):
    email: Optional[EmailStr] = None
    otp: Optional[str] = None
    token: Optional[str] = None
    new_password: str
    confirm_password: str
    accept_terms: bool = False


def _invite_lifetime() -> timedelta:
    return timedelta(hours=max(1, int(getattr(settings, "INVITE_EXPIRE_HOURS", 72))))


@router.get("/invitation")
def invitation_details(token: str, db: Session = Depends(get_db)):
    """What an invitation link is for (email, role, organization, clinic, expiry), without using it."""
    from app.models.otp import OTPModel
    row = db.query(OTPModel).filter(OTPModel.token_hash == passwords.token_hash(token), OTPModel.purpose == "invite").first()
    if row is None or row.is_used or (row.expires_at and datetime.utcnow() > row.expires_at):
        raise HTTPException(status_code=400, detail="This invitation link is invalid, used or expired")
    user = db.query(User).filter(User.email == row.email).first()
    if user is None or user.is_active:
        raise HTTPException(status_code=400, detail="This invitation link is invalid, used or expired")
    from app.models.organization import OrganizationModel
    from app.models.clinic import ClinicModel
    org = db.query(OrganizationModel).filter(OrganizationModel.id == user.org_id).first() if user.org_id else None
    clinic = db.query(ClinicModel).filter(ClinicModel.id == user.clinic_id).first() if user.clinic_id else None
    return {"email": user.email, "full_name": user.full_name, "role": canonical_role(user.role),
            "role_label": ROLE_LABELS.get(canonical_role(user.role), user.role),
            "organization": org.name if org else None, "clinic": clinic.name if clinic else None,
            "expires_at": row.expires_at.isoformat() + "Z" if row.expires_at else None,
            "terms_version": settings.TERMS_VERSION, "password_policy": passwords.policy()}


@router.post("/accept-invite")
def accept_invite(payload: AcceptInviteRequest, db: Session = Depends(get_db), ip: str = Depends(check_ip_abuse)):
    """First-time activation from an invitation: the terms are accepted, a password following the rule
    and its confirmation is set, and the account becomes active."""
    row = passwords.redeem(db, payload.email, "invite", code=payload.otp, token=payload.token,
                           default_lifetime=_invite_lifetime())
    user = db.query(User).filter(User.email == row.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired link or code")
    if user.is_active:
        raise HTTPException(status_code=400, detail="This account is already active")
    if not payload.accept_terms:
        raise HTTPException(status_code=400, detail="Accept the terms of use to activate your account")
    passwords.validate(payload.new_password, payload.confirm_password, user.email)
    if org_is_suspended(db, user.org_id):
        raise HTTPException(status_code=403, detail="Your organization is suspended")

    passwords.consume(db, row)        # atomic: a second submission of the same link or code fails here
    now = datetime.utcnow()
    user.hashed_password = get_password_hash(payload.new_password)
    user.is_active = True
    user.password_changed_at = now
    user.terms_accepted_at = now
    user.terms_version = settings.TERMS_VERSION
    user.updated_at = now
    passwords.retire_all(db, user.email, "invite")
    record(db, user, "USER_ACTIVATED", "User Management", target_type="user", target_id=user.id,
           detail=f"terms {settings.TERMS_VERSION} accepted")
    db.commit()
    return {"message": "Account has been successfully activated."}


@router.get("/me", response_model=UserResponse)
def read_user_me(current_user: User = Depends(get_current_user)):
    """
    Returns the secure profile of the currently authenticated user.
    """
    return current_user
