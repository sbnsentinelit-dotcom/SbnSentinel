from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import random
from typing import Optional

from app.core.config import settings
from app.db.database import get_db
from app.models.insurance import PatientInsuranceModel
from app.models.user import User, UserRole
from app.api.deps import get_scoped_user
from app.schemas.insurance import PatientInsuranceCreate, PatientInsuranceResponse, OCRScanResponse

router = APIRouter()


@router.post("/ocr-scan", response_model=OCRScanResponse)
def simulate_ocr_scan(file_type: str = "image/png"):
    """
    Simulate OCR scanning on a patient's uploaded health insurance card (front/back).
    Extracts Payer Name, Member ID, Group Number, etc.
    """
    if getattr(settings, "ENVIRONMENT", "").upper() == "PRODUCTION" or not getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
        raise HTTPException(
            status_code=403,
            detail="Synthetic simulation disabled in production runtime. Requires source-verified document."
        )
    # Simple list of mock insurance providers
    providers = [
        {"name": "Blue Cross Blue Shield", "payer_id": "BCBS8902"},
        {"name": "Aetna Health", "payer_id": "AETNA1192"},
        {"name": "Cigna Healthcare", "payer_id": "CIGNA9923"},
        {"name": "UnitedHealthcare", "payer_id": "UHC44901"}
    ]
    selected = random.choice(providers)
    member_id = f"MEM{random.randint(100000, 999999)}"
    group_no = f"GRP{random.randint(10000, 99999)}"

    ocr_text = f"""
    INSURANCE CARD FRONT
    -------------------------
    PROVIDER: {selected['name']}
    MEMBER NAME: John Doe
    MEMBER ID: {member_id}
    GROUP NO: {group_no}
    PAYER ID: {selected['payer_id']}
    RX BIN: 610014
    -------------------------
    """

    return OCRScanResponse(
        provider_name=selected['name'],
        member_id=member_id,
        group_number=group_no,
        payer_id=selected['payer_id'],
        confidence_score=0.97,
        ocr_raw_text=ocr_text
    )


@router.post("/verify-eligibility", response_model=PatientInsuranceResponse)
def verify_patient_eligibility(
    insurance_in: PatientInsuranceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Verify patient insurance eligibility against simulated clearinghouse (EDI 270/271).
    Saves or updates the record in the database.
    """
    if current_user.role == UserRole.READ_ONLY_AUDITOR.value:
        raise HTTPException(status_code=403, detail="Read-Only Auditor cannot verify or modify insurance records")
    if getattr(settings, "ENVIRONMENT", "").upper() == "PRODUCTION" or not getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
        raise HTTPException(
            status_code=403,
            detail="Synthetic clearinghouse simulation disabled in production runtime. Requires authenticated EDI 270/271 connection."
        )
    # Check if there is an existing record for the patient
    existing = db.query(PatientInsuranceModel).filter(
        PatientInsuranceModel.patient_name == insurance_in.patient_name
    ).first()

    # Determine eligibility status (Deterministically check for test keywords
    # like EXPIRED, INVALID, INACTIVE)
    mem_upper = (insurance_in.member_id or "").upper()
    prov_upper = (insurance_in.provider_name or "").upper()
    if any(
        k in mem_upper or k in prov_upper for k in [
            "EXPIRE",
            "EXPIRED",
            "INVALID",
            "CANCEL",
            "CANCELLED",
            "INACTIVE"]):
        eligibility = "Inactive"
    else:
        eligibility = "Active"

    copay_p = 20.0 if eligibility == "Active" else 0.0
    copay_s = 45.0 if eligibility == "Active" else 0.0
    deductible = float(random.choice([250, 500, 1000])) if eligibility == "Active" else 0.0

    raw_ocr = f"Verified via clearinghouse gateway on {datetime.now(timezone.utc).isoformat()}"

    if existing:
        existing.provider_name = insurance_in.provider_name
        existing.member_id = insurance_in.member_id
        existing.group_number = insurance_in.group_number
        existing.payer_id = insurance_in.payer_id
        existing.eligibility_status = eligibility
        existing.copay_primary = copay_p
        existing.copay_specialist = copay_s
        existing.deductible = deductible
        existing.ocr_raw_text = raw_ocr
        existing.last_verified = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        new_record = PatientInsuranceModel(
            patient_name=insurance_in.patient_name,
            provider_name=insurance_in.provider_name,
            member_id=insurance_in.member_id,
            group_number=insurance_in.group_number,
            payer_id=insurance_in.payer_id,
            eligibility_status=eligibility,
            copay_primary=copay_p,
            copay_specialist=copay_s,
            deductible=deductible,
            ocr_raw_text=raw_ocr,
            last_verified=datetime.utcnow()
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        return new_record


@router.get("/patient-ref/{patient_ref}")
def get_patient_insurance_by_ref(
    patient_ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Insurance for one patient, found by the patient's identifier (e.g. PAT20260002), never by name:
    several patients share a name. Returns the verified record if one is stored for this identifier
    (verify-eligibility stores it with the identifier as its key) and the payer facts on file in the
    clinic's patient records.
    """
    from app.services.workbook_dataset import patients_by_ref
    on_file = patients_by_ref().get(patient_ref)
    record = db.query(PatientInsuranceModel).filter(PatientInsuranceModel.patient_name == patient_ref).first()
    return {
        "patient_ref": patient_ref,
        "stored": PatientInsuranceResponse.model_validate(record).model_dump(mode="json") if record else None,
        "on_file": ({"payer": on_file.get("payer"), "plan": on_file.get("plan"),
                     "member_id": on_file.get("member_id"), "name": on_file.get("name")} if on_file else None),
    }


@router.get("/patient/{patient_name}", response_model=Optional[PatientInsuranceResponse])
def get_patient_insurance_details(
    patient_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Fetch stored insurance card details & eligibility status for a patient.
    """
    record = db.query(PatientInsuranceModel).filter(
        PatientInsuranceModel.patient_name == patient_name
    ).first()
    if not record:
        # Crosswalk by Patient_ID or MRN (Report 05 §05.5)
        try:
            from app.api.v1.endpoints.patients import _get_all_raw_patients
            for p in _get_all_raw_patients():
                if p.get("patient_id") == patient_name or p.get("mrn") == patient_name:
                    resolved_name = f"{p.get('first_name')} {p.get('last_name')}"
                    record = db.query(PatientInsuranceModel).filter(
                        PatientInsuranceModel.patient_name == resolved_name
                    ).first()
                    if not record and p.get("member_id"):
                        record = db.query(PatientInsuranceModel).filter(
                            PatientInsuranceModel.member_id == p.get("member_id")
                        ).first()
                    break
        except Exception:
            pass
    if not record:
        # Create a default unverified record so user can verify
        return None
    return record
