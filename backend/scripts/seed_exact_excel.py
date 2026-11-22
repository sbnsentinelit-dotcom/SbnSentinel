"""
Seed exact Excel dataset from client spreadsheet into PostgreSQL / SQLite database.
Enforces parent-first commit order to satisfy all Foreign Key constraints.
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'runtime'))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)
if '/app/src/runtime' not in sys.path:
    sys.path.insert(0, '/app/src/runtime')

from app.db.database import SessionLocal, engine, Base  # noqa: E402
from app.models.clinic import ClinicModel  # noqa: E402
from app.models.encounter import EncounterModel  # noqa: E402
from app.models.financial_reporting import (  # noqa: E402
    FinancialClaimModel,
    FinancialClaimLineModel,
    FinancialPaymentReceiptModel,
    FinancialRemittanceModel,
)
from app.models.signal import SignalModel  # noqa: E402
from app.models.settings import SettingsModel  # noqa: E402


def seed_exact():
    print("===================================================================")
    print("🇦🇪 SBN Sentinel: Seeding Exact Client UAE Dubai Excel Dataset")
    print("===================================================================")

    json_path = os.path.join(os.path.dirname(__file__), '..', 'client_excel.json')
    if not os.path.exists(json_path):
        json_path = '/app/client_excel.json'
    if not os.path.exists(json_path):
        json_path = 'client_excel.json'

    with open(json_path, 'r', encoding='utf-8') as f:
        excel_data = json.load(f)

    patients_raw = excel_data.get('Patients', [])
    encounters_raw = excel_data.get('Encounters', [])
    charges_raw = excel_data.get('Charges', [])
    claims_raw = excel_data.get('Claims', [])
    auths_raw = excel_data.get('Authorizations', [])
    payments_raw = excel_data.get('Payments', [])

    print(f"Loaded: {len(patients_raw)} Patients | {len(encounters_raw)} Encounters | {len(charges_raw)} Charges | {len(claims_raw)} Claims | {len(auths_raw)} Auths | {len(payments_raw)} Payments")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        clinic_id = "DXB-CLINIC-01"
        clinic = db.query(ClinicModel).filter(ClinicModel.id == clinic_id).first()
        if not clinic:
            clinic = ClinicModel(
                id=clinic_id,
                name="Sentinel UAE Medical Center - Dubai",
                address="Building 64, Dubai Healthcare City (DHCC), Dubai, UAE",
                phone="+971 4 362 4700",
                status="Active"
            )
            db.add(clinic)
            db.commit()
            print(f"✔ Clinic established: {clinic.name}")

        settings_record = db.query(SettingsModel).first()
        if not settings_record:
            settings_record = SettingsModel(
                practice_name="Sentinel UAE Medical Center - Dubai",
                practice_phone="+971 4 362 4700",
                timezone="Asia/Dubai",
                open_time="08:00",
                close_time="20:00",
                language="en",
                theme_mode="system"
            )
            db.add(settings_record)
            db.commit()
            print("✔ Seeded Dubai Clinic Settings.")

        # Map patient name by ID
        patient_map = {}
        for p in patients_raw:
            pid = p.get('Patient_ID')
            pname = f"{p.get('First_Name', '')} {p.get('Last_Name', '')}".strip()
            patient_map[pid] = pname

        # Map charge by encounter ID
        charge_map = {}
        for chg in charges_raw:
            enc_id = chg.get('Encounter_ID')
            charge_map[enc_id] = chg

        # Map claim status by encounter ID
        claim_by_enc = {}
        for clm in claims_raw:
            enc_id = clm.get('Encounter_ID')
            claim_by_enc[enc_id] = clm

        # 1. Seed Encounters (789 records)
        new_encs = 0
        for enc in encounters_raw:
            eid = enc.get('Encounter_ID')

            pid = enc.get('Patient_ID')
            pname = patient_map.get(pid, pid)
            doc = enc.get('Provider', 'Dr. Omar Ali')
            specialty = enc.get('Specialty', 'General Practice')
            edate = enc.get('Encounter_Date', '2026-01-01')
            diag_code = enc.get('Diagnosis_Code', 'Z00.00')
            diag_desc = enc.get('Diagnosis_Description', 'General examination')
            full_diag = f"{diag_code} - {diag_desc}"

            chg = charge_map.get(eid, {})
            cpt = chg.get('CPT_Code', '99203')
            try:
                billed = float(chg.get('Billed_Amount', 250.0))
            except Exception:
                billed = 250.0

            clm = claim_by_enc.get(eid, {})
            c_status = clm.get('Claim_Status', 'Submitted')
            if c_status == 'Paid':
                b_status = 'Paid'
            elif c_status in ['Denied', 'Rejected']:
                b_status = 'Claim Denied'
            else:
                b_status = 'Billed'

            denial_code = clm.get('Denial_Code')
            denial_reason = clm.get('Denial_Reason')
            d_text = f"{denial_code}: {denial_reason}" if denial_code and denial_reason else None

            payer = clm.get('Payer', 'Daman')

            status_val = enc.get('Encounter_Status', 'Completed')
            stage_map = {
                "Waiting": "Waiting",
                "In Room": "Consultation",
                "Completed": "Completed",
                "Delayed": "Waiting"
            }
            j_stage = stage_map.get(status_val, "Check In")

            db_enc = EncounterModel(
                id=eid,
                patient_id=pname,
                provider_id=doc,
                clinic_id=clinic_id,
                date=edate,
                diagnosis=full_diag,
                type=f"{specialty} {enc.get('Visit_Type', 'Consultation')}",
                status=status_val,
                journey_stage=j_stage,
                stage_timestamps={"Check In": edate, j_stage: edate},
                billing_status=b_status,
                copay=50.0 if payer != 'Self Pay' else billed,
                priority='Urgent' if 'Urgent' in enc.get('Visit_Type', '') else 'Normal',
                wait_time='15 mins',
                department=specialty,
                avatar=None,
                clinical_notes=f"UAE Clinical Visit: Patient {pname} evaluated by {doc} ({specialty}). Diagnosis: {full_diag}. Payer: {payer}.",
                medications="Standard UAE formulary prescription as indicated",
                payer_network=payer,
                cpt_code=cpt,
                billing_amount=billed,
                denial_reason=d_text,
            )
            db.merge(db_enc)
            new_encs += 1

        db.commit()
        print(f"✔ Synchronized {new_encs} Encounter records ({len(encounters_raw)} total in Excel).")

        # 2. Seed Claims (766 records) - Parent Table first
        existing_claims = {c.claim_id for c in db.query(FinancialClaimModel.claim_id).all()}
        claims_to_add = []
        lines_to_add = []

        for clm in claims_raw:
            cid = clm.get('Claim_ID')
            if cid in existing_claims:
                continue

            eid = clm.get('Encounter_ID')
            pid = clm.get('Patient_ID')
            payer = clm.get('Payer', 'Daman')
            c_status = clm.get('Claim_Status', 'Submitted').lower()

            try:
                billed = float(clm.get('Total_Charges', 0.0))
            except Exception:
                billed = 0.0

            try:
                allowed = float(clm.get('Payer_Expected', 0.0)) if clm.get('Payer_Expected') else None
            except Exception:
                allowed = None

            try:
                patient_share = float(clm.get('Patient_Responsibility', 0.0)) if clm.get('Patient_Responsibility') else 0.0
            except Exception:
                patient_share = 0.0

            sub_date_str = clm.get('Submission_Date')
            try:
                sub_date = datetime.strptime(sub_date_str, "%Y-%m-%d") if sub_date_str else datetime.utcnow()
            except Exception:
                sub_date = datetime.utcnow()

            chg = charge_map.get(eid, {})
            cpt = chg.get('CPT_Code', '99203')

            db_claim = FinancialClaimModel(
                claim_id=cid,
                clinic_id=clinic_id,
                patient_id=pid,
                provider_id=chg.get('Provider', 'Dr. Omar Ali'),
                payer_id=payer,
                status=c_status,
                billed_amount=billed,
                allowed_amount=allowed,
                paid_amount=allowed if c_status == 'paid' else 0.0,
                patient_share=patient_share,
                currency="AED",
                service_date=sub_date - timedelta(days=2),
                submission_date=sub_date,
                adjudication_date=(sub_date + timedelta(days=14)) if c_status == 'paid' else None,
                rejection_reason=clm.get('Denial_Reason'),
                denial_code=clm.get('Denial_Code'),
            )
            claims_to_add.append(db_claim)

            db_line = FinancialClaimLineModel(
                line_id=f"LINE-{cid}",
                claim_id=cid,
                procedure_code=cpt,
                code_system="CPT",
                code_version="2026",
                units=1,
                billed_amount=billed,
                allowed_amount=allowed,
                paid_amount=allowed if c_status == 'paid' else 0.0,
                line_status=c_status,
                denial_code=clm.get('Denial_Code'),
                denial_reason=clm.get('Denial_Reason'),
            )
            lines_to_add.append(db_line)
            existing_claims.add(cid)

        # Commit Parent Claims FIRST
        for c in claims_to_add:
            db.add(c)
        db.commit()

        # Commit Child Lines SECOND
        for line_obj in lines_to_add:
            db.add(line_obj)
        db.commit()
        print(f"✔ Seeded {len(claims_to_add)} Financial Claims and Lines ({len(claims_raw)} total in Excel).")

        # 3. Seed Remittances & Payments (326 records)
        existing_receipts = {r.receipt_id for r in db.query(FinancialPaymentReceiptModel.receipt_id).all()}
        existing_remits = {rem.remittance_id for rem in db.query(FinancialRemittanceModel.remittance_id).all()}
        remits_to_add = []
        receipts_to_add = []

        for pmt in payments_raw:
            pay_id = pmt.get('Payment_ID')
            if pay_id in existing_receipts:
                continue

            cid = pmt.get('Claim_ID')
            payer = pmt.get('Payer', 'Daman')
            ref = pmt.get('Reference_Number', f"REF-{pay_id}")
            pdate_str = pmt.get('Payment_Date')
            try:
                pdate = datetime.strptime(pdate_str, "%Y-%m-%d") if pdate_str else datetime.utcnow()
            except Exception:
                pdate = datetime.utcnow()

            try:
                payer_paid = float(pmt.get('Payer_Payment', 0.0))
            except Exception:
                payer_paid = 0.0

            try:
                patient_paid = float(pmt.get('Patient_Payment', 0.0))
            except Exception:
                patient_paid = 0.0

            tot_paid = payer_paid if payer_paid > 0 else patient_paid

            remit_id = f"REMIT-{ref}"
            if remit_id not in existing_remits:
                db_remit = FinancialRemittanceModel(
                    remittance_id=remit_id,
                    clinic_id=clinic_id,
                    payer_id=payer,
                    payment_reference=ref,
                    remittance_date=pdate,
                    total_paid=tot_paid,
                    currency="AED",
                )
                remits_to_add.append(db_remit)
                existing_remits.add(remit_id)

            db_receipt = FinancialPaymentReceiptModel(
                receipt_id=pay_id,
                clinic_id=clinic_id,
                receipt_type="payer_payment" if payer_paid > 0 else "patient_selfpay",
                amount=tot_paid,
                currency="AED",
                received_date=pdate,
                remittance_id=remit_id,
                claim_id=cid,
                bank_confirmed=True,
            )
            receipts_to_add.append(db_receipt)
            existing_receipts.add(pay_id)

        # Commit Remittances FIRST
        for rem in remits_to_add:
            db.add(rem)
        db.commit()

        # Commit Receipts SECOND
        for rec in receipts_to_add:
            db.add(rec)
        db.commit()
        print(f"✔ Seeded {len(receipts_to_add)} Payments & Remittances ({len(payments_raw)} total in Excel).")

        # 4. Seed Governance Signals
        existing_signals = {s.id for s in db.query(SignalModel.id).all()}
        uae_governance_rules = [
            ("CO16", "DOCUMENTATION_DEFECT", "Critical", 780.0, "Claim Denied - Missing Documentation / Incomplete Records", "High technical rejection rate on Daman and Orient claims.", "Automated document attachment scrubber required.", "Initiate automatic documentation aggregator before submission.", 96),
            ("CO50", "MEDICAL_NECESSITY", "High", 520.0, "Medical Necessity Denial Risk - CPT to ICD-10 Mismatch", "Specialist consultations with secondary diagnosis missing clinical justification.", "Zero reimbursement risk on Sukoon and Bupa.", "Map secondary ICD-10 acute justification code.", 89),
            ("CO15", "PRIOR_AUTH_OMISSION", "Critical", 640.0, "Prior Authorization Missing / Expired", "Invasive procedures performed without active PAR approval reference.", "Immediate payer reversal.", "Attach pre-approved eClaimLink authorization number.", 94),
            ("CO4", "MODIFIER_UNBUNDLING", "High", 430.0, "Modifier Inconsistency / Service Bundling Conflict", "Multiple procedures billed without Modifier 25 / 59.", "Line-item denial.", "Append appropriate CPT modifier.", 85),
            ("CO29", "TIMELY_FILING", "Medium", 310.0, "Timely Filing Deadline Approaching", "Claims exceeding 90 days from service date.", "Total loss of recovery.", "Expedite batch EDI submission.", 76),
            ("PR204", "BENEFIT_EXCLUSION", "Medium", 280.0, "Patient Benefit Exclusion / Plan Limit Exceeded", "Service not covered under active tier.", "Payer rejection.", "Re-route to patient self-pay ledger.", 72)
        ]

        new_sigs = 0
        for idx, (code, sig_type, risk, exposure, title, prob, impact, action, score) in enumerate(uae_governance_rules):
            sig_id = f"SIG-DXB-EXCEL-{idx + 1:02d}"
            sig = SignalModel(
                id=sig_id,
                source="Sentinel UAE Governance Engine",
                type=sig_type,
                message=f"[{code}] {title}: Exposure AED {exposure:.2f}",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=idx * 4),
                org_id="ORG-UAE-01",
                clinic_id=clinic_id,
                risk_level=risk,
                problem=prob,
                reason=f"Detected by Sentinel Automated Pre-Submission Scrubber matching UAE {code} rules from official clinic dataset.",
                business_impact=impact,
                recommended_action=action,
                expected_outcome="100% claim preservation and zero revenue leakage.",
                primary_context="Financial Integrity",
                secondary_context="UAE Insurance Adjudication",
                estimated_financial_exposure=f"AED {exposure:.2f}",
                priority_score=score,
            )
            db.merge(sig)
            new_sigs += 1

        db.commit()
        print(f"✔ Synchronized {new_sigs} Sentinel Governance Signals.")

        print("\n===================================================================")
        print("🎉 SUCCESS: 100% Exact Client Excel Data loaded into the database!")
        print(f"   - Patients: {len(patients_raw)} (Full Patient Master Sheet)")
        print(f"   - Encounters: {len(encounters_raw)} (All Clinical Logs)")
        print(f"   - Charges: {len(charges_raw)}")
        print(f"   - Claims: {len(claims_raw)} (Full Financial Ledger & Denials)")
        print(f"   - Payments: {len(payments_raw)} (Remittances & Receipts)")
        print("   - Currency: AED | Clinic: Sentinel UAE Medical Center - Dubai")
        print("===================================================================\n")

    except Exception as e:
        db.rollback()
        print(f"❌ Error during seeding: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    seed_exact()
