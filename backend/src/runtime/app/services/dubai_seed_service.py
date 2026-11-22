"""
UAE Dubai Healthcare Clinic Demo Dataset Service for SBN Sentinel.
Auto-seeds 500 UAE Patients, 789 Encounters, 766 Financial Claims with Denials,
326 Remittances in AED currency, and Sentinel Governance Signals into PostgreSQL/SQLite.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.clinic import ClinicModel
from app.models.encounter import EncounterModel
from app.models.financial_reporting import (
    FinancialClaimModel,
    FinancialClaimLineModel,
    FinancialPaymentReceiptModel,
    FinancialRemittanceModel,
)
from app.models.signal import SignalModel
from app.models.connector import ConnectorModel
from app.models.governance_storage import (
    RuleEvaluationModel,
    RecommendationModel,
)


# Deterministic UAE Clinic Dataset Specs
EMIRATES = ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Fujairah", "Ras Al Khaimah", "Umm Al Quwain", "Al Ain"]
PAYERS = ["Daman", "Orient Insurance", "MetLife", "Bupa Arabia", "Self Pay", "Sukoon", "Cigna", "ADNIC", "AXA Gulf", "NMC Insurance"]
PLANS = ["Gold", "Silver", "Premium", "Standard", "Basic", "Self Pay"]
NATIONALITIES = ["UAE", "India", "Pakistan", "Egypt", "Jordan", "Philippines", "Bangladesh", "UK"]
FIRST_NAMES = ["Mariam", "Reem", "Aisha", "Mona", "Huda", "Khalid", "Tariq", "Mohammed", "Ahmed", "Rashid", "Layla", "Hassan", "Sara", "Ali", "Omar", "Amira", "Fatima", "Saeed", "Noor"]
LAST_NAMES = ["Ali", "Malik", "Verma", "Sharma", "George", "Nair", "Al-Mansoori", "Rahman", "Al-Hassan", "Ahmed", "Al-Nuaimi", "Patel", "Siddiqui", "Khan", "Hussain", "Thomas"]

PROVIDERS = [
    ("Dr. Omar Ali", "ENT"),
    ("Dr. Ahmed Rahman", "Cardiology"),
    ("Dr. Reem Malik", "Family Medicine"),
    ("Dr. Fatima Hassan", "Pediatrics"),
    ("Dr. Sara Khan", "Internal Medicine"),
    ("Dr. Tariq Mansoor", "Orthopedics"),
    ("Dr. Aisha Al-Nuaimi", "Dermatology"),
]

DIAGNOSES = [
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("I10", "Essential (primary) hypertension"),
    ("E78.5", "Hyperlipidemia, unspecified"),
    ("K21.9", "Gastro-esophageal reflux disease without esophagitis"),
    ("M54.50", "Low back pain, unspecified"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("J30.9", "Allergic rhinitis, unspecified"),
    ("R51.9", "Headache, unspecified"),
    ("R10.9", "Abdominal pain, unspecified"),
    ("L30.9", "Dermatitis, unspecified"),
    ("Z00.00", "General adult medical examination without abnormal findings"),
]

CPT_CATALOG = [
    ("99202", "Office/outpatient visit new patient, 15-29 min", 180.0),
    ("99203", "Office/outpatient visit new patient, 30-44 min", 250.0),
    ("99204", "Office/outpatient visit new patient, 45-59 min", 350.0),
    ("99213", "Office/outpatient visit established patient, 20-29 min", 160.0),
    ("99214", "Office/outpatient visit established patient, 30-39 min", 240.0),
    ("71046", "Radiologic examination chest 2 views", 220.0),
    ("93000", "Electrocardiogram routine ECG with interpretation", 198.0),
    ("80053", "Comprehensive metabolic panel", 154.0),
    ("85025", "Blood count complete (CBC) automated", 90.0),
    ("94010", "Spirometry including graphic record and capacity", 198.0),
    ("17110", "Destruction of benign lesions up to 14 lesions", 300.0),
    ("36415", "Routine venipuncture collection of blood specimen", 35.0),
]

DENIAL_REASONS = [
    ("CO16", "Claim lacks information or has billing errors"),
    ("CO50", "These are non-covered services because this is not deemed medical necessity"),
    ("CO15", "Payment adjusted because the submitted authorization number is missing or invalid"),
    ("CO4", "The procedure code is inconsistent with the modifier used or a required modifier is missing"),
    ("CO29", "The time limit for filing has expired"),
    ("PR204", "This service/equipment/drug is not covered under the patient's current benefit plan"),
]


def seed_dubai_data(db: Session):
    """
    Seeds the complete UAE Dubai healthcare dataset into the provided database session.
    """
    from app.core.config import get_deployment_currency
    curr = get_deployment_currency()

    clinic_id = "DXB-CLINIC-01"
    clinic = db.query(ClinicModel).filter(ClinicModel.id == clinic_id).first()
    if not clinic:
        clinic = ClinicModel(
            id=clinic_id,
            name="Sentinel UAE Medical Center - Dubai",
            address="Building 47, Dubai Healthcare City (DHCC), Dubai, UAE",
            phone="+971 4 362 4700",
            status="Active"
        )
        db.add(clinic)
        db.commit()

    # 1. Generate 500 UAE Patients
    patients = []
    base_dob = datetime(1960, 1, 1)
    for i in range(1, 501):
        f = FIRST_NAMES[(i * 7) % len(FIRST_NAMES)]
        last_n = LAST_NAMES[(i * 11) % len(LAST_NAMES)]
        name = f"{f} {last_n}"
        em = EMIRATES[i % len(EMIRATES)]
        nat = NATIONALITIES[(i * 3) % len(NATIONALITIES)]
        payer = PAYERS[(i * 5) % len(PAYERS)]
        plan = PLANS[(i * 2) % len(PLANS)] if payer != "Self Pay" else "Self Pay"
        eid = f"784-19{60 + (i % 40):02d}-{i * 137 % 9000000 + 1000000:07d}-{(i * 3) % 9 + 1}"
        mem_id = f"MEM-UAE-{100000 + i}"
        dob = (base_dob + timedelta(days=i * 53)).strftime("%Y-%m-%d")
        gender = "Female" if i % 2 == 0 else "Male"

        patients.append({
            "patient_id": eid,
            "name": name,
            "dob": dob,
            "gender": gender,
            "emirate": em,
            "nationality": nat,
            "payer": payer,
            "plan": plan,
            "member_id": mem_id,
        })

    # 2. Seed 789 Encounters
    start_date = datetime(2026, 1, 1)
    existing_enc_ids = {e.id for e in db.query(EncounterModel.id).all()}
    encounters_data = []

    for i in range(1, 790):
        enc_id = f"ENC2026{i:06d}"
        p = patients[(i * 5) % len(patients)]
        doc, specialty = PROVIDERS[i % len(PROVIDERS)]
        diag_code, diag_desc = DIAGNOSES[(i * 3) % len(DIAGNOSES)]
        cpt_code, cpt_desc, billed_amt = CPT_CATALOG[i % len(CPT_CATALOG)]
        enc_date = (start_date + timedelta(days=(i * 3) % 280)).strftime("%Y-%m-%d")

        is_denied = (i % 5 == 0) and (p["payer"] != "Self Pay")
        is_paid = (i % 3 == 0) or (p["payer"] == "Self Pay")

        if is_denied:
            billing_status = "Claim Denied"
            d_code, d_desc = DENIAL_REASONS[i % len(DENIAL_REASONS)]
            denial_reason = f"{d_code}: {d_desc}"
        elif is_paid:
            billing_status = "Paid"
            denial_reason = None
        else:
            billing_status = "Billed"
            denial_reason = None

        enc_obj = {
            "id": enc_id,
            "patient_name": p["name"],
            "patient_id": p["patient_id"],
            "payer": p["payer"],
            "member_id": p["member_id"],
            "provider": doc,
            "specialty": specialty,
            "date": enc_date,
            "diagnosis": f"{diag_code} - {diag_desc}",
            "cpt_code": cpt_code,
            "billed_amt": billed_amt,
            "billing_status": billing_status,
            "denial_reason": denial_reason,
        }
        encounters_data.append(enc_obj)

        enc_status = "Completed" if i % 10 != 0 else "In Room"
        enc_stage = "Completed" if enc_status == "Completed" else "Consultation"
        enc_timestamps = {
            "Check In": f"{enc_date}T08:30:00Z",
            "Waiting": f"{enc_date}T08:45:00Z",
            "Consultation": f"{enc_date}T09:00:00Z"
        }
        if enc_status == "Completed":
            enc_timestamps["Billing"] = f"{enc_date}T09:30:00Z"
            enc_timestamps["Completed"] = f"{enc_date}T09:45:00Z"

        if enc_id not in existing_enc_ids:
            encounter = EncounterModel(
                id=enc_id,
                patient_id=p["name"],
                provider_id=doc,
                clinic_id=clinic_id,
                date=enc_date,
                diagnosis=f"{diag_code} - {diag_desc}",
                type=f"{specialty} Consultation",
                status=enc_status,
                journey_stage=enc_stage,
                stage_timestamps=enc_timestamps,
                billing_status=billing_status,
                copay=50.0 if p["payer"] != "Self Pay" else billed_amt,
                priority="Urgent" if "Urgent" in specialty else "Normal",
                wait_time=f"{(i * 7) % 25 + 5} mins",
                department=specialty,
                avatar=None,
                clinical_notes=f"UAE Clinical Visit: Patient {p['name']} presenting with {diag_desc}. Evaluated by {doc} ({specialty}). Payer: {p['payer']}.",
                medications="Standard UAE formulary prescription as indicated",
                payer_network=p["payer"],
                cpt_code=cpt_code,
                billing_amount=billed_amt,
                denial_reason=denial_reason,
            )
            db.add(encounter)
            existing_enc_ids.add(enc_id)

    db.commit()

    # 3. Seed 766 Claims
    existing_claims = {c.claim_id for c in db.query(FinancialClaimModel.claim_id).all()}
    claims_to_add = []
    lines_to_add = []
    for i in range(1, 767):
        clm_id = f"CLM2026{i:06d}"
        if clm_id in existing_claims:
            continue

        enc = encounters_data[i - 1]
        billed = enc["billed_amt"]

        if enc["billing_status"] == "Paid":
            status = "paid"
            allowed = round(billed * 0.85, 2)
            paid = round(billed * 0.80, 2)
            d_code, d_reason = None, None
        elif enc["billing_status"] == "Claim Denied":
            status = "denied"
            allowed = 0.0
            paid = 0.0
            denial = enc["denial_reason"].split(": ") if enc["denial_reason"] else ("CO16", "Missing documentation")
            d_code = denial[0]
            d_reason = denial[1] if len(denial) > 1 else "Claim Denied"
        else:
            status = "submitted"
            allowed = None
            paid = 0.0
            d_code, d_reason = None, None

        serv_date = datetime.strptime(enc["date"], "%Y-%m-%d")

        claim = FinancialClaimModel(
            claim_id=clm_id,
            clinic_id=clinic_id,
            patient_id=enc["patient_id"],
            provider_id=enc["provider"],
            payer_id=enc["payer"],
            status=status,
            billed_amount=billed,
            allowed_amount=allowed,
            paid_amount=paid,
            patient_share=round(billed * 0.15, 2) if enc["payer"] != "Self Pay" else billed,
            currency=curr,
            service_date=serv_date,
            submission_date=serv_date + timedelta(days=2),
            adjudication_date=(serv_date + timedelta(days=14)) if status == "paid" else None,
            rejection_reason=d_reason,
            denial_code=d_code,
        )
        claims_to_add.append(claim)

        line = FinancialClaimLineModel(
            line_id=f"LINE-{clm_id}",
            claim_id=clm_id,
            procedure_code=enc["cpt_code"],
            code_system="CPT",
            code_version="2026",
            units=1,
            billed_amount=billed,
            allowed_amount=allowed,
            paid_amount=paid,
            line_status=status,
            denial_code=d_code,
            denial_reason=d_reason,
        )
        lines_to_add.append(line)
        existing_claims.add(clm_id)

    # Commit claims first to satisfy FK constraints in PostgreSQL
    for c in claims_to_add:
        db.add(c)
    db.commit()

    # Now commit claim lines
    for line_item in lines_to_add:
        db.add(line_item)
    db.commit()

    # 4. Seed 326 Remittances & Receipts
    existing_receipts = {r.receipt_id for r in db.query(FinancialPaymentReceiptModel.receipt_id).all()}
    existing_remits = {rem.remittance_id for rem in db.query(FinancialRemittanceModel.remittance_id).all()}
    remits_to_add = []
    receipts_to_add = []

    for i in range(1, 327):
        rec_id = f"PAY2026{i:06d}"
        if rec_id in existing_receipts:
            continue

        enc = encounters_data[i - 1]
        paid_val = round(enc["billed_amt"] * 0.80, 2)
        remit_id = f"CHK-UAE-{202600 + i}"

        if remit_id not in existing_remits:
            remittance = FinancialRemittanceModel(
                remittance_id=remit_id,
                clinic_id=clinic_id,
                payer_id=enc["payer"],
                payment_reference=remit_id,
                remittance_date=datetime.strptime(enc["date"], "%Y-%m-%d") + timedelta(days=15),
                total_paid=paid_val,
                currency=curr,
            )
            remits_to_add.append(remittance)
            existing_remits.add(remit_id)

        receipt = FinancialPaymentReceiptModel(
            receipt_id=rec_id,
            clinic_id=clinic_id,
            receipt_type="payer_payment" if enc["payer"] != "Self Pay" else "patient_selfpay",
            amount=paid_val,
            currency=curr,
            received_date=datetime.strptime(enc["date"], "%Y-%m-%d") + timedelta(days=15),
            remittance_id=remit_id,
            claim_id=f"CLM2026{i:06d}",
            bank_confirmed=True,
        )
        receipts_to_add.append(receipt)
        existing_receipts.add(rec_id)

    # Commit remittances first to satisfy FK constraints
    for rem in remits_to_add:
        db.add(rem)
    db.commit()

    # Now commit payment receipts
    for rec in receipts_to_add:
        db.add(rec)
    db.commit()

    # 5. Seed Sentinel Governance Signals & Governance Models
    # The UAE policy, its denial rules and mappings are registered through the governance
    # registry (dates without timezone, scope "UAE") so live claim events can use them too.
    from app.services.uae_claims_governance import register_uae_claims_governance, UAE_POLICY_ID
    register_uae_claims_governance()
    policy_id = UAE_POLICY_ID

    existing_signals = {s.id for s in db.query(SignalModel.id).all()}
    uae_governance_rules = [
        ("CO15", "PRIOR_AUTH_OMISSION", "High", 350.0,
         "Pre-bill Prior Authorization Omission",
         "Claim scheduled for submission to Daman lacks mandatory pre-authorization approval reference.",
         "High risk of immediate claim bounce under DHA / DHPO technical rejection rule TR-04.",
         "Attach pre-authorization approval code or request retrospective urgent review.",
         88),
        ("CO4", "MODIFIER_UNBUNDLING", "Critical", 520.0,
         "Procedure Modifier Conflict (Unbundling)",
         "Simultaneous billing of Evaluation & Management (99214) and Minor Surgical Procedure (17110) missing Modifier 25.",
         "Automated payer claim denial under NCCI / UAE Insurance bundling rules.",
         "Append Modifier 25 to separate distinct professional services prior to submission.",
         95),
        ("CO50", "MEDICAL_NECESSITY", "Medium", 220.0,
         "Medical Necessity Discrepancy",
         "Radiology Chest X-Ray (71046) mapped to non-specific diagnosis code without secondary clinical rationale.",
         "Risk of adjudication hold and demand for full medical records review.",
         "Update primary ICD-10 diagnostic coding to reflect clinical findings before billing.",
         78),
        ("CO29", "TIMELY_FILING_RISK", "Critical", 640.0,
         "UAE Timely Filing Horizon Critical (T-4 Days)",
         "Adjudicated claim from Orient Insurance approaching 90-day statutory dispute cutoff.",
         "Unrecoverable bad debt write-off if appeal is not lodged.",
         "Dispatch automated electronic dispute pack with proof of original timely submission.",
         99),
        ("CO16", "DOCUMENTATION_INSUFFICIENCY", "Medium", 280.0,
         "Missing Supporting Clinical Documentation",
         "Sukoon Insurance request for operative note and pathology report for dermatological lesion removal.",
         "Payment delayed past 45-day payment cycle.",
         "Upload signed operative clinical notes directly via DHA unified portal integration.",
         82),
    ]

    for idx, (code, sig_type, risk, exposure, title, prob, impact, action, score) in enumerate(uae_governance_rules):
        sig_id = f"SIG-DXB-2026-{idx + 1:02d}"
        eval_id = f"EVAL-{sig_id}"
        rec_id = f"REC-{sig_id}"
        ctx_id = f"CTX-{sig_id}"

        # Ensure RuleEvaluationModel exists
        if not db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == eval_id).first():
            eval_row = RuleEvaluationModel(
                evaluation_id=eval_id,
                decision_context_id=ctx_id,
                policy_id=policy_id,
                policy_version="2026.04",
                rule_id=f"RULE-UAE-{code}",
                rule_version="2026.1",
                result="VIOLATION_TRIGGERED",
                evaluation_timestamp=(datetime.now(timezone.utc) - timedelta(hours=idx * 6)).isoformat(),
                input_values_json=f'{{"denial_code": "{code}", "exposure": {exposure}}}',
                journey_id=sig_id
            )
            db.add(eval_row)

        # Ensure RecommendationModel exists
        if not db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == rec_id).first():
            rec_row = RecommendationModel(
                recommendation_id=rec_id,
                decision_context_id=ctx_id,
                rule_evaluation_id=eval_id,
                journey_id=sig_id,
                mapping_id=f"MAP-UAE-{code}",
                mapping_version="2026.1",
                content=action,
                status="ACTIVE",
                priority="HIGH" if risk in ["Critical", "High"] else "NORMAL",
                generated_at=(datetime.now(timezone.utc) - timedelta(hours=idx * 6)).isoformat(),
                intended_target_reference=sig_id,
                # Must be an AuthorityRequirement value; a human must approve before any action.
                authority_requirement="APPROVAL_REQUIRED",
                problem=prob,
                business_impact=impact,
                expected_outcome="100% claim preservation and zero revenue leakage."
            )
            db.add(rec_row)

        meta_payload = {
            "correlation_id": sig_id,
            "pipeline_event_id": sig_id,
            "patient_name": f"{FIRST_NAMES[idx % len(FIRST_NAMES)]} {LAST_NAMES[idx % len(LAST_NAMES)]}",
            "payer": PAYERS[idx % len(PAYERS)],
            "cpt_code": CPT_CATALOG[idx % len(CPT_CATALOG)][0],
            "claim_id": f"CLM2026-DXB-{idx + 1:04d}",
            "decision_context_id": ctx_id,
            "exposure_amount": exposure,
            "currency": curr,
            "exposure_aed": exposure if curr == "AED" else None
        }

        if sig_id in existing_signals:
            # Update metadata_data if missing
            existing_s = db.query(SignalModel).filter(SignalModel.id == sig_id).first()
            if existing_s and not existing_s.metadata_data:
                existing_s.metadata_data = meta_payload
            continue

        sig = SignalModel(
            id=sig_id,
            source="Sentinel UAE Governance Engine",
            type=sig_type,
            message=f"[{code}] {title}: Exposure {curr} {exposure:.2f}",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=idx * 6),
            org_id="ORG-UAE-01",
            clinic_id=clinic_id,
            risk_level=risk,
            problem=prob,
            reason=f"Detected by Sentinel Automated Pre-Submission Scrubber matching UAE {code} rules.",
            business_impact=impact,
            recommended_action=action,
            expected_outcome="100% claim preservation and zero revenue leakage.",
            primary_context="Financial Integrity",
            secondary_context="UAE Insurance Adjudication",
            estimated_financial_exposure=f"{curr} {exposure:.2f}",
            priority_score=score,
            metadata_data=meta_payload,
        )
        db.add(sig)
        existing_signals.add(sig_id)

    db.commit()

    # 6. Ensure UAE Clinic Connector exists
    existing_conn = db.query(ConnectorModel).filter(ConnectorModel.id == "conn_dxb_dhpo").first()
    if not existing_conn:
        conn = ConnectorModel(
            id="conn_dxb_dhpo",
            name="DHPO eClaimLink (Dubai Health Authority)",
            type="UAE Clearinghouse / EHR Gateway",
            status="Connected",
            latency_ms=24,
            last_sync=datetime.utcnow()
        )
        db.add(conn)

    db.commit()
    print("✔ Successfully populated UAE Dubai clinic data into database.")


def auto_seed_dubai_if_empty(db: Session):
    """
    Checks if encounters table is empty, and auto-seeds the Dubai demo data if so.
    """
    count = db.query(EncounterModel).count()
    if count == 0:
        seed_dubai_data(db)


# Dubai demo recommendations seeded before this fix carry "ORGANIZATION_ADMINISTRATOR",
# which is a role, not an AuthorityRequirement; loading them failed with ValueError.
_INVALID_SEEDED_AUTHORITY = "ORGANIZATION_ADMINISTRATOR"


def repair_seeded_authority_requirement(db: Session) -> int:
    """Sets the stored authority of previously seeded Dubai recommendations to APPROVAL_REQUIRED."""
    rows = db.query(RecommendationModel).filter(
        RecommendationModel.authority_requirement == _INVALID_SEEDED_AUTHORITY
    ).all()
    for row in rows:
        row.authority_requirement = "APPROVAL_REQUIRED"
    if rows:
        db.commit()
    return len(rows)


_patient_names_by_id = {}


def synthetic_patient_name(patient_id: str) -> str:
    """Name of a seeded Dubai patient (same formula the seed uses for IDs and names)."""
    if not _patient_names_by_id:
        for i in range(1, 501):
            eid = f"784-19{60 + (i % 40):02d}-{i * 137 % 9000000 + 1000000:07d}-{(i * 3) % 9 + 1}"
            _patient_names_by_id[eid] = f"{FIRST_NAMES[(i * 7) % len(FIRST_NAMES)]} {LAST_NAMES[(i * 11) % len(LAST_NAMES)]}"
    return _patient_names_by_id.get(patient_id, patient_id)
