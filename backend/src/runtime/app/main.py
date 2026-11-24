from fastapi import FastAPI
from app.api.endpoints import jwks
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.v1.api import api_router
from app.models.user import User, UserRole
from app.models.otp import OTPModel  # noqa

# Create tables in SQLite/PostgreSQL (if they don't exist)
# F-28: Transitioned to Alembic migrations.
# Run migrations via `alembic upgrade head` instead of create_all()
# Base.metadata.create_all(bind=engine)

# SES-010: Initialize structured JSON logging for observability
setup_logging(settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO")


def create_app() -> FastAPI:
    """
    SBN Sentinel Backend Factory.
    Initializes the FastAPI application with enterprise configurations.
    """
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        description="Core Intelligence Backend for SBN Sentinel Revenue Architecture."
    )

    # Set all CORS enabled origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all domains including Vercel
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount JWKS directly on app — public URL is /.well-known/jwks.json (NOT under /api/v1)
    app.include_router(jwks.router)
    # Mount the v1 API router under /api/v1
    app.include_router(api_router, prefix=settings.API_V1_STR)

    @app.get("/")
    def root():
        return {
            "message": "Welcome to SBN Sentinel Core Engine",
            "documentation": "/docs"
        }

    @app.on_event("startup")
    def seed_data():
        run_startup_seeding()

    return app


def run_startup_seeding():
    """
    Startup seeding. Governance seeds and the super admin account always; outside production
    (and outside tests) also the demo organization admin and the Dubai demo dataset.
    scripts/reset_demo_data.py calls this after clearing the demo tables.
    """
    import os
    import sys
    from app.services.governance_registry import initialize_registry_seeds
    initialize_registry_seeds()
    # Auto-seed a super_admin account on first startup.
    from app.db.database import SessionLocal
    from app.core.security import get_password_hash
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "superadmin@sbnsentinel.com").first()
        if not existing:
            admin_pwd = (
                os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
                or getattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", None)
            )
            if not admin_pwd:
                import secrets
                admin_pwd = secrets.token_urlsafe(16)
            admin = User(
                email="superadmin@sbnsentinel.com",
                hashed_password=get_password_hash(admin_pwd),
                full_name="SBN Super Admin",
                role=UserRole.SYSTEM_ADMINISTRATOR.value,
                is_active=True
            )
            db.add(admin)
            db.commit()
            print("[SUCCESS] Super Admin seeded: superadmin@sbnsentinel.com / [HIDDEN]")

        is_production = getattr(settings, "ENVIRONMENT", "").upper() in ["PRODUCTION", "PROD"]
        is_testing = "pytest" in sys.modules or os.getenv("TESTING") == "1"
        if is_production:
            # BUG-053: In production, do NOT create demo admin or demo data.
            # Disable demo accounts if present.
            existing_clinic = db.query(User).filter(User.email == "clinicadmin@sbnsentinel.com").first()
            if existing_clinic and existing_clinic.is_active:
                existing_clinic.is_active = False
                db.commit()
                print("[SECURITY] Disabled demo clinicadmin account in production environment.")
        elif not is_testing:
            existing_clinic = db.query(User).filter(User.email == "clinicadmin@sbnsentinel.com").first()
            if not existing_clinic:
                demo_pwd = os.getenv("DEMO_ADMIN_PASSWORD") or getattr(settings, "DEMO_ADMIN_PASSWORD", None)
                if not demo_pwd:
                    import secrets
                    demo_pwd = secrets.token_urlsafe(16)
                clinic_admin = User(
                    email="clinicadmin@sbnsentinel.com",
                    hashed_password=get_password_hash(demo_pwd),
                    full_name="UAE Clinic Administrator",
                    role=UserRole.ORGANIZATION_ADMINISTRATOR.value,
                    # The demo organization admin carries the demo organization's scope;
                    # scope is no longer defaulted at request time.
                    org_id="ORG-UAE-01",
                    clinic_id="DXB-CLINIC-01",
                    is_active=True
                )
                db.add(clinic_admin)
                db.commit()
            elif not existing_clinic.org_id:
                existing_clinic.org_id = "ORG-UAE-01"
                existing_clinic.clinic_id = existing_clinic.clinic_id or "DXB-CLINIC-01"
                db.commit()

            # Auto-seed UAE Dubai clinic demo dataset if encounters is empty (development only)
            try:
                from app.models.encounter import EncounterModel
                from app.services.dubai_seed_service import (
                    auto_seed_dubai_if_empty, repair_seeded_authority_requirement, repair_seeded_revenue_fields)
                enc_count = db.query(EncounterModel).count()
                # The workbook profile stays empty until an explicit import, except in synthetic
                # (demo) mode, where the clinic's workbook is imported and the visit generator runs.
                demo_import = getattr(settings, "SYNTHETIC_TEST_ENABLED", False)
                if enc_count == 0 and (getattr(settings, "SOURCE_PROFILE", "") != "workbook" or demo_import):
                    print("[INFO] Encounters empty on startup. Auto-seeding UAE Dubai clinic data...")
                    auto_seed_dubai_if_empty(db)
                    print("[SUCCESS] UAE Dubai clinic dataset seeded automatically.")
                repaired = repair_seeded_authority_requirement(db)
                if repaired:
                    print(f"[INFO] Repaired authority requirement on {repaired} seeded recommendations.")
                repaired = repair_seeded_revenue_fields(db)
                if repaired:
                    print(f"[INFO] Filled revenue category and dependency on {repaired} claim-denial signals.")
                # UAE claim-denial policy, rules and mappings (also repairs rows from older seeds).
                from app.services.uae_claims_governance import register_uae_claims_governance
                register_uae_claims_governance()
                # Synthetic mode: the visit generator plays the clinic's queue and billing feeds. Store
                # the visit events from the workbook's last visit up to now (at least 14 days, so Patient
                # Flow trends have history; at most 60), and bill the completed visits.
                if getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
                    from app.models.clinic import ClinicModel
                    from datetime import datetime
                    from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY
                    from app.services.visit_generator import history_days, sync_generated_events
                    days = history_days(DEFAULT_FLOW_POLICY.local_date(datetime.utcnow()))
                    for (clinic_id,) in db.query(ClinicModel.id).all():
                        added = sync_generated_events(db, clinic_id, days=days)
                        if added:
                            print(f"[INFO] Stored {added} synthetic visit events for {clinic_id}.")
            except Exception as seed_err:
                print(f"[WARNING] Could not auto-seed dubai data: {seed_err}")

    except Exception as e:
        print(f"[WARNING] Could not seed bootstrap data: {e}")
    finally:
        db.close()


app = create_app()
