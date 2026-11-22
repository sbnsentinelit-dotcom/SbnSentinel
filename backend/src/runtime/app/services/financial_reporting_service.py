import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.financial_reporting import (
    FinancialClaimModel,
    FinancialClaimLineModel,
    FinancialSubmissionAttemptModel,
    FinancialRemittanceModel,
    FinancialPaymentReceiptModel,
    FinancialAdjustmentModel,
    ReportRunModel,
    ReportSnapshotModel,
)


def neutralize_formula(val: Any) -> str:
    """
    RA09 / Security: Neutralizes spreadsheet formula-injection strings.
    If string starts with '=', '+', '-', or '@', prepends single quote.
    """
    if val is None:
        return ""
    s = str(val)
    if s and s[0] in ("=", "+", "-", "@"):
        return f"'{s}"
    return s


class FinancialReportingService:
    """
    Automated Clinic Financial Reporting Service (Section 6, FR01-FR08).
    Deterministic, read-only reporting extension under the revenue domain.
    Preserves exact currency (AED), source timestamps, and reconciliation rules.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # FR01: Daily and Monthly Owner Summary
    # ─────────────────────────────────────────────────────────────────────────
    def generate_owner_summary(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            # Query all claims for the period
            all_claims = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
            ).all()

            target_currency = currency or (all_claims[0].currency if all_claims else None)
            if not target_currency:
                from app.core.config import get_deployment_currency
                target_currency = get_deployment_currency()

            # Separate totals by currency to prevent mixed-currency aggregation (e.g. AED 640 + USD 100 = 740)
            currency_totals: Dict[str, Dict[str, Any]] = {}
            for c in all_claims:
                c_curr = c.currency or target_currency
                if c_curr not in currency_totals:
                    currency_totals[c_curr] = {
                        "total_charges": 0.0,
                        "submitted_amount": 0.0,
                        "remittance_paid": 0.0,
                        "claims_count": 0,
                    }
                currency_totals[c_curr]["total_charges"] += c.billed_amount or 0.0
                if c.status in ("submitted", "adjudicated", "paid", "denied"):
                    currency_totals[c_curr]["submitted_amount"] += c.billed_amount or 0.0
                if c.paid_amount is not None:
                    currency_totals[c_curr]["remittance_paid"] += c.paid_amount
                currency_totals[c_curr]["claims_count"] += 1

            for curr_key, t_dict in currency_totals.items():
                t_dict["total_charges"] = round(t_dict["total_charges"], 2)
                t_dict["submitted_amount"] = round(t_dict["submitted_amount"], 2)
                t_dict["remittance_paid"] = round(t_dict["remittance_paid"], 2)

            # Filter claims and receipts strictly matching the target currency
            claims = [c for c in all_claims if (c.currency or target_currency) == target_currency]

            total_charges = sum(c.billed_amount or 0.0 for c in claims)
            submitted_claims = [c for c in claims if c.status in ("submitted", "adjudicated", "paid", "denied")]
            submitted_amount = sum(c.billed_amount or 0.0 for c in submitted_claims)
            remittance_paid = sum(c.paid_amount or 0.0 for c in claims if c.paid_amount is not None)
            patient_share = sum(c.patient_share or 0.0 for c in claims if c.patient_share is not None)

            # Reconciled Receipts
            all_receipts = session.query(FinancialPaymentReceiptModel).filter(
                FinancialPaymentReceiptModel.clinic_id == clinic_id,
                FinancialPaymentReceiptModel.received_date >= period_start,
                FinancialPaymentReceiptModel.received_date <= period_end,
            ).all()
            receipts = [r for r in all_receipts if (r.currency or target_currency) == target_currency]

            confirmed_receipts = sum(r.amount for r in receipts if r.bank_confirmed)
            total_cash_receipts = sum(r.amount for r in receipts)

            # Adjustments & refunds
            adjustments = session.query(FinancialAdjustmentModel).filter(
                FinancialAdjustmentModel.claim_id.in_([c.claim_id for c in claims]) if claims else False
            ).all()
            total_adjustments = sum(a.amount for a in adjustments if a.adjustment_type in ("contractual", "charity", "write_off"))
            total_refunds = sum(a.amount for a in adjustments if a.adjustment_type == "refund")

            # Outstanding Balance = Charges - Paid - Adjustments
            outstanding_balance = max(0.0, total_charges - remittance_paid - total_adjustments)

            completeness = "COMPLETE" if claims or receipts else "EMPTY_PERIOD"

            return {
                "report_code": "FR01",
                "report_name": "Daily and Monthly Owner Summary",
                "clinic_id": clinic_id,
                "currency": target_currency,
                "currency_totals": currency_totals,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "as_of_cutoff": datetime.now(timezone.utc).isoformat(),
                "completeness_status": completeness,
                "metrics": {
                    "total_charges": round(total_charges, 2),
                    "submitted_amount": round(submitted_amount, 2),
                    "remittance_reported_payment": round(remittance_paid, 2),
                    "patient_share": round(patient_share, 2),
                    "confirmed_bank_receipts": round(confirmed_receipts, 2),
                    "total_receipts_recorded": round(total_cash_receipts, 2),
                    "total_adjustments": round(total_adjustments, 2),
                    "total_refunds": round(total_refunds, 2),
                    "outstanding_balance": round(outstanding_balance, 2),
                    "total_claims_count": len(claims),
                },
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR02: Payer and Plan Summary
    # ─────────────────────────────────────────────────────────────────────────
    def generate_payer_plan_summary(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            claims_query = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
            )
            if currency:
                claims_query = claims_query.filter(FinancialClaimModel.currency == currency)
            claims = claims_query.all()

            effective_currency = currency or (claims[0].currency if claims else None)
            if not effective_currency:
                from app.core.config import get_deployment_currency
                effective_currency = get_deployment_currency()

            payer_groups: Dict[str, Dict[str, Any]] = {}
            for c in claims:
                claim_curr = c.currency or effective_currency
                key = f"{c.payer_id}:{c.tpa_id or 'NO_TPA'}:{claim_curr}"
                if key not in payer_groups:
                    payer_groups[key] = {
                        "payer_id": c.payer_id,
                        "tpa_id": c.tpa_id or "Direct",
                        "currency": claim_curr,
                        "claim_count": 0,
                        "billed_total": 0.0,
                        "allowed_total": 0.0,
                        "paid_total": 0.0,
                        "denied_count": 0,
                        "paid_count": 0,
                        "partial_count": 0,
                    }
                g = payer_groups[key]
                g["claim_count"] += 1
                g["billed_total"] += c.billed_amount or 0.0
                g["allowed_total"] += c.allowed_amount or 0.0
                g["paid_total"] += c.paid_amount or 0.0

                if c.status == "denied":
                    g["denied_count"] += 1
                elif c.status == "paid":
                    g["paid_count"] += 1
                elif c.status in ("partially_paid", "adjudicated"):
                    g["partial_count"] += 1

            rows = list(payer_groups.values())
            for r in rows:
                r["billed_total"] = round(r["billed_total"], 2)
                r["allowed_total"] = round(r["allowed_total"], 2)
                r["paid_total"] = round(r["paid_total"], 2)

            return {
                "report_code": "FR02",
                "report_name": "Payer and Plan Summary",
                "clinic_id": clinic_id,
                "currency": effective_currency,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "rows": rows,
                "total_payers": len(rows),
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR03: Claim Status and Processing Register
    # ─────────────────────────────────────────────────────────────────────────
    def generate_claim_register(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            claims_query = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
            )
            if currency:
                claims_query = claims_query.filter(FinancialClaimModel.currency == currency)
            claims = claims_query.order_by(FinancialClaimModel.service_date.desc()).all()

            effective_currency = currency or (claims[0].currency if claims else None)
            if not effective_currency:
                from app.core.config import get_deployment_currency
                effective_currency = get_deployment_currency()

            rows = []
            for c in claims:
                attempts = session.query(FinancialSubmissionAttemptModel).filter(
                    FinancialSubmissionAttemptModel.claim_id == c.claim_id
                ).order_by(FinancialSubmissionAttemptModel.attempt_number.asc()).all()

                rows.append({
                    "claim_id": c.claim_id,
                    "patient_id": c.patient_id,
                    "payer_id": c.payer_id,
                    "currency": c.currency or effective_currency,
                    "service_date": c.service_date.isoformat(),
                    "submission_date": c.submission_date.isoformat() if c.submission_date else None,
                    "adjudication_date": c.adjudication_date.isoformat() if c.adjudication_date else None,
                    "status": c.status,
                    "billed_amount": round(c.billed_amount or 0.0, 2),
                    "allowed_amount": round(c.allowed_amount, 2) if c.allowed_amount is not None else None,
                    "paid_amount": round(c.paid_amount, 2) if c.paid_amount is not None else None,
                    "attempt_count": len(attempts),
                    "latest_rejection": attempts[-1].rejection_reason if attempts and attempts[-1].rejection_reason else None,
                })

            return {
                "report_code": "FR03",
                "report_name": "Claim Status and Processing Register",
                "clinic_id": clinic_id,
                "currency": effective_currency,
                "rows": rows,
                "total_claims": len(rows),
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR04: Unpaid and Partially Paid Procedure Lines
    # ─────────────────────────────────────────────────────────────────────────
    def generate_unpaid_procedure_lines(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            q = session.query(
                FinancialClaimLineModel, FinancialClaimModel
            ).join(
                FinancialClaimModel, FinancialClaimLineModel.claim_id == FinancialClaimModel.claim_id
            ).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
                FinancialClaimLineModel.line_status.in_(["submitted", "partially_paid", "denied"]),
            )
            if currency:
                q = q.filter(FinancialClaimModel.currency == currency)
            lines = q.all()

            effective_currency = currency or (lines[0][1].currency if lines else None)
            if not effective_currency:
                from app.core.config import get_deployment_currency
                effective_currency = get_deployment_currency()

            currency_totals: Dict[str, float] = {}
            rows = []
            for line, claim in lines:
                claim_curr = claim.currency or effective_currency
                allowed = line.allowed_amount or 0.0
                paid = line.paid_amount or 0.0
                adjusted = line.adjusted_amount or 0.0
                supported_balance = max(0.0, (line.billed_amount or 0.0) - paid - adjusted)

                rows.append({
                    "line_id": line.line_id,
                    "claim_id": line.claim_id,
                    "procedure_code": line.procedure_code,
                    "code_system": line.code_system,
                    "code_version": line.code_version,
                    "units": line.units,
                    "payer_id": claim.payer_id,
                    "currency": claim_curr,
                    "service_date": claim.service_date.isoformat(),
                    "billed_amount": round(line.billed_amount or 0.0, 2),
                    "allowed_amount": round(allowed, 2) if line.allowed_amount is not None else None,
                    "paid_amount": round(paid, 2) if line.paid_amount is not None else None,
                    "supported_balance": round(supported_balance, 2),
                    "line_status": line.line_status,
                    "denial_code": line.denial_code,
                    "denial_reason": line.denial_reason,
                })
                currency_totals[claim_curr] = round(currency_totals.get(claim_curr, 0.0) + supported_balance, 2)

            return {
                "report_code": "FR04",
                "report_name": "Unpaid and Partially Paid Procedure Lines",
                "clinic_id": clinic_id,
                "currency": effective_currency,
                "currency_totals": currency_totals,
                "rows": rows,
                "unpaid_lines_count": len(rows),
                "total_unpaid_balance": round(currency_totals.get(effective_currency, sum(r["supported_balance"] for r in rows)), 2),
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR05: Rejection and Denial Reasons
    # ─────────────────────────────────────────────────────────────────────────
    def generate_denials_and_rejections(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            # Payer Adjudication Denials (from Claim Lines)
            lines_q = session.query(
                FinancialClaimLineModel, FinancialClaimModel
            ).join(
                FinancialClaimModel, FinancialClaimLineModel.claim_id == FinancialClaimModel.claim_id
            ).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
                FinancialClaimLineModel.line_status == "denied",
            )
            if currency:
                lines_q = lines_q.filter(FinancialClaimModel.currency == currency)
            lines = lines_q.all()

            effective_currency = currency
            if not effective_currency and lines:
                effective_currency = lines[0][1].currency
            if not effective_currency:
                from app.core.config import get_deployment_currency
                effective_currency = get_deployment_currency()

            denial_groups: Dict[str, Dict[str, Any]] = {}
            for line, claim in lines:
                claim_curr = claim.currency or effective_currency
                code = line.denial_code or "UNKNOWN_DENIAL"
                key = f"{claim.payer_id}:{code}:{claim_curr}"
                if key not in denial_groups:
                    denial_groups[key] = {
                        "category": "Payer Adjudication Denial",
                        "payer_id": claim.payer_id,
                        "currency": claim_curr,
                        "code": code,
                        "reason": line.denial_reason or "Reason unavailable from payer",
                        "affected_lines_count": 0,
                        "total_denied_amount": 0.0,
                    }
                denial_groups[key]["affected_lines_count"] += 1
                denial_groups[key]["total_denied_amount"] += line.billed_amount or 0.0

            # Technical Rejections (from Submission Attempts)
            rej_date = func.coalesce(
                FinancialSubmissionAttemptModel.response_date,
                FinancialSubmissionAttemptModel.submission_date,
            )
            attempts_q = session.query(
                FinancialSubmissionAttemptModel, FinancialClaimModel
            ).join(
                FinancialClaimModel, FinancialSubmissionAttemptModel.claim_id == FinancialClaimModel.claim_id
            ).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialSubmissionAttemptModel.outcome == "rejected_technical",
                rej_date >= period_start,
                rej_date <= period_end,
            )
            if currency:
                attempts_q = attempts_q.filter(FinancialClaimModel.currency == currency)
            attempts = attempts_q.all()

            for att, claim in attempts:
                claim_curr = claim.currency or effective_currency
                key = f"{claim.payer_id}:TECH_REJECT:{att.rejection_reason or 'UNKNOWN'}:{claim_curr}"
                if key not in denial_groups:
                    denial_groups[key] = {
                        "category": "Technical Clearinghouse Rejection",
                        "payer_id": claim.payer_id,
                        "currency": claim_curr,
                        "code": "TECH_REJECT",
                        "reason": att.rejection_reason or "Technical format validation failure",
                        "affected_lines_count": 0,
                        "total_denied_amount": 0.0,
                    }
                denial_groups[key]["affected_lines_count"] += 1
                denial_groups[key]["total_denied_amount"] += claim.billed_amount or 0.0

            rows = list(denial_groups.values())
            currency_totals: Dict[str, float] = {}
            for r in rows:
                r["total_denied_amount"] = round(r["total_denied_amount"], 2)
                curr = r["currency"]
                currency_totals[curr] = round(currency_totals.get(curr, 0.0) + r["total_denied_amount"], 2)

            return {
                "report_code": "FR05",
                "report_name": "Rejection and Denial Reasons",
                "clinic_id": clinic_id,
                "currency": effective_currency,
                "currency_totals": currency_totals,
                "rows": rows,
                "total_reasons": len(rows),
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR06: Payer Receipts and Reconciliation
    # ─────────────────────────────────────────────────────────────────────────
    def generate_reconciliation_report(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        RA05 Invariant:
        A remittance of AED 100, clinic payment of AED 100 and bank receipt of AED 100
        for the same settlement shows AED 100 advised and AED 100 received (NOT AED 300).
        """
        session = db or SessionLocal()
        try:
            rem_q = session.query(FinancialRemittanceModel).filter(
                FinancialRemittanceModel.clinic_id == clinic_id,
                FinancialRemittanceModel.remittance_date >= period_start,
                FinancialRemittanceModel.remittance_date <= period_end,
            )
            if currency:
                rem_q = rem_q.filter(FinancialRemittanceModel.currency == currency)
            remittances = rem_q.all()

            rcpt_q = session.query(FinancialPaymentReceiptModel).filter(
                FinancialPaymentReceiptModel.clinic_id == clinic_id,
                FinancialPaymentReceiptModel.received_date >= period_start,
                FinancialPaymentReceiptModel.received_date <= period_end,
            )
            if currency:
                rcpt_q = rcpt_q.filter(FinancialPaymentReceiptModel.currency == currency)
            receipts = rcpt_q.all()

            # Link receipts to remittances by remittance_id
            matched_remittance_ids = set()
            reconciled_rows = []

            for rem in remittances:
                matched_receipts = [r for r in receipts if r.remittance_id == rem.remittance_id]
                receipt_amount = sum(r.amount for r in matched_receipts)
                is_bank_confirmed = any(r.bank_confirmed for r in matched_receipts)

                if matched_receipts:
                    matched_remittance_ids.add(rem.remittance_id)

                rem_currency = rem.currency or "AED"
                receipt_currencies = {r.currency or "AED" for r in matched_receipts}
                receipt_currency = list(receipt_currencies)[0] if len(receipt_currencies) == 1 else ("MIXED" if receipt_currencies else rem_currency)

                currencies_match = bool(matched_receipts and len(receipt_currencies) == 1 and rem_currency in receipt_currencies)
                amount_match = abs(rem.total_paid - receipt_amount) < 0.01

                if not matched_receipts:
                    status = "UNALLOCATED"
                elif not currencies_match:
                    status = "CURRENCY_MISMATCH"
                elif amount_match:
                    status = "MATCHED"
                else:
                    status = "DISCREPANCY"

                reconciled_rows.append({
                    "remittance_id": rem.remittance_id,
                    "payment_reference": rem.payment_reference,
                    "payer_id": rem.payer_id,
                    "remittance_date": rem.remittance_date.isoformat(),
                    "currency": rem_currency,
                    "remittance_currency": rem_currency,
                    "receipt_currency": receipt_currency,
                    "payer_advised_amount": round(rem.total_paid, 2),
                    "received_cash_amount": round(receipt_amount, 2),
                    "reconciliation_status": status,
                    "bank_confirmed": is_bank_confirmed,
                })

            # Unallocated receipts
            unallocated_receipts = [r for r in receipts if not r.remittance_id or r.remittance_id not in matched_remittance_ids]

            rep_currency = currency or (remittances[0].currency if remittances else (receipts[0].currency if receipts else None))
            if not rep_currency:
                from app.core.config import get_deployment_currency
                rep_currency = get_deployment_currency()

            # Separate advised, received, and unallocated amounts by currency
            all_currencies = set(r.currency or "AED" for r in remittances) | set(r.currency or "AED" for r in receipts)
            currency_totals: Dict[str, Dict[str, Any]] = {}
            for curr in all_currencies:
                c_rems = [r for r in remittances if (r.currency or "AED") == curr]
                c_rcpts = [r for r in receipts if (r.currency or "AED") == curr]
                c_unallocated = [r for r in unallocated_receipts if (r.currency or "AED") == curr]
                currency_totals[curr] = {
                    "total_payer_advised": round(sum(r.total_paid for r in c_rems), 2),
                    "total_cash_received": round(sum(r.amount for r in c_rcpts), 2),
                    "unallocated_cash_total": round(sum(r.amount for r in c_unallocated), 2),
                    "unallocated_receipts_count": len(c_unallocated),
                }

            eff_curr_totals = currency_totals.get(rep_currency, {
                "total_payer_advised": 0.0,
                "total_cash_received": 0.0,
                "unallocated_cash_total": 0.0,
                "unallocated_receipts_count": 0,
            })

            return {
                "report_code": "FR06",
                "report_name": "Payer Receipts and Reconciliation",
                "clinic_id": clinic_id,
                "currency": rep_currency,
                "currency_totals": currency_totals,
                "reconciled_items": reconciled_rows,
                "unallocated_receipts_count": eff_curr_totals["unallocated_receipts_count"],
                "unallocated_cash_total": eff_curr_totals["unallocated_cash_total"],
                "total_payer_advised": eff_curr_totals["total_payer_advised"],
                "total_cash_received": eff_curr_totals["total_cash_received"],
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR07: Outstanding Items and Ageing
    # ─────────────────────────────────────────────────────────────────────────
    def generate_ageing_report(
        self,
        clinic_id: str,
        currency: Optional[str] = None,
        as_of_cutoff: Optional[datetime] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            cutoff = as_of_cutoff or datetime.now(timezone.utc)
            claims_query = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.status.in_(["submitted", "adjudicated", "partially_paid"]),
            )
            if currency:
                claims_query = claims_query.filter(FinancialClaimModel.currency == currency)
            claims = claims_query.all()

            effective_currency = currency or (claims[0].currency if claims else None)
            if not effective_currency:
                from app.core.config import get_deployment_currency
                effective_currency = get_deployment_currency()

            currency_buckets: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for c in claims:
                c_curr = c.currency or effective_currency
                if c_curr not in currency_buckets:
                    currency_buckets[c_curr] = {
                        "0_30_days": {"count": 0, "amount": 0.0},
                        "31_60_days": {"count": 0, "amount": 0.0},
                        "61_90_days": {"count": 0, "amount": 0.0},
                        "91_120_days": {"count": 0, "amount": 0.0},
                        "120_plus_days": {"count": 0, "amount": 0.0},
                    }
                claim_date = c.service_date.replace(tzinfo=timezone.utc) if c.service_date.tzinfo is None else c.service_date
                age_days = (cutoff - claim_date).days
                unpaid = max(0.0, (c.billed_amount or 0.0) - (c.paid_amount or 0.0))

                if age_days <= 30:
                    b = "0_30_days"
                elif age_days <= 60:
                    b = "31_60_days"
                elif age_days <= 90:
                    b = "61_90_days"
                elif age_days <= 120:
                    b = "91_120_days"
                else:
                    b = "120_plus_days"

                currency_buckets[c_curr][b]["count"] += 1
                currency_buckets[c_curr][b]["amount"] += unpaid

            if effective_currency not in currency_buckets:
                currency_buckets[effective_currency] = {
                    "0_30_days": {"count": 0, "amount": 0.0},
                    "31_60_days": {"count": 0, "amount": 0.0},
                    "61_90_days": {"count": 0, "amount": 0.0},
                    "91_120_days": {"count": 0, "amount": 0.0},
                    "120_plus_days": {"count": 0, "amount": 0.0},
                }

            primary_buckets = currency_buckets[effective_currency]
            currency_totals: Dict[str, float] = {}
            for curr_k, b_dict in currency_buckets.items():
                curr_tot = 0.0
                for b in b_dict.values():
                    b["amount"] = round(b["amount"], 2)
                    curr_tot += b["amount"]
                currency_totals[curr_k] = round(curr_tot, 2)

            return {
                "report_code": "FR07",
                "report_name": "Outstanding Items and Ageing",
                "clinic_id": clinic_id,
                "currency": effective_currency,
                "currency_totals": currency_totals,
                "currency_buckets": currency_buckets,
                "as_of_cutoff": cutoff.isoformat(),
                "ageing_basis": "service_date",
                "buckets": primary_buckets,
                "total_outstanding": currency_totals.get(effective_currency, 0.0),
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # FR08: Completeness and Exceptions
    # ─────────────────────────────────────────────────────────────────────────
    def generate_completeness_exceptions(
        self,
        clinic_id: str,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            # Check unmapped statuses or missing remittances
            unmapped_claims = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.status == "unknown",
            ).count()

            claims_missing_remittance = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.status == "submitted",
                FinancialClaimModel.submission_date < datetime.now(timezone.utc),
            ).count()

            unallocated_receipts = session.query(FinancialPaymentReceiptModel).filter(
                FinancialPaymentReceiptModel.clinic_id == clinic_id,
                FinancialPaymentReceiptModel.remittance_id.is_(None),
            ).count()

            exceptions = []
            if unmapped_claims > 0:
                exceptions.append({
                    "exception_type": "UNMAPPED_CLAIM_STATUS",
                    "severity": "WARNING",
                    "count": unmapped_claims,
                    "description": "Claims in unknown processing status",
                })
            if claims_missing_remittance > 0:
                exceptions.append({
                    "exception_type": "PENDING_REMITTANCE",
                    "severity": "INFO",
                    "count": claims_missing_remittance,
                    "description": "Submitted claims awaiting remittance advice",
                })
            if unallocated_receipts > 0:
                exceptions.append({
                    "exception_type": "UNALLOCATED_RECEIPT",
                    "severity": "INFO",
                    "count": unallocated_receipts,
                    "description": "Receipts recorded without matching remittance link",
                })

            return {
                "report_code": "FR08",
                "report_name": "Completeness and Exceptions Report",
                "clinic_id": clinic_id,
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "exceptions": exceptions,
                "is_complete": len(exceptions) == 0,
            }
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Durable Report Snapshot & Run Management (MS-010 / RA08 / RA09)
    # ─────────────────────────────────────────────────────────────────────────
    def create_snapshot(
        self,
        clinic_id: str,
        report_code: str,
        period_start: datetime,
        period_end: datetime,
        db: Optional[Session] = None,
    ) -> ReportSnapshotModel:
        session = db or SessionLocal()
        try:
            # Compute report payload
            if report_code == "FR01":
                payload = self.generate_owner_summary(clinic_id, period_start, period_end, db=session)
            elif report_code == "FR02":
                payload = self.generate_payer_plan_summary(clinic_id, period_start, period_end, db=session)
            elif report_code == "FR03":
                payload = self.generate_claim_register(clinic_id, period_start, period_end, db=session)
            elif report_code == "FR04":
                payload = self.generate_unpaid_procedure_lines(clinic_id, period_start, period_end, db=session)
            elif report_code == "FR05":
                payload = self.generate_denials_and_rejections(clinic_id, period_start, period_end, db=session)
            elif report_code == "FR06":
                payload = self.generate_reconciliation_report(clinic_id, period_start, period_end, db=session)
            elif report_code == "FR07":
                payload = self.generate_ageing_report(clinic_id, as_of_cutoff=period_end, db=session)
            elif report_code == "FR08":
                payload = self.generate_completeness_exceptions(clinic_id, db=session)
            else:
                raise ValueError(f"Unknown financial report code: {report_code}")

            run_key = f"{clinic_id}_{report_code}_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}_v1"

            # Check existing run or revision
            existing_run = session.query(ReportRunModel).filter(ReportRunModel.run_key == run_key).first()
            if not existing_run:
                run = ReportRunModel(
                    run_key=run_key,
                    clinic_id=clinic_id,
                    definition_id=report_code,
                    definition_version="1.0",
                    period_start=period_start,
                    period_end=period_end,
                    cutoff_time=datetime.now(timezone.utc),
                    status="complete",
                )
                session.add(run)
                session.flush()

            # Find existing snapshots to increment revision (RA08)
            prior_snapshots = session.query(ReportSnapshotModel).filter(
                ReportSnapshotModel.run_key == run_key
            ).order_by(ReportSnapshotModel.revision.desc()).all()

            revision = (prior_snapshots[0].revision + 1) if prior_snapshots else 1
            snapshot_id = f"snp_{uuid.uuid4().hex[:12]}"

            snapshot = ReportSnapshotModel(
                snapshot_id=snapshot_id,
                run_key=run_key,
                clinic_id=clinic_id,
                report_code=report_code,
                payload_json=json.dumps(payload),
                summary_metrics_json=json.dumps(payload.get("metrics") or {}),
                completeness_status=payload.get("completeness_status", "COMPLETE"),
                revision=revision,
            )
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            session.expunge(snapshot)
            return snapshot
        finally:
            if not db:
                session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Export Generator: CSV, XLSX, PDF representation with Formula Neutralization
    # ─────────────────────────────────────────────────────────────────────────
    def export_csv(self, snapshot_id: str, db: Optional[Session] = None) -> str:
        session = db or SessionLocal()
        try:
            snapshot = session.query(ReportSnapshotModel).filter(
                ReportSnapshotModel.snapshot_id == snapshot_id
            ).first()
            if not snapshot:
                raise ValueError(f"Snapshot '{snapshot_id}' not found")

            payload = json.loads(snapshot.payload_json)
            output = io.StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

            # Metadata Header
            writer.writerow(["Report Code", neutralize_formula(payload.get("report_code"))])
            writer.writerow(["Report Name", neutralize_formula(payload.get("report_name"))])
            writer.writerow(["Clinic ID", neutralize_formula(payload.get("clinic_id"))])
            writer.writerow(["Revision", snapshot.revision])
            writer.writerow([])

            # Metrics or Rows
            if "metrics" in payload:
                writer.writerow(["Metric", "Value"])
                for k, v in payload["metrics"].items():
                    writer.writerow([neutralize_formula(k), neutralize_formula(v)])
            elif "rows" in payload and payload["rows"]:
                headers = list(payload["rows"][0].keys())
                writer.writerow([neutralize_formula(h) for h in headers])
                for r in payload["rows"]:
                    writer.writerow([neutralize_formula(r.get(h)) for h in headers])

            return output.getvalue()
        finally:
            if not db:
                session.close()


financial_reporting_service = FinancialReportingService()
