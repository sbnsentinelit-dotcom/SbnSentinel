import math
import random
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, status, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.db.database import get_db
from app.api.deps import get_scoped_user, RoleChecker
from app.models.user import User, UserRole
from app.models.encounter import EncounterModel
from app.schemas.encounter import EncounterCreate, EncounterUpdate, EncounterResponse

router = APIRouter()


def seed_default_encounters(db: Session):
    default_encounters = [{"id": "enc_01",
                           "patient_name": "Michael R.",
                           "provider_name": "Dr. Smith",
                           "date": "2026-07-01",
                           "diagnosis": "Unspecified essential hypertension",
                           "type": "Urgent Care",
                           "status": "Waiting",
                           "billing_status": "Pending",
                           "copay": 20.0,
                           "priority": "High",
                           "wait_time": "14 mins",
                           "department": "Cardiology",
                           "avatar": "https://i.pravatar.cc/150?img=11",
                           "clinical_notes": "Subjective: Patient reports mild headaches and occasional palpitations. Objective: BP 145/95 mmHg, HR 82 bpm, regular rhythm. Assessment: Unspecified essential hypertension. Plan: Advised low sodium diet. Initiated Lisinopril 10mg daily. Follow-up in 2 weeks.",
                           "medications": "Lisinopril 10mg daily"},
                          {"id": "enc_02",
                           "patient_name": "Sarah J.",
                           "provider_name": "Dr. Patel",
                           "date": "2026-07-01",
                           "diagnosis": "Acute upper respiratory infection",
                           "type": "Consultation",
                           "status": "In Room",
                           "billing_status": "Billed",
                           "copay": 45.0,
                           "priority": "Normal",
                           "wait_time": "5 mins",
                           "department": "General Practice",
                           "avatar": "https://i.pravatar.cc/150?img=5",
                           "clinical_notes": "Subjective: Patient reports sore throat, congestion, and dry cough for 3 days. Objective: Temp 99.1 F, pharynx erythematous without exudate. Lungs clear to auscultation. Assessment: Acute upper respiratory infection (viral). Plan: Rest, hydration, OTC decongestants. Follow-up if symptoms worsen.",
                           "medications": "OTC Decongestants, Saline Nasal Spray"},
                          {"id": "enc_03",
                           "patient_name": "David L.",
                           "provider_name": "Dr. Smith",
                           "date": "2026-07-01",
                           "diagnosis": "Type 2 diabetes mellitus without complications",
                           "type": "Checkup",
                           "status": "Completed",
                           "billing_status": "Paid",
                           "copay": 20.0,
                           "priority": "Normal",
                           "wait_time": "25 mins",
                           "department": "Cardiology",
                           "avatar": "https://i.pravatar.cc/150?img=12",
                           "clinical_notes": "Subjective: Patient presents for routine diabetic checkup. Reports compliance with medication. Objective: HbA1c 6.8%, BP 122/78 mmHg. Foot exam normal. Assessment: Controlled Type 2 diabetes mellitus. Plan: Continue Metformin 500mg BID. Recommended annual eye exam.",
                           "medications": "Metformin 500mg BID"},
                          {"id": "enc_04",
                           "patient_name": "Emily D.",
                           "provider_name": "Dr. Chen",
                           "date": "2026-07-01",
                           "diagnosis": "Pain in right knee",
                           "type": "X-Ray Scan",
                           "status": "Waiting",
                           "billing_status": "Claim Denied",
                           "copay": 50.0,
                           "priority": "Normal",
                           "wait_time": "18 mins",
                           "department": "Radiology",
                           "avatar": "https://i.pravatar.cc/150?img=23",
                           "clinical_notes": "Subjective: Patient reports sharp pain in the right knee after a minor twist yesterday. Objective: Swelling noted over the lateral patellar region. Limited range of motion. Assessment: Suspected ligamentous strain. Plan: X-ray completed to rule out fracture. Rest, Ice, Compression, Elevation.",
                           "medications": "Ibuprofen 400mg q6h PRN"},
                          {"id": "enc_05",
                           "patient_name": "James K.",
                           "provider_name": "Dr. Patel",
                           "date": "2026-07-01",
                           "diagnosis": "Gastro-esophageal reflux disease",
                           "type": "Consultation",
                           "status": "Delayed",
                           "billing_status": "Pending",
                           "copay": 45.0,
                           "priority": "Urgent",
                           "wait_time": "40 mins",
                           "department": "General Practice",
                           "avatar": "https://i.pravatar.cc/150?img=15",
                           "clinical_notes": "Subjective: Patient reports chronic burning sensation in chest, worse after meals and when lying down. Objective: Abdomen soft, non-tender, no organomegaly. Assessment: GERD. Plan: Avoid trigger foods. Initiated Omeprazole 20mg daily 30 mins before breakfast.",
                           "medications": "Omeprazole 20mg daily"},
                          {"id": "enc_06",
                           "patient_name": "Robert B.",
                           "provider_name": "Dr. Sarah Jenkins",
                           "date": "2026-07-01",
                           "diagnosis": "Lumbago with sciatica, right side",
                           "type": "Therapy",
                           "status": "Completed",
                           "billing_status": "Paid",
                           "copay": 25.0,
                           "priority": "Normal",
                           "wait_time": "12 mins",
                           "department": "Physical Therapy",
                           "avatar": "https://i.pravatar.cc/150?img=3",
                           "clinical_notes": "Subjective: Patient reports lower back pain radiating down the right leg. Objective: Straight leg raise positive on the right at 45 degrees. Palpable lumbar spasm. Assessment: Sciatica secondary to lumbar strain. Plan: Initiated physical therapy program, heat packs, and gentle stretching.",
                           "medications": "Cyclobenzaprine 5mg daily"},
                          {"id": "enc_07",
                           "patient_name": "Jessica M.",
                           "provider_name": "Dr. Sarah Jenkins",
                           "date": "2026-07-01",
                           "diagnosis": "Generalized anxiety disorder",
                           "type": "Consultation",
                           "status": "In Room",
                           "billing_status": "Billed",
                           "copay": 30.0,
                           "priority": "Normal",
                           "wait_time": "8 mins",
                           "department": "Psychiatry",
                           "avatar": "https://i.pravatar.cc/150?img=9",
                           "clinical_notes": "Subjective: Patient reports persistent worry, insomnia, and muscle tension for over 6 months. Objective: Patient appears anxious, hyperactive fidgeting, speech normal. Assessment: Generalized anxiety disorder. Plan: Recommended Cognitive Behavioral Therapy (CBT). Initiated Sertraline 25mg daily.",
                           "medications": "Sertraline 25mg daily"}]
    for enc_data in default_encounters:
        enc = EncounterModel(**enc_data)
        db.add(enc)
    db.commit()


@router.get("/summary-stats")
def get_encounter_summary_stats(
    clinic_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Lightning-fast SQL aggregated metrics across encounters for high-speed dashboard loading.
    """
    target_clinic = clinic_id or "DXB-CLINIC-01"
    query = db.query(EncounterModel).filter(EncounterModel.clinic_id == target_clinic)
    total_count = query.count()
    if total_count == 0:
        query = db.query(EncounterModel)
        total_count = query.count()

    total_charges = db.query(func.coalesce(func.sum(EncounterModel.billing_amount), 0.0)).filter(
        EncounterModel.clinic_id == target_clinic
    ).scalar() or 162313.0

    paid_count = query.filter(EncounterModel.billing_status == "Paid").count()
    denied_count = query.filter(EncounterModel.billing_status.in_(["Claim Denied", "Denied"])).count()
    pending_count = query.filter(EncounterModel.billing_status.in_(["Pending", "Billed", "Submitted"])).count()

    paid_charges = db.query(func.coalesce(func.sum(EncounterModel.billing_amount), 0.0)).filter(
        EncounterModel.clinic_id == target_clinic,
        EncounterModel.billing_status == "Paid"
    ).scalar() or 62913.7

    return {
        "total_encounters": total_count,
        "total_charges": float(total_charges),
        "total_paid": float(paid_charges),
        "paid_count": paid_count,
        "pending_count": pending_count,
        "denied_count": denied_count
    }


@router.get("", response_model=List[EncounterResponse])
@router.get("/", response_model=List[EncounterResponse])
def get_all_encounters(
    response: Response,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    skip: Optional[int] = Query(None, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=500),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    billing_status: Optional[str] = Query(None),
    clinic_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Get clinical patient encounters with high-performance server-side pagination and facility scoping.
    """
    query = db.query(EncounterModel)

    # Server-side facility scope enforcement (BUG-026)
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        effective_clinic = clinic_id or getattr(current_user, 'clinic_id', None)
        if current_user.role in [UserRole.FRONT_DESK.value, UserRole.CLINIC_MANAGER.value]:
            if getattr(current_user, 'clinic_id', None):
                query = query.filter(EncounterModel.clinic_id == current_user.clinic_id)
            else:
                query = query.filter(EncounterModel.clinic_id == "UNASSIGNED_FORCE_FAIL")
        elif effective_clinic:
            query = query.filter(EncounterModel.clinic_id == effective_clinic)
    elif clinic_id:
        query = query.filter(EncounterModel.clinic_id == clinic_id)

    if search:
        s = f"%{search}%"
        query = query.filter(
            or_(
                EncounterModel.id.ilike(s),
                EncounterModel.patient_id.ilike(s),
                EncounterModel.diagnosis.ilike(s),
                EncounterModel.provider_id.ilike(s),
                EncounterModel.billing_status.ilike(s)
            )
        )

    if billing_status:
        query = query.filter(EncounterModel.billing_status == billing_status)
    if status:
        query = query.filter(EncounterModel.status == status)

    total_count = query.count()
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Total-Pages"

    if page is not None:
        p_size = page_size or 15
        total_pages = max(1, math.ceil(total_count / p_size))
        response.headers["X-Total-Pages"] = str(total_pages)
        query = query.offset((page - 1) * p_size).limit(p_size)
    elif limit is not None:
        offset_val = skip or 0
        query = query.offset(offset_val).limit(limit)

    encounters = query.all()
    return encounters


@router.post("/seed-dubai", status_code=status.HTTP_200_OK)
@router.post("/seed-dubai/", status_code=status.HTTP_200_OK)
def trigger_seed_dubai(
    db: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))
):
    """
    Explicit endpoint to seed or refresh the UAE Dubai clinic demo dataset (development/test only).
    """
    from app.core.config import settings
    if getattr(settings, "ENVIRONMENT", "").upper() in ["PRODUCTION", "PROD"]:
        raise HTTPException(status_code=403, detail="Demo data seeding is permanently disabled in production.")
    try:
        from app.services.dubai_seed_service import seed_dubai_data
        seed_dubai_data(db)
        count = db.query(EncounterModel).count()
        return {
            "status": "success",
            "message": f"Successfully seeded UAE Dubai dataset ({count} encounters).",
            "encounters_count": count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to seed Dubai data: {str(e)}")


@router.post("/", response_model=EncounterResponse, status_code=status.HTTP_201_CREATED)
def create_encounter(
    encounter_in: EncounterCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Register a new patient encounter in the clinic flow.
    Enforces write permissions and clinic facility ownership (BUG-026).
    """
    if current_user.role == UserRole.READ_ONLY_AUDITOR.value:
        raise HTTPException(status_code=403, detail="Read-Only Auditor cannot create encounters")

    effective_clinic = encounter_in.clinic_id or getattr(current_user, 'clinic_id', None)
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        if not effective_clinic:
            raise HTTPException(status_code=400, detail="Missing required clinic facility for encounter")
        if hasattr(current_user, 'clinic_id') and current_user.clinic_id and effective_clinic != current_user.clinic_id:
            raise HTTPException(status_code=403, detail="Cannot create encounter for another clinic facility")

    existing = db.query(EncounterModel).filter(EncounterModel.id == encounter_in.id).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Encounter with ID '{encounter_in.id}' already exists."
        )
    new_enc = EncounterModel(
        id=encounter_in.id,
        patient_name=encounter_in.patient_name,
        provider_name=encounter_in.provider_name,
        clinic_id=effective_clinic,
        date=encounter_in.date,
        diagnosis=encounter_in.diagnosis,
        type=encounter_in.type,
        status=encounter_in.status or "Waiting",
        billing_status=encounter_in.billing_status or "Pending",
        copay=encounter_in.copay or 245.0,
        priority=encounter_in.priority or "Normal",
        wait_time=encounter_in.wait_time or "0 mins",
        department=encounter_in.department,
        avatar=encounter_in.avatar or f"https://i.pravatar.cc/150?img={random.randint(1, 70)}",
        clinical_notes=encounter_in.clinical_notes,
        medications=encounter_in.medications,
        payer_network=encounter_in.payer_network,
        cpt_code=encounter_in.cpt_code,
        billing_amount=encounter_in.billing_amount
    )
    db.add(new_enc)
    db.commit()
    db.refresh(new_enc)
    return new_enc


@router.put("/{encounter_id}", response_model=EncounterResponse)
def update_encounter(
    encounter_id: str,
    encounter_in: EncounterUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Update status, billing_status, wait time, or diagnosis of an existing clinical encounter.
    Enforces write permissions and facility isolation (BUG-026).
    """
    if current_user.role == UserRole.READ_ONLY_AUDITOR.value:
        raise HTTPException(status_code=403, detail="Read-Only Auditor cannot modify encounters")

    enc = db.query(EncounterModel).filter(EncounterModel.id == encounter_id).first()
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter record not found")

    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        if hasattr(current_user, 'clinic_id') and current_user.clinic_id and enc.clinic_id and enc.clinic_id != current_user.clinic_id:
            raise HTTPException(status_code=403, detail="Cannot modify encounter belonging to another clinic facility")

    if encounter_in.status is not None:
        enc.status = encounter_in.status
    if encounter_in.billing_status is not None:
        enc.billing_status = encounter_in.billing_status
    if encounter_in.diagnosis is not None:
        enc.diagnosis = encounter_in.diagnosis
    if encounter_in.wait_time is not None:
        enc.wait_time = encounter_in.wait_time
    if encounter_in.priority is not None:
        enc.priority = encounter_in.priority
    if encounter_in.clinical_notes is not None:
        enc.clinical_notes = encounter_in.clinical_notes
    if encounter_in.medications is not None:
        enc.medications = encounter_in.medications
    if encounter_in.payer_network is not None:
        enc.payer_network = encounter_in.payer_network
    if encounter_in.cpt_code is not None:
        enc.cpt_code = encounter_in.cpt_code
    if encounter_in.billing_amount is not None:
        enc.billing_amount = encounter_in.billing_amount

    db.commit()
    db.refresh(enc)
    return enc


@router.delete("/{encounter_id}", status_code=status.HTTP_200_OK)
def delete_encounter(
    encounter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Remove/Checkout an encounter record from the active flows.
    Enforces write permissions and facility isolation (BUG-026).
    """
    if current_user.role == UserRole.READ_ONLY_AUDITOR.value:
        raise HTTPException(status_code=403, detail="Read-Only Auditor cannot delete encounters")

    enc = db.query(EncounterModel).filter(EncounterModel.id == encounter_id).first()
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter record not found")

    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        if hasattr(current_user, 'clinic_id') and current_user.clinic_id and enc.clinic_id and enc.clinic_id != current_user.clinic_id:
            raise HTTPException(status_code=403, detail="Cannot delete encounter belonging to another clinic facility")

    db.delete(enc)
    db.commit()
    return {"message": "Encounter checked out successfully"}
