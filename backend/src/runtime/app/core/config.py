from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    PROJECT_NAME: str = "SBN Sentinel API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "supersecret_sentinel_key_2026_dev"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    OTP_EXPIRE_MINUTES: int = 15
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "sentinel_db"
    OPENAI_API_KEY: str = ""
    # CP-27: Deterministic V1 boundary - learning engine is disabled by default
    LEARNING_ENGINE_ENABLED: bool = False

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # SMTP / Brevo
    SMTP_SERVER: str = ""
    SMTP_PORT: str = ""
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    BREVO_API_KEY: str = ""

    # EHR
    EHR_CLIENT_ID: str = ""
    EHR_CLIENT_SECRET: str = ""

    # SESR-012 Configuration Integrity
    ENVIRONMENT: str = "DEVELOPMENT"
    DEPLOYMENT_REGION: str = "US"  # G6: "US" | "UAE" regional deployment scope
    CLINIC_TIMEZONE: str = "UTC"
    SYNTHETIC_TEST_ENABLED: bool = False
    BUILD_ID: str = "unknown"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""

    ENABLE_PRACTICE_FUSION: bool = True

    # JWT / JWKS — System App (SMART Backend Services)
    # JWT_PRIVATE_KEY must be a PEM-encoded RSA private key.
    # Never commit a real private key. Set via environment variable in production.
    JWT_PRIVATE_KEY: str = ""
    JWT_KEY_ID: str = "sbn-sentinel-key-1"
    JWT_ALGORITHM: str = "RS384"

    # Runtime database URL — overrides the default SQLite dev URL.
    # In production set: SQLALCHEMY_DATABASE_URL=postgresql://user:pass@host/db
    SQLALCHEMY_DATABASE_URL: str = "sqlite:///./sentinel.db"

    ALLOWED_WS_ORIGINS: list[str] = ["http://localhost:3000", "https://sbnsentinel.com", "https://www.sbnsentinel.com"]

    # Connectors that must be ready for /health/ready to pass, in every environment.
    # Set as a JSON list, e.g. REQUIRED_CONNECTORS='[]' for a deployment with no live EHR connector.
    REQUIRED_CONNECTORS: list[str] = ["PRACTICE_FUSION"]

    @model_validator(mode='after')
    def validate_sesr012_config(self) -> 'Settings':
        if self.ENVIRONMENT == "PRODUCTION":
            if self.LEARNING_ENGINE_ENABLED:
                raise ValueError("CP-27 Violation: LEARNING_ENGINE_ENABLED cannot be enabled in PRODUCTION V1")
            if self.SYNTHETIC_TEST_ENABLED:
                raise ValueError(
                    "SESR-012 CDI-006 Violation: SYNTHETIC_TEST_ENABLED must be disabled in PRODUCTION")
            if self.SECRET_KEY == "supersecret_sentinel_key_2026_dev" or len(self.SECRET_KEY) < 32:
                raise ValueError(
                    "SESR-012 CDI-007 Violation: SECRET_KEY must be a secure, non-default string of at least 32 characters in PRODUCTION")
            # Audit 3 Item 6: Secret Bootstrap
            if self.BOOTSTRAP_ADMIN_PASSWORD == "SBNAdmin@2024" or not self.BOOTSTRAP_ADMIN_PASSWORD:
                raise ValueError(
                    "PRODUCTION_BOOTSTRAP_ERROR: BOOTSTRAP_ADMIN_PASSWORD must be explicitly provided in production and cannot be the default.")
            # G4: Validate credentials per active connector.
            # JWT System App key must be configured in production only when Practice Fusion is enabled.
            if self.ENABLE_PRACTICE_FUSION:
                if not self.JWT_PRIVATE_KEY:
                    raise ValueError(
                        "PRODUCTION_JWT_ERROR: JWT_PRIVATE_KEY must be set in production for SMART System App authentication.")
                if not self.JWT_KEY_ID:
                    raise ValueError(
                        "PRODUCTION_JWT_ERROR: JWT_KEY_ID must be set in production.")
            # F-29: Require PostgreSQL in PRODUCTION
            if not self.SQLALCHEMY_DATABASE_URL.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
        if not self.CLINIC_TIMEZONE:
            raise ValueError("SESR-012 CDI-014 Violation: CLINIC_TIMEZONE is required")
        if self.DEPLOYMENT_REGION not in ("US", "UAE"):
            raise ValueError(f"Invalid DEPLOYMENT_REGION: '{self.DEPLOYMENT_REGION}'. Must be 'US' or 'UAE'.")
        return self

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """Legacy property kept for compatibility. Prefer SQLALCHEMY_DATABASE_URL."""
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}/{self.POSTGRES_DB}"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


def get_deployment_currency(region: Optional[str] = None) -> str:
    """
    Authoritative regional currency mapping:
    UAE -> AED, US -> USD.
    When operating with UAE clinic data or DEPLOYMENT_REGION is UAE, returns AED.
    """
    reg = region or getattr(settings, "DEPLOYMENT_REGION", "US")
    if str(reg).upper() == "UAE":
        return "AED"
    if region is None:
        try:
            from app.db.database import SessionLocal
            from app.models.clinic import ClinicModel
            with SessionLocal() as db:
                uae_clinic = db.query(ClinicModel).filter(
                    (ClinicModel.id.ilike("%dxb%")) | (ClinicModel.name.ilike("%dubai%")) | (ClinicModel.name.ilike("%uae%"))
                ).first()
                if uae_clinic:
                    return "AED"
        except Exception:
            pass
    return "USD"
