"""MS-001: one password rule and one-time link credentials, shared by signup, invitation, reset and change."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings


def policy() -> dict:
    return {"min_length": max(8, int(getattr(settings, "PASSWORD_MIN_LENGTH", 8))),
            "requires_letter": True, "requires_digit": True}


def validate(password: str, confirm: Optional[str] = None, email: Optional[str] = None):
    """The password rule (configurable minimum length; letters and digits; not the email address) and,
    when given, the confirmation must match."""
    rule = policy()
    password = password or ""
    if len(password) < rule["min_length"]:
        raise HTTPException(status_code=400, detail=f"Password must be at least {rule['min_length']} characters long")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise HTTPException(status_code=400, detail="Password must contain both letters and digits")
    if email and password.lower() == email.lower():
        raise HTTPException(status_code=400, detail="Password must not be your email address")
    if confirm is not None and not hmac.compare_digest(password, confirm):
        raise HTTPException(status_code=400, detail="Password and confirmation do not match")


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def issue(db: Session, email: str, purpose: str, lifetime: timedelta):
    """A new one-time credential for this email and purpose: a 6-digit code and a link token. Every
    earlier unused credential of the same purpose is retired, so only the newest one works."""
    from app.models.otp import OTPModel
    now = datetime.utcnow()
    for old in db.query(OTPModel).filter(OTPModel.email == email, OTPModel.purpose == purpose,
                                         OTPModel.is_used == False).all():  # noqa: E712
        old.is_used = True
        old.used_at = now
    code = str(secrets.randbelow(900000) + 100000)
    token = secrets.token_urlsafe(32)
    row = OTPModel(email=email, otp_code=code, purpose=purpose, created_at=now, token_hash=token_hash(token),
                   expires_at=now + lifetime, attempts=0, is_used=False)
    db.add(row)
    db.flush()
    return row, code, token


def redeem(db: Session, email: Optional[str], purpose: str, code: Optional[str] = None,
           token: Optional[str] = None, default_lifetime: Optional[timedelta] = None):
    """The credential matching a link token, or the newest unused one for the email checked against the
    code (3 attempts). Returns the row; raises 400 when it is unknown, used or expired. The caller marks
    it used in the same transaction as the change it authorises."""
    from app.models.otp import OTPModel
    now = datetime.utcnow()
    bad = HTTPException(status_code=400, detail="Invalid or expired link or code")
    if token:
        row = db.query(OTPModel).filter(OTPModel.token_hash == token_hash(token), OTPModel.purpose == purpose).first()
        if row is None or row.is_used or (email and row.email.lower() != email.lower()):
            raise bad
    else:
        if not email or not code:
            raise bad
        row = db.query(OTPModel).filter(OTPModel.email == email, OTPModel.purpose == purpose,
                                        OTPModel.is_used == False  # noqa: E712
                                        ).order_by(OTPModel.created_at.desc()).first()
        if row is None:
            raise bad
    expires = row.expires_at or (row.created_at + (default_lifetime or timedelta(minutes=settings.OTP_EXPIRE_MINUTES)))
    if now > expires:
        row.is_used = True
        row.used_at = now
        db.commit()
        raise bad
    if not token:
        row.attempts = (row.attempts or 0) + 1
        if row.attempts > 3:
            row.is_used = True
            row.used_at = now
            db.commit()
            raise HTTPException(status_code=400, detail="Maximum attempts exceeded. Please request a new one.")
        if not hmac.compare_digest(row.otp_code or "", code):
            db.commit()
            raise bad
    return row


def retire_all(db: Session, email: str, purpose: str):
    from app.models.otp import OTPModel
    now = datetime.utcnow()
    for r in db.query(OTPModel).filter(OTPModel.email == email, OTPModel.purpose == purpose,
                                       OTPModel.is_used == False).all():  # noqa: E712
        r.is_used = True
        r.used_at = now


def link(path: str, **params) -> str:
    from urllib.parse import urlencode
    base = (getattr(settings, "FRONTEND_BASE_URL", "") or "http://localhost:3000").rstrip("/")
    return f"{base}{path}?{urlencode(params)}"
