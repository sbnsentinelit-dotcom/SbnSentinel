import math
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, status, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from app.db.database import get_db
from app.api.deps import get_scoped_user, RoleChecker
from app.models.user import User, UserRole
from app.models.encounter import EncounterModel
from app.schemas.encounter import EncounterCreate, EncounterUpdate, EncounterResponse
from app.core.config import get_deployment_currency

router = APIRouter()


def seed_default_encounters(db: Session):
    default_encounters = [
        {
            "id": "enc_01",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient reports mild headaches and occasional palpitations. Objective: BP 145/95 mmHg, HR 82 bpm, regular rhythm. Assessment: Unspecified essential hypertension. Plan: Advised low sodium diet. Initiated Lisinopril 10mg daily. Follow-up in 2 weeks.",
            "medications": "Lisinopril 10mg daily"
        },
        {
            "id": "enc_02",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient reports sore throat, congestion, and dry cough for 3 days. Objective: Temp 99.1 F, pharynx erythematous without exudate. Lungs clear to auscultation. Assessment: Acute upper respiratory infection (viral). Plan: Rest, hydration, OTC decongestants. Follow-up if symptoms worsen.",
            "medications": "OTC Decongestants, Saline Nasal Spray"
        },
        {
            "id": "enc_03",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient presents for routine diabetic checkup. Reports compliance with medication. Objective: HbA1c 6.8%, BP 122/78 mmHg. Foot exam normal. Assessment: Controlled Type 2 diabetes mellitus. Plan: Continue Metformin 500mg BID. Recommended annual eye exam.",
            "medications": "Metformin 500mg BID"
        },
        {
            "id": "enc_04",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient reports sharp pain in the right knee after a minor twist yesterday. Objective: Swelling noted over the lateral patellar region. Limited range of motion. Assessment: Suspected ligamentous strain. Plan: X-ray completed to rule out fracture. Rest, Ice, Compression, Elevation.",
            "medications": "Ibuprofen 400mg q6h PRN"
        },
        {
            "id": "enc_05",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient reports chronic burning sensation in chest, worse after meals and when lying down. Objective: Abdomen soft, non-tender, no organomegaly. Assessment: GERD. Plan: Avoid trigger foods. Initiated Omeprazole 20mg daily 30 mins before breakfast.",
            "medications": "Omeprazole 20mg daily"
        },
        {
            "id": "enc_06",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient reports lower back pain radiating down the right leg. Objective: Straight leg raise positive on the right at 45 degrees. Palpable lumbar spasm. Assessment: Sciatica secondary to lumbar strain. Plan: Initiated physical therapy program, heat packs, and gentle stretching.",
            "medications": "Cyclobenzaprine 5mg daily"
        },
        {
            "id": "enc_07",
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
            "avatar": None,
            "clinical_notes": "Subjective: Patient reports persistent worry, insomnia, and muscle tension for over 6 months. Objective: Patient appears anxious, hyperactive fidgeting, speech normal. Assessment: Generalized anxiety disorder. Plan: Recommended Cognitive Behavioral Therapy (CBT). Initiated Sertraline 25mg daily.",
            "medications": "Sertraline 25mg daily"
        }
    ]
    for enc_data in default_encounters:
        enc = EncounterModel(**enc_data)
        db.add(enc)
    db.commit()


def legacy_encounter_query(db: Session):
    """Keep hospital exclusion on every legacy list, aggregate and fallback."""
    try:
        from app.services.hospital_scope_service import hospital_facility_ids
        hids = hospital_facility_ids()
        if hids:
            return db.query(EncounterModel).filter(or_(
                EncounterModel.clinic_id.is_(None),
                EncounterModel.clinic_id.not_in(hids)))
    except (ImportError, Exception):
        pass
    return db.query(EncounterModel)


@router.get("/summary-stats")
def get_encounter_summary_stats(
    clinic_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Lightning-fast SQL aggregated metrics across encounters for high-speed dashboard loading.
    """
    target_clinic = clinic_id or "DXB-CLINIC-01"
    base = legacy_encounter_query(db)
    query = base.filter(EncounterModel.clinic_id == target_clinic)
    if clinic_id is None and query.count() == 0:
        query = base
    total_count = query.count()

    total_charges = query.with_entities(func.coalesce(func.sum(EncounterModel.billing_amount), 0.0)).scalar() or 0.0
    paid_query = query.filter(EncounterModel.billing_status == "Paid")
    paid_charges = paid_query.with_entities(func.coalesce(func.sum(EncounterModel.billing_amount), 0.0)).scalar() or 0.0

    return {
        "total_encounters": total_count,
        "total_charges": float(total_charges),
        "total_paid": float(paid_charges),
        "paid_count": paid_query.count(),
        "pending_count": query.filter(EncounterModel.billing_status.in_(["Pending", "Billed", "Submitted"])).count(),
        "denied_count": query.filter(EncounterModel.billing_status.in_(["Claim Denied", "Denied"])).count(),
        "currency": get_deployment_currency()
    }


def scoped_encounter_query(db: Session, current_user: User, clinic_id: Optional[str] = None):
    """Encounters the caller may see: own clinic for front desk / clinic managers, the requested or own
    clinic for other roles, everything (or the requested clinic) for System Administrators."""
    query = legacy_encounter_query(db)
    if current_user and getattr(current_user, 'role', None) != UserRole.SYSTEM_ADMINISTRATOR.value:
        effective_clinic = clinic_id or getattr(current_user, 'clinic_id', None)
        if getattr(current_user, 'role', None) in [UserRole.FRONT_DESK.value, UserRole.CLINIC_MANAGER.value]:
            if getattr(current_user, 'clinic_id', None):
                query = query.filter(EncounterModel.clinic_id == current_user.clinic_id)
            else:
                query = query.filter(EncounterModel.clinic_id == "UNASSIGNED_FORCE_FAIL")
        elif effective_clinic:
            query = query.filter(EncounterModel.clinic_id == effective_clinic)
    elif clinic_id:
        query = query.filter(EncounterModel.clinic_id == clinic_id)
    return query


def _period_bounds(period: Optional[str]):
    """(period key, start, end) for today / week / month in the clinic's local time."""
    from app.services import revenue_overview as rev
    try:
        key = rev.normalize_period(period)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    start, end, _ps, _pe = rev.period_bounds(key, rev.clinic_today())
    return key, start, end


@router.get("/revenue-overview")
def get_revenue_overview(
    period: Optional[str] = Query("week", description="today | week | month"),
    clinic_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """Revenue Intelligence cards, trends, daily chart and claim-status ring for one period, all
    calculated from the encounters the caller may see."""
    from app.services import revenue_overview as rev
    key, _start, _end = _period_bounds(period)
    return rev.revenue_overview(scoped_encounter_query(db, current_user, clinic_id), key,
                                rev.clinic_today(), get_deployment_currency())


@router.get("/export")
def export_encounters(
    period: Optional[str] = Query("week", description="today | week | month"),
    billing_status: Optional[str] = Query("all", description="all | paid | pending | denied | an exact status"),
    clinic_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """CSV of every billing record in the period that matches the status filter (not just one page)."""
    import csv
    import io
    from app.services import revenue_overview as rev
    key, start, end = _period_bounds(period)
    query = rev.status_filter(rev.in_dates(scoped_encounter_query(db, current_user, clinic_id), start, end),
                              billing_status)
    currency = get_deployment_currency()

    def lines():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(rev.EXPORT_COLUMNS)
        for row in rev.export_rows(query, currency):
            writer.writerow(row)
            if buf.tell() > 64_000:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    slug = "all" if not billing_status or billing_status == "all" else \
        "".join(ch if ch.isalnum() else "_" for ch in billing_status.lower()).strip("_")
    filename = f"sbn_sentinel_revenue_{key}_{start.isoformat()}_{end.isoformat()}_{slug}.csv"
    return StreamingResponse(lines(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                      "Access-Control-Expose-Headers": "Content-Disposition"})


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
    period: Optional[str] = Query(None, description="today | week | month (Revenue Intelligence table)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    Get clinical patient encounters with high-performance server-side pagination and facility scoping.
    """
    def selected_query():
        query = scoped_encounter_query(db, current_user, clinic_id)
        if period:
            from app.services.revenue_overview import in_dates
            _key, start, end = _period_bounds(period)
            query = in_dates(query, start, end)

        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(
                EncounterModel.id.ilike(pattern),
                EncounterModel.patient_id.ilike(pattern),
                EncounterModel.diagnosis.ilike(pattern),
                EncounterModel.provider_id.ilike(pattern),
                EncounterModel.billing_status.ilike(pattern)
            ))
        if billing_status:
            from app.services.revenue_overview import status_filter
            query = status_filter(query, billing_status)
        if status:
            query = query.filter(EncounterModel.status == status)
        return query

    def fetch_page():
        query = selected_query()
        total = query.count()
        response.headers["X-Total-Count"] = str(total)
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Total-Pages"
        query = query.order_by(EncounterModel.id)
        if page is not None:
            size = page_size or 15
            response.headers["X-Total-Pages"] = str(max(1, math.ceil(total / size)))
            query = query.offset((page - 1) * size).limit(size)
        elif limit is not None:
            query = query.offset(skip or 0).limit(limit)
        return query.all(), total

    # A read never writes: an empty clinic stays empty (no demo seeding from this endpoint).
    encounters, _total_count = fetch_page()
    return encounters


@router.post("/seed-dubai", status_code=status.HTTP_200_OK)
@router.post("/seed-dubai/", status_code=status.HTTP_200_OK)
def trigger_seed_dubai(
    db: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker([
        UserRole.SYSTEM_ADMINISTRATOR.value,
        UserRole.ORGANIZATION_ADMINISTRATOR.value,
        UserRole.CLINIC_MANAGER.value
    ]))
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


@router.post("", response_model=EncounterResponse, status_code=status.HTTP_201_CREATED)
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
        patient_id=encounter_in.patient_name,
        provider_id=encounter_in.provider_name,
        clinic_id=effective_clinic,
        date=encounter_in.date,
        diagnosis=encounter_in.diagnosis,
        type=encounter_in.type,
        status=encounter_in.status or "Waiting",
        journey_stage=encounter_in.journey_stage or "Check In",
        stage_timestamps=encounter_in.stage_timestamps or {"Check In": datetime.utcnow().isoformat()},
        billing_status=encounter_in.billing_status or "Pending",
        copay=encounter_in.copay or 245.0,
        priority=encounter_in.priority or "Normal",
        wait_time=encounter_in.wait_time,
        department=encounter_in.department,
        avatar=encounter_in.avatar or None,
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


@router.get("/{encounter_id}/journey")
def get_encounter_journey(
    encounter_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
):
    """
    B-71 / B-72: Authoritative Patient Flow Journey endpoint.
    Returns encounter stages, timestamps, patient details, MRN, provider, department, insurance with clinic scope check.
    Enforces positive facility membership, preserves recorded timestamps without dynamic generation, and defaults missing insurance to 'Unknown'.
    """
    enc = db.query(EncounterModel).filter(EncounterModel.id == encounter_id).first()
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter record not found")

    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        user_clinic = getattr(current_user, 'clinic_id', None)
        if not user_clinic or not enc.clinic_id or enc.clinic_id != user_clinic:
            raise HTTPException(status_code=403, detail="Cannot access encounter belonging to another clinic facility")

    # Visits with events: stages, times, status and wait come from the recorded events.
    from app.services.patient_flow_service import journey_for_visit
    journey = journey_for_visit(db, enc.id)

    timestamps = dict(enc.stage_timestamps or {})
    current_stage = enc.journey_stage
    status_val = enc.status
    wait_val = enc.wait_time
    stage_sources = {}
    if journey:
        timestamps = journey["stage_timestamps"]
        current_stage = journey["current_stage"]
        status_val = journey["status"]
        wait_val = journey["wait_time"]
        stage_sources = journey["stage_sources"]

    # The patient's identifier and MRN: as stored, otherwise from the clinic's patient records by
    # the patient's identifier (never by name, never a default MRN).
    from app.services.workbook_dataset import patients_by_ref
    records = patients_by_ref()
    patient_ref = enc.patient_ref or (enc.patient_id if enc.patient_id in records else None)
    mrn_val = getattr(enc, "mrn", None) or (records.get(patient_ref) or {}).get("mrn") or "Unknown"

    return {
        "encounter_id": enc.id,
        "patient_id": patient_ref,
        "patient_name": enc.patient_name or "Unknown Patient",
        "mrn": mrn_val,
        "provider_name": enc.provider_name or "Unknown Provider",
        "department": enc.department or "Unknown",
        "insurance": enc.payer_network or "Unknown",
        "current_stage": current_stage,
        "stage_timestamps": timestamps,
        "stage_sources": stage_sources,
        "stages": ["Check In", "Waiting", "Consultation", "Lab", "Billing", "Completed"],
        "status": status_val,
        "priority": enc.priority or "Normal",
        "wait_time": wait_val
    }


@router.put("/{encounter_id}", response_model=EncounterResponse)
@router.put("/{encounter_id}/", response_model=EncounterResponse)
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

    from app.core.config import settings
    flow_fields = (encounter_in.status, encounter_in.journey_stage, encounter_in.stage_timestamps,
                   encounter_in.wait_time)
    if getattr(settings, "SOURCE_PROFILE", "") == "workbook" and any(v is not None for v in flow_fields):
        # Report 05 §05.6: a visit's status, stages and wait are read-only in the workbook integration
        # profile (staff reports go to POST /patient-flow/visits/{id}/status as events). Clinical
        # charting (notes, diagnosis, medications) stays editable.
        raise HTTPException(status_code=403, detail="Encounter status is read-only in workbook integration profile (Report 05 §05.6)")

    enc = db.query(EncounterModel).filter(EncounterModel.id == encounter_id).first()
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter record not found")

    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        user_clinic = getattr(current_user, 'clinic_id', None)
        if not user_clinic or not enc.clinic_id or enc.clinic_id != user_clinic:
            raise HTTPException(status_code=403, detail="Cannot modify encounter belonging to another clinic facility")

    # A visit's flow status comes from its events. A person reports a change through
    # POST /patient-flow/visits/{id}/status, which records it as an event with the actor.
    if encounter_in.status is not None or encounter_in.journey_stage is not None \
            or encounter_in.stage_timestamps is not None or encounter_in.wait_time is not None:
        from app.services.patient_flow_service import has_visit_events
        if has_visit_events(db, enc.id):
            raise HTTPException(
                status_code=409,
                detail="This visit's status comes from its recorded events. Report a change with "
                       "POST /api/v1/patient-flow/visits/{id}/status.")

    now_iso = datetime.utcnow().isoformat()
    timestamps = dict(enc.stage_timestamps or {})

    new_stage = encounter_in.journey_stage
    if not new_stage and encounter_in.status:
        status_map = {
            "Check In": "Check In",
            "Waiting": "Waiting",
            "In Room": "Consultation",
            "In Consultation": "Consultation",
            "Consultation": "Consultation",
            "Lab": "Lab",
            "Billing": "Billing",
            "Completed": "Completed",
            "Delayed": "Waiting"
        }
        new_stage = status_map.get(encounter_in.status, enc.journey_stage or "Check In")

    if new_stage:
        enc.journey_stage = new_stage
        if new_stage not in timestamps:
            timestamps[new_stage] = now_iso
        else:
            # B-71: Re-entering a stage preserves first entry as initial and records current entry
            initial_key = f"{new_stage}_initial"
            if initial_key not in timestamps:
                timestamps[initial_key] = timestamps[new_stage]
            timestamps[new_stage] = now_iso
            timestamps[f"{new_stage}_current"] = now_iso
        enc.stage_timestamps = timestamps

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
@router.delete("/{encounter_id}/", status_code=status.HTTP_200_OK)
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
