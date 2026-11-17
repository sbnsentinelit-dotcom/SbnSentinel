"""Disposable loopback-only HSP01 UI/API sandbox. Never opens an application DB.

Run with the backend virtualenv. All records are synthetic, held in memory and
lost on exit. This tests scoped UI/assignment/export, not a vendor feed or the
governed billing decision engine. No production router includes /uat routes.
"""
from pathlib import Path
import os
import secrets
import sys
import tempfile
import types
import atexit


def main():
    # An empty working directory prevents Settings from loading the project's
    # .env. Replace the DB module BEFORE importing any application component.
    original_directory = os.getcwd()
    scratch = tempfile.TemporaryDirectory(prefix="hsp01-ui-")
    os.chdir(scratch.name)
    # Registered after TemporaryDirectory's finalizer: leave the directory
    # first even if an import or port bind fails (Windows locks the cwd).
    atexit.register(os.chdir, original_directory)
    os.environ.update(ENVIRONMENT="DEVELOPMENT", DEPLOYMENT_REGION="UAE",
        CLINIC_TIMEZONE="Asia/Dubai", SECRET_KEY=secrets.token_hex(32),
        SENTINEL_DISABLE_SCHEMA_AUTOCREATE="1")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "runtime"))
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import declarative_base, sessionmaker
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    sessions = sessionmaker(engine, expire_on_commit=False)
    database = types.ModuleType("app.db.database")
    database.Base = declarative_base()
    database.SessionLocal = sessions
    def get_db():
        with sessions() as db:
            yield db
    database.get_db = get_db
    sys.modules["app.db.database"] = database
    from app.models import organization, clinic, user, signal, encounter, audit, connector, event as event_model, intelligence, governance_storage
    from app.models import evidence, financial_reporting, hospital_dimensions, hospital_membership, hospital_processing, work_assignment
    from app.services.hospital_directory_service import HospitalDirectoryService
    from app.services.work_assignment_service import WorkAssignmentService
    from app.api.v1.endpoints.hospital import router, recommendation_basis
    from app.core.config import settings
    from fastapi import FastAPI, HTTPException
    from datetime import datetime, timezone, timedelta
    from decimal import Decimal
    try:
        from jose import jwt
    except ImportError:
        import jwt
    database.Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with sessions() as db:
        db.add(organization.OrganizationModel(id="uat-org", name="Synthetic UAT Organization")); db.flush()
        for facility, name in [("uat-a", "UAT Hospital A"), ("uat-b", "UAT Hospital B")]:
            db.add(organization.OrganizationClinicModel(id=facility, organization_id="uat-org", name=name))
        db.flush()
        db.add_all([hospital_dimensions.HospitalFacilityEnrollment(clinic_id=value, basis_ref="synthetic:uat") for value in ("uat-a", "uat-b")]); db.flush()
        directory = HospitalDirectoryService(db)
        card = directory.create_department("uat-org", "uat-a", "CARD", "Cardiology", now-timedelta(days=365), "synthetic:uat").id
        rad = directory.create_department("uat-org", "uat-a", "RAD", "Radiology", now-timedelta(days=365), "synthetic:uat").id
        other = directory.create_department("uat-org", "uat-b", "GEN", "General", now-timedelta(days=365), "synthetic:uat").id
        caps = ["report.read", "report.export", "review.read", "assignment.write"]
        for actor_id, label, departments in [(1, "Hospital reviewer", [card, rad]), (2, "Cardiology only", [card]), (3, "Radiology only", [rad]), (4, "No hospital access", [])]:
            db.add(user.User(id=actor_id, email=f"uat-{actor_id}@example.invalid", full_name=label,
                hashed_password="disabled-no-password-login", org_id="uat-org", clinic_id="uat-a", is_active=True))
            db.flush()
            if departments:
                db.add(hospital_membership.HospitalMembership(actor_id=actor_id, clinic_id="uat-a",
                    department_ids=departments, capabilities=caps, all_departments=actor_id == 1,
                    full_record=actor_id == 1, allow_unattributed=actor_id == 1))
        db.add(hospital_membership.HospitalMembership(actor_id=1, clinic_id="uat-b", department_ids=[other],
            capabilities=caps, all_departments=True, full_record=True, allow_unattributed=True))
        db.flush()
        # Explicit preprojected synthetic fixtures. They do not assert a real
        # source import, evidence ledger, approved policy or financial outcome.
        for claim_id, facility, currency, rows in [
            ("uat-claim-a", "uat-a", "AED", [("uat-card", card, "100.10", "60.00"), ("uat-rad", rad, "200.20", None), ("uat-unknown", None, "50.00", "0")]),
            ("uat-claim-usd", "uat-a", "USD", [("uat-usd", card, "9.99", "0")]),
            ("uat-claim-b", "uat-b", "AED", [("uat-other", other, "999.00", "900.00")]),
        ]:
            db.add(financial_reporting.FinancialClaimModel(claim_id=claim_id, clinic_id=facility,
                org_id="uat-org", patient_id="synthetic-patient", provider_id="synthetic-provider",
                payer_id="synthetic-payer", status="submitted", currency=currency,
                billed_amount=sum(Decimal(row[2]) for row in rows), service_date=now.replace(tzinfo=None)))
            db.flush()
            for line_id, department, billed, paid in rows:
                db.add(financial_reporting.FinancialClaimLineModel(line_id=line_id, claim_id=claim_id,
                    clinic_id=facility, currency=currency, department_id=department, attribution_basis="synthetic:uat" if department else None,
                    source_evidence_id="synthetic-fixture:"+line_id, procedure_code="TEST", code_system="TEST",
                    billed_amount=Decimal(billed), paid_amount=Decimal(paid) if paid is not None else None,
                    line_status="submitted"))
        db.add(governance_storage.RecommendationModel(recommendation_id="uat-assignment-fixture",
            decision_context_id="synthetic", rule_evaluation_id="synthetic", journey_id="uat-journey",
            mapping_id="synthetic", mapping_version="1", content="Assignment metadata test only",
            status="SYNTHETIC_ASSIGNMENT_ONLY"))
        db.add(signal.SignalModel(id="uat-signal", org_id="uat-org", clinic_id="uat-a",
            metadata_data={"correlation_id": "uat-journey", "hospital_attribution_basis": "synthetic:uat", "department_id": card}))
        db.flush()
        WorkAssignmentService(db, recommendation_basis).assign(db.get(user.User, 1), {"clinic_id": "uat-a"},
            "uat-assignment-fixture", expected_version=0, assignee="Synthetic reviewer", reason="Synthetic UAT setup")
        db.commit()
    app = FastAPI(title="HSP01 synthetic UI sandbox")
    app.include_router(router, prefix="/api/v1/hospital")
    @app.get("/uat/session/{actor_id}")
    def session(actor_id: int):
        if actor_id not in (1, 2, 3, 4):
            raise HTTPException(404)
        return {"token": jwt.encode({"sub": str(actor_id), "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc)+timedelta(hours=4)}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)}
    print("Synthetic in-memory HSP01 API: http://127.0.0.1:18001 (no application database)", flush=True)
    import uvicorn
    try:
        uvicorn.run(app, host="127.0.0.1", port=18001, access_log=False)
    finally:
        engine.dispose()
        os.chdir(original_directory)
        scratch.cleanup()


if __name__ == "__main__":
    main()
