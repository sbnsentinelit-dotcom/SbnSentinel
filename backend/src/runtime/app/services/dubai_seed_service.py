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


DEMO_ORG_ID = "ORG-UAE-01"
DEMO_ORG_NAME = "Sentinel UAE Medical Center"


def record_existing_workbook_import(db: Session) -> int:
    """A database imported before import records existed: the demo clinic's encounters whose ids are
    the workbook's own ids are recorded as that workbook import, once. Nothing else is counted."""
    from app.models.integration_evidence import DataImportModel
    from app.services.workbook_dataset import encounters as workbook_encounters
    clinic_id = "DXB-CLINIC-01"
    if db.query(DataImportModel).filter(DataImportModel.clinic_id == clinic_id,
                                        DataImportModel.source_kind == "workbook").first():
        return 0
    ids = {w["encounter_id"] for w in workbook_encounters()}
    present = sum(1 for (eid,) in db.query(EncounterModel.id).filter(EncounterModel.clinic_id == clinic_id).all()
                  if eid in ids)
    if present:
        db.add(DataImportModel(clinic_id=clinic_id, source_kind="workbook", source_label="Clinic billing workbook",
                               accepted_count=present, completed_at=datetime.utcnow(),
                               recorded_by="recorded from the workbook's encounter ids"))
        db.commit()
    return present


def ensure_demo_organization(db: Session):
    """The demo organization that owns the demo clinic (MS-001: every clinic belongs to an organization)."""
    from app.models.organization import OrganizationModel
    org = db.query(OrganizationModel).filter(OrganizationModel.id == DEMO_ORG_ID).first()
    if org is None:
        now = datetime.utcnow()
        org = OrganizationModel(id=DEMO_ORG_ID, name=DEMO_ORG_NAME, status="Active", time_zone="Asia/Dubai",
                                created_date=now, updated_at=now)
        db.add(org)
        db.commit()
    elif org.name == DEMO_ORG_ID:
        # Created by migration r1 from the id alone; the demo organization has a name.
        org.name = DEMO_ORG_NAME
        org.updated_at = datetime.utcnow()
        db.commit()
    clinic = db.query(ClinicModel).filter(ClinicModel.id == "DXB-CLINIC-01").first()
    if clinic is not None and not clinic.org_id:
        clinic.org_id = DEMO_ORG_ID
        db.commit()
    return org


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
            status="Active",
            org_id=DEMO_ORG_ID,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(clinic)
        db.commit()
    ensure_demo_organization(db)

    # 1-2. Patients and encounters come from the clinic's UAE billing workbook (client_excel.json):
    # 500 patients (directory reads them from the workbook) and 789 historical encounters with their
    # own identifiers. Nothing is invented here: no live statuses, waits or stage times. Today's live
    # visits are produced as visit events by the visit generator, and every Patient Flow number is
    # calculated from those events.
    from app.services.workbook_dataset import encounters as workbook_encounters

    existing_enc_ids = {e.id for e in db.query(EncounterModel.id).all()}
    encounters_data = []  # encounters that have a claim, in claim order (CLM2026000001 is the first)
    imported_now = 0

    for w in workbook_encounters():
        enc_id = w["encounter_id"]
        diagnosis = (f"{w['diagnosis_code']} - {w['diagnosis_description']}"
                     if w.get("diagnosis_code") else w.get("diagnosis_description"))
        cancelled = w.get("status") == "Cancelled"
        if w.get("billing_status") in ("Paid", "Claim Denied", "Billed"):
            encounters_data.append({
                "id": enc_id,
                "patient_name": w["patient_name"],
                "patient_id": w["patient_ref"],
                "payer": w.get("payer") or "Self Pay",
                "provider": w["provider"],
                "specialty": w.get("specialty"),
                "date": w["date"],
                "diagnosis": diagnosis,
                "cpt_code": w.get("cpt_code"),
                "billed_amt": w.get("billed_amount") or 0.0,
                "billing_status": w["billing_status"],
                "denial_reason": w.get("denial_reason"),
            })

        if enc_id not in existing_enc_ids:
            encounter = EncounterModel(
                id=enc_id,
                patient_id=w["patient_name"],
                patient_ref=w["patient_ref"],
                mrn=w.get("mrn"),
                provider_id=w["provider"],
                clinic_id=clinic_id,
                date=w["date"],
                diagnosis=diagnosis,
                type=f"{w.get('specialty')} {w.get('visit_type') or 'Consultation'}",
                status=w.get("status") or None,
                journey_stage=None if cancelled else "Completed",
                stage_timestamps=None,
                billing_status=w.get("billing_status"),
                copay=w.get("patient_responsibility"),
                priority="Urgent" if w.get("visit_type") == "Urgent" else "Normal",
                wait_time=None,
                department=w.get("specialty"),
                avatar=None,
                clinical_notes=(f"UAE Clinical Visit: Patient {w['patient_name']} evaluated by {w['provider']} "
                                f"({w.get('specialty')}). Diagnosis: {diagnosis}. Payer: {w.get('payer')}."),
                medications=None,
                payer_network=w.get("payer"),
                cpt_code=w.get("cpt_code"),
                billing_amount=w.get("billed_amount"),
                denial_reason=w.get("denial_reason"),
            )
            db.add(encounter)
            existing_enc_ids.add(enc_id)
            imported_now += 1

    if imported_now:
        from app.models.integration_evidence import DataImportModel
        db.add(DataImportModel(clinic_id=clinic_id, source_kind="workbook", source_label="Clinic billing workbook",
                               accepted_count=imported_now, completed_at=datetime.utcnow(),
                               recorded_by="workbook import"))
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

        # A denied claim usually covers the visit plus procedures or tests billed with it, so the
        # synthetic denied claims carry 1-3 extra service lines (the claim total is their sum).
        visit_amount = billed
        extra_lines = []
        if status == "denied":
            for k in range((i % 3) + 1):
                extra_lines.append(CPT_CATALOG[(i * (k + 3) + k) % len(CPT_CATALOG)])
            billed = round(visit_amount + sum(amount for _, _, amount in extra_lines), 2)

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
            billed_amount=visit_amount,
            allowed_amount=allowed,
            paid_amount=paid,
            line_status=status,
            denial_code=d_code,
            denial_reason=d_reason,
        )
        lines_to_add.append(line)
        for k, (extra_code, _extra_desc, extra_amount) in enumerate(extra_lines, start=1):
            lines_to_add.append(FinancialClaimLineModel(
                line_id=f"LINE-{clm_id}-{k}",
                claim_id=clm_id,
                procedure_code=extra_code,
                code_system="CPT",
                code_version="2026",
                units=1,
                billed_amount=extra_amount,
                allowed_amount=0.0,
                paid_amount=0.0,
                line_status=status,
                denial_code=d_code,
                denial_reason=d_reason,
            ))
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
         "High risk of immediate claim bounce under the DHA technical rejection rule TR-04.",
         "Attach pre-authorization approval code or request retrospective urgent review.",
         88),
        ("CO4", "MODIFIER_UNBUNDLING", "Critical", 520.0,
         "Procedure Modifier Conflict (Unbundling)",
         "Simultaneous billing of Evaluation & Management (99214) and Minor Surgical Procedure (17110) missing Modifier 25.",
         "Automated payer claim denial under NCCI / UAE Insurance bundling rules.",
         "Append Modifier 25 to separate distinct professional services prior to submission.",
         95),
        ("CO50", "MEDICAL_NECESSITY", "Moderate", 220.0,
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
        ("CO16", "DOCUMENTATION_INSUFFICIENCY", "Moderate", 280.0,
         "Missing Supporting Clinical Documentation",
         "Sukoon Insurance request for operative note and pathology report for dermatological lesion removal.",
         "Payment delayed past 45-day payment cycle.",
         "Upload signed operative clinical notes directly via DHA unified portal integration.",
         82),
        ("CO29", "TIMELY_FILING_RISK", "High", 760.0,
         "Original Claim Filing Deadline",
         "Original claim filing is due with the payer today; the electronic dispute pack is ready for submission.",
         "Unrecoverable write-off if the payer filing deadline is missed.",
         "Submit the electronic dispute pack before the payer filing deadline.",
         97),
    ]

    # Payer resubmission deadline (days from now) as the payer / clearinghouse would report it for each
    # seeded denial; None = not received yet. The risk level is evaluated from this data, not fixed per code.
    seed_deadline_days = {0: 6, 1: 1.5, 2: 10, 3: 4, 4: None}
    # The sixth claim's filing deadline is the payer's 5:00 PM UAE cutoff (today, or tomorrow once it has
    # passed); its one recovery route is the electronic dispute pack submission, 12-18 minutes end to end
    # (verified bounds from the clearinghouse), with no advance warning lead.
    local_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=4)
    cutoff_local = local_now.replace(hour=17, minute=0, second=0, microsecond=0)
    if cutoff_local <= local_now:
        cutoff_local += timedelta(days=1)
    filing_deadline_utc = cutoff_local - timedelta(hours=4)
    route_data = {5: {"recovery_routes": [{"name": "Electronic dispute pack submission", "shortest_minutes": 12,
                                           "longest_minutes": 18, "verified": True, "ref": "ROUTE-EDP-SUBMIT"}],
                      "route_inventory_complete": True, "alert_lead_minutes": 0}}
    from app.services.risk_level_evaluator import CLAIM_DENIAL, evaluate as evaluate_risk
    for idx, (code, sig_type, _baseline, exposure, title, prob, impact, action, score) in enumerate(uae_governance_rules):
        days = seed_deadline_days.get(idx)
        seed_now = datetime.now(timezone.utc).replace(tzinfo=None)
        from app.services.uae_claims_governance import SYNTHETIC_REQUIRED_EVIDENCE
        risk_inputs = {
            "source": "Payer remittance + EHR (synthetic)",
            "claim_id": f"CLM2026-DXB-{idx + 1:04d}",
            "payer": PAYERS[idx % len(PAYERS)],
            "denial_reason": prob,
            "required_evidence": list(SYNTHETIC_REQUIRED_EVIDENCE.get(code, [])),
            "evidence_available": list(SYNTHETIC_REQUIRED_EVIDENCE.get(code, [])),
            "denial_code": code,
            "denied_amount": f"{exposure:.2f}",
            "currency": curr,
            "remittance_date": (seed_now - timedelta(days=3)).isoformat() + "Z",
            "resubmission_deadline": (seed_now + timedelta(days=days)).isoformat() + "Z" if days is not None else None,
            **route_data.get(idx, {}),
        }
        if idx in route_data:
            risk_inputs["resubmission_deadline"] = filing_deadline_utc.isoformat() + "Z"
        risk_eval = evaluate_risk(CLAIM_DENIAL, risk_inputs, seed_now)
        risk = risk_eval.level
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
            "exposure_aed": exposure if curr == "AED" else None,
            "risk_kind": CLAIM_DENIAL,
            "risk_inputs": risk_inputs,
            "risk_evaluation": risk_eval.to_dict(),
            "origin": {"kind": "simulator", "import_id": None, "sheet": None,
                       "external_record_id": None, "connector_instance_id": None},
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
            problem=risk_eval.problem,
            reason=risk_eval.reason,
            business_impact=impact,
            recommended_action=risk_eval.recommended_action,
            expected_outcome="100% claim preservation and zero revenue leakage.",
            primary_context="Financial Integrity",
            secondary_context="UAE Insurance Adjudication",
            estimated_financial_exposure=f"{curr} {exposure:.2f}",
            **_claim_revenue_fields(code, exposure, curr),
            priority_score=score,
            metadata_data=meta_payload,
        )
        db.add(sig)
        existing_signals.add(sig_id)

    db.commit()

    # The clinic's claim review roster (read by the review-capacity check).
    from app.models.review_roster import ReviewRosterModel
    if not db.query(ReviewRosterModel).filter(ReviewRosterModel.clinic_id == clinic_id).first():
        for reviewer, role, start, end, assigned in (
                ("Biller A", "Claims reviewer", "08:00", "17:00", True),
                ("Biller B", "Backup reviewer", "16:45", "17:00", False)):
            db.add(ReviewRosterModel(clinic_id=clinic_id, reviewer=reviewer, role=role, qualified=True,
                                     shift_start=start, shift_end=end, assigned=assigned, source="clinic roster",
                                     updated_at=datetime.utcnow(), updated_by="clinic onboarding"))
        db.commit()

    # The clinic's rooms (their state is calculated from visit events).
    from app.services.patient_flow_service import ensure_rooms
    ensure_rooms(db, clinic_id)
    db.commit()

    # 6. Ensure UAE Clinic Connector exists (Only outside workbook profile)
    from app.core.config import settings
    if getattr(settings, "SOURCE_PROFILE", "") != "workbook":
        existing_conn = db.query(ConnectorModel).filter(ConnectorModel.id == "conn_dxb_dhpo").first()
        if not existing_conn:
            # Registered for the demo clinic, never verified with the provider: it says so.
            conn = ConnectorModel(
                id="conn_dxb_dhpo",
                name="DHPO eClaimLink (Dubai Health Authority)",
                type="UAE Clearinghouse / EHR Gateway",
                status="Configured",
                state="CONFIGURED",
                clinic_id=clinic_id,
                latency_ms=None,
                last_sync=None
            )
            db.add(conn)

    db.commit()
    print("✔ Successfully populated UAE Dubai clinic data into database.")


def auto_seed_dubai_if_empty(db: Session):
    """
    Checks if encounters table is empty, and auto-seeds the Dubai demo data if so.
    """
    from app.core.config import settings
    if getattr(settings, "SOURCE_PROFILE", "") == "workbook" and not getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
        return
    count = db.query(EncounterModel).count()
    if count == 0:
        seed_dubai_data(db)


# Dubai demo recommendations seeded before this fix carry "ORGANIZATION_ADMINISTRATOR",
# which is a role, not an AuthorityRequirement; loading them failed with ValueError.
_INVALID_SEEDED_AUTHORITY = "ORGANIZATION_ADMINISTRATOR"


def _claim_revenue_fields(code, amount, currency) -> dict:
    """Revenue fields the Revenue Intelligence Engine gives a denied claim (rule RULE-UAE-<code>)."""
    from app.services.revenue_intelligence_engine import revenue_fields
    facts = [{"key": "denied_amount", "value": amount}, {"key": "currency", "value": currency}]
    rev = revenue_fields(f"RULE-UAE-{code}", facts)
    return {"revenue_risk_category": rev["revenue_risk_category"], "revenue_confidence": rev["revenue_confidence"],
            "operational_dependency": rev["operational_dependency"]}


def repair_seeded_revenue_fields(db: Session) -> int:
    """Fills risk category, confidence and dependency on claim-denial signals stored without them."""
    from app.services.risk_level_evaluator import CLAIM_DENIAL
    repaired = 0
    for sig in db.query(SignalModel).filter(SignalModel.revenue_risk_category.is_(None)).all():
        meta = sig.metadata_data or {}
        inputs = meta.get("risk_inputs") or {}
        if meta.get("risk_kind") != CLAIM_DENIAL or not inputs.get("denial_code"):
            continue
        for key, value in _claim_revenue_fields(inputs["denial_code"], inputs.get("denied_amount"),
                                                inputs.get("currency") or "AED").items():
            if getattr(sig, key) in (None, ""):
                setattr(sig, key, value)
        repaired += 1
    if repaired:
        db.commit()
    return repaired


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
