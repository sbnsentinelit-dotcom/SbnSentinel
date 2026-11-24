"""
Billing of the synthetic visits (the part of the clinic's billing / claims feed the visit generator plays).

While no billing system is connected, each completed generated visit (VIS-...) is billed the way one of
the clinic's own workbook claims in the same department was billed: that claim's CPT code and billed
amount, its outcome (paid, denied, still open, or held for a denied authorization), its submission lag
(days from the visit to submission) and, for a paid claim, its payment lag (days from submission to the
payer's payment). A denied claim takes the payment lag of a paid workbook claim (the remittance that
carries the denial arrives like a payment). Which workbook claim a visit follows is seeded by the visit
id, so a visit's billing never changes between runs.

The encounter's billing fields are written (CPT, amount, billing status, denial reason) and the status
follows the timeline: Pending until submission, Billed until the payer's decision, then Paid or Claim
Denied. When a claim is paid, the payment the workbook claim received (payer + patient payment) is
posted as a payment receipt dated on the payment day. Every figure on the Revenue Intelligence screen
is calculated from these records.
A visit that ends without a consultation (left before being seen) has no charge.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.encounter import EncounterModel
from app.models.financial_reporting import FinancialPaymentReceiptModel
from app.models.patient_flow import CANCELLED, CHECKED_OUT, VisitEventModel
from app.services import workbook_dataset
from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY, PatientFlowPolicy

BILLING_VERSION = "v1"
NO_CHARGE = "No Charge"
HELD = "Held - Auth Denied"
FINAL_STATUSES = ("Paid", "Claim Denied", HELD, NO_CHARGE)


@dataclass(frozen=True)
class ClaimTemplate:
    """How one workbook claim went: what was billed and when it was submitted and decided."""
    claim_ref: str
    specialty: str
    cpt_code: Optional[str]
    amount: Optional[float]
    outcome: str                      # Paid | Claim Denied | Billed | Held - Auth Denied
    submit_lag_days: Optional[int]
    decision_lag_days: Optional[int]  # paid claims only (payment date - submission date)
    denial_reason: Optional[str]
    paid_amount: Optional[float] = None  # payer payment + patient payment on the workbook claim


@lru_cache(maxsize=1)
def claim_templates() -> Dict[str, List[ClaimTemplate]]:
    """The workbook's claims grouped by department (specialty)."""
    wb = workbook_dataset.load_workbook()
    encounters = {e.get("Encounter_ID"): e for e in wb.get("Encounters", [])}
    claims = {c.get("Encounter_ID"): c for c in wb.get("Claims", [])}
    payments = {p.get("Claim_ID"): p for p in wb.get("Payments", [])}
    paid_on = {cid: workbook_dataset.excel_date(p.get("Payment_Date")) for cid, p in payments.items()}
    out: Dict[str, List[ClaimTemplate]] = {}
    for chg in wb.get("Charges", []):
        enc = encounters.get(chg.get("Encounter_ID")) or {}
        if enc.get("Encounter_Status") == "Cancelled":
            continue
        visit_day = workbook_dataset.excel_date(enc.get("Encounter_Date"))
        clm = claims.get(chg.get("Encounter_ID"))
        submit_lag = decision_lag = paid_amount = None
        denial = None
        if clm:
            outcome = workbook_dataset._CLAIM_BILLING_STATUS.get(clm.get("Claim_Status"), clm.get("Claim_Status"))
            submitted = workbook_dataset.excel_date(clm.get("Submission_Date"))
            if submitted and visit_day:
                submit_lag = max(0, (submitted - visit_day).days)
            paid = paid_on.get(clm.get("Claim_ID"))
            if outcome == "Paid" and paid and submitted:
                decision_lag = max(0, (paid - submitted).days)
                pay = payments.get(clm.get("Claim_ID")) or {}
                paid_amount = round((workbook_dataset.to_float(pay.get("Payer_Payment")) or 0.0)
                                    + (workbook_dataset.to_float(pay.get("Patient_Payment")) or 0.0), 2)
            if clm.get("Denial_Code") and clm.get("Denial_Reason"):
                denial = f"{clm['Denial_Code']}: {clm['Denial_Reason']}"
        else:
            outcome = chg.get("Charge_Status") or "Pending"
        tpl = ClaimTemplate(
            claim_ref=(clm or {}).get("Claim_ID") or chg.get("Charge_ID"), specialty=enc.get("Specialty") or "",
            cpt_code=chg.get("CPT_Code") or None, amount=workbook_dataset.to_float(chg.get("Billed_Amount")),
            outcome=outcome, submit_lag_days=submit_lag, decision_lag_days=decision_lag, denial_reason=denial,
            paid_amount=paid_amount)
        out.setdefault(tpl.specialty, []).append(tpl)
    return out


@lru_cache(maxsize=1)
def _paid_lags() -> List[int]:
    return sorted(t.decision_lag_days for ts in claim_templates().values() for t in ts
                  if t.decision_lag_days is not None)


@dataclass(frozen=True)
class VisitBilling:
    claim_ref: str
    cpt_code: Optional[str]
    amount: Optional[float]
    outcome: str
    submitted_on: Optional[date]
    decided_on: Optional[date]
    denial_reason: Optional[str]
    paid_amount: Optional[float] = None

    def status_on(self, today: date) -> str:
        if self.outcome == HELD:
            return HELD
        if self.submitted_on is None or today < self.submitted_on:
            return "Pending"
        if self.outcome in ("Paid", "Claim Denied") and self.decided_on and today >= self.decided_on:
            return self.outcome
        return "Billed"


def plan_billing(visit_id: str, department: Optional[str], visit_day: date) -> Optional[VisitBilling]:
    """The billing a completed visit follows (seeded by its id); None when the workbook has no claims."""
    templates = claim_templates()
    pool = templates.get(department or "") or [t for ts in templates.values() for t in ts]
    if not pool:
        return None
    rng = random.Random(f"{visit_id}|billing|{BILLING_VERSION}")
    tpl = rng.choice(pool)
    submitted = visit_day + timedelta(days=tpl.submit_lag_days) if tpl.submit_lag_days is not None else None
    decided = None
    if submitted and tpl.outcome in ("Paid", "Claim Denied"):
        lag = tpl.decision_lag_days if tpl.decision_lag_days is not None else (
            rng.choice(_paid_lags()) if _paid_lags() else None)
        decided = submitted + timedelta(days=lag) if lag is not None else None
    return VisitBilling(claim_ref=tpl.claim_ref, cpt_code=tpl.cpt_code, amount=tpl.amount, outcome=tpl.outcome,
                        submitted_on=submitted, decided_on=decided,
                        denial_reason=tpl.denial_reason if tpl.outcome == "Claim Denied" else None,
                        paid_amount=tpl.paid_amount if tpl.outcome == "Paid" else None)


def update_generated_billing(db: Session, clinic_id: str, now: Optional[datetime] = None,
                             policy: PatientFlowPolicy = DEFAULT_FLOW_POLICY) -> int:
    """Brings the billing of the clinic's generated visits up to `now`. Returns how many changed."""
    now = now or datetime.utcnow()
    today = policy.local_date(now)
    open_rows = db.query(EncounterModel).filter(
        EncounterModel.clinic_id == clinic_id, EncounterModel.id.like("VIS-%"),
        ~EncounterModel.billing_status.in_(FINAL_STATUSES) | EncounterModel.billing_status.is_(None)).all()
    if not open_rows:
        return 0
    ended = {}
    for visit_id, event_type in db.query(VisitEventModel.visit_id, VisitEventModel.event_type).filter(
            VisitEventModel.clinic_id == clinic_id,
            VisitEventModel.visit_id.in_([e.id for e in open_rows]),
            VisitEventModel.event_type.in_([CHECKED_OUT, CANCELLED]),
            VisitEventModel.event_time <= now,
            VisitEventModel.superseded_at.is_(None)).all():
        if ended.get(visit_id) != CHECKED_OUT:
            ended[visit_id] = event_type
    changed = 0
    for enc in open_rows:
        end = ended.get(enc.id)
        if end is None:
            continue  # still in the clinic: nothing to bill yet
        if end == CANCELLED:
            fields = {"billing_status": NO_CHARGE, "billing_amount": None, "cpt_code": None}
        else:
            bill = plan_billing(enc.id, enc.department, date.fromisoformat(enc.date[:10]))
            if bill is None:
                continue
            fields = {"cpt_code": bill.cpt_code, "billing_amount": bill.amount,
                      "billing_status": bill.status_on(today), "denial_reason": bill.denial_reason
                      if bill.status_on(today) == "Claim Denied" else None}
        if any(getattr(enc, k) != v for k, v in fields.items()):
            for k, v in fields.items():
                setattr(enc, k, v)
            changed += 1
        if end == CHECKED_OUT and fields["billing_status"] == "Paid" and bill.paid_amount:
            _post_receipt(db, clinic_id, enc, bill, policy)
    return changed


def _post_receipt(db: Session, clinic_id: str, enc: EncounterModel, bill: VisitBilling,
                  policy: PatientFlowPolicy) -> None:
    from app.core.config import get_deployment_currency
    receipt_id = f"RCPT-{enc.id}"
    if db.query(FinancialPaymentReceiptModel.receipt_id).filter(
            FinancialPaymentReceiptModel.receipt_id == receipt_id).first():
        return
    received = datetime.combine(bill.decided_on, datetime.min.time()) + timedelta(hours=12) - policy.offset
    db.add(FinancialPaymentReceiptModel(
        receipt_id=receipt_id, clinic_id=clinic_id,
        receipt_type="patient_selfpay" if enc.payer_network == "Self Pay" else "payer_payment",
        amount=bill.paid_amount, currency=get_deployment_currency(), received_date=received,
        remittance_id=None, claim_id=enc.id, bank_confirmed=True))
