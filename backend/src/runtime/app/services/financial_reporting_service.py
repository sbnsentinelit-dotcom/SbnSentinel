import csv
import io
import json
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, Optional
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
    def _hospital_predicate(self, scope, period_start, period_end, currency):
        from app.services.hospital_scope_service import scope_predicate
        line, claim = FinancialClaimLineModel, FinancialClaimModel
        return (scope_predicate(scope, line.clinic_id, line.department_id)
            & (claim.claim_id == line.claim_id) & (claim.clinic_id == scope.clinic_id)
            & (claim.org_id == scope.org_id) & (claim.currency == currency)
            & (claim.service_date >= period_start.astimezone(timezone.utc).replace(tzinfo=None))
            & (claim.service_date < period_end.astimezone(timezone.utc).replace(tzinfo=None))
            & line.tombstone.is_(False) & claim.tombstone.is_(False))

    def _hospital_rows(self, db, scope, period_start, period_end, currency):
        from sqlalchemy import select
        line, claim = FinancialClaimLineModel, FinancialClaimModel
        query = select(line).join(claim, claim.claim_id == line.claim_id).where(
            self._hospital_predicate(scope, period_start, period_end, currency)).order_by(line.line_id)
        for row in db.scalars(query).yield_per(100):
            yield {"line_id": row.line_id, "department_id": row.department_id,
                "source_evidence_id": row.source_evidence_id,
                "billed_amount": format(row.billed_amount, "f") if row.billed_amount is not None else None,
                "paid_amount": format(row.paid_amount, "f") if row.paid_amount is not None else None,
                "status": row.line_status}

    def generate_hospital_summary(self, db, scope, period_start, period_end, currency, *, limit=100):
        """Exact, scoped HSP01 line summary. No inferred balance or cash allocation.

        Kept under the existing calculation owner. A bounded detail page is
        returned separately from aggregate counts; restricted views never use
        whole-claim amounts or whole-claim metadata.
        """
        from decimal import Decimal
        from sqlalchemy import select, func
        from app.services.hospital_scope_service import scope_predicate, require_capability
        require_capability(scope, "report.read")
        if (period_start.tzinfo is None or period_end.tzinfo is None or period_end <= period_start
                or not 1 <= limit <= 500 or len(currency) != 3 or currency != currency.upper()):
            raise ValueError("Explicit valid period, currency and bounds required")
        line = FinancialClaimLineModel
        claim = FinancialClaimModel
        predicate = self._hospital_predicate(scope, period_start, period_end, currency)
        aggregate = db.execute(select(func.count(line.line_id), func.count(func.distinct(line.claim_id)),
            func.sum(line.billed_amount), func.sum(line.paid_amount), func.count(line.paid_amount))
            .select_from(line).join(claim, claim.claim_id == line.claim_id).where(predicate)).one()
        rows = db.execute(select(line).join(claim, claim.claim_id == line.claim_id)
            .where(predicate).order_by(line.line_id).limit(limit + 1)).scalars().all()
        total, distinct, billed, paid, known = aggregate
        def amount(value):
            return format(value, "f") if value is not None else None
        return {"report_code": "HSP01_LINES_V1", "clinic_id": scope.clinic_id,
            "scope": scope.model_dump(mode="json"), "currency": currency,
            "business_timezone": "Asia/Dubai", "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(), "restricted": not scope.full_record,
            "completeness_status": "PARTIAL", "coverage_exceptions": ["Source feed completeness not established"],
            "metrics": {"line_count": total, "distinct_claim_count": distinct,
                "known_billed_amount": amount(billed), "known_paid_amount": amount(paid),
                "unknown_payment_lines": total - known, "facility_total": None,
                "receivable": None, "confirmed_cash": None},
            "rows": [{"line_id": row.line_id, "department_id": row.department_id,
                "source_evidence_id": row.source_evidence_id,
                "billed_amount": amount(row.billed_amount), "paid_amount": amount(row.paid_amount),
                "status": row.line_status} for row in rows[:limit]],
            "has_more": len(rows) > limit}

    def freeze_hospital_summary(self, db, scope, period_start, period_end, currency):
        """A bounded immutable snapshot; same inputs/manifest reuse its identity."""
        import hashlib
        from sqlalchemy import select
        from app.models.organization import OrganizationClinicModel
        from app.models.hospital_processing import HospitalSnapshotPage
        db.scalars(select(OrganizationClinicModel).where(
            OrganizationClinicModel.id == scope.clinic_id).with_for_update()).one()
        payload = self.generate_hospital_summary(db, scope, period_start, period_end, currency, limit=100)
        digest = hashlib.sha256()
        for item in self._hospital_rows(db, scope, period_start, period_end, currency):
            if item["source_evidence_id"] is None:
                raise ValueError("Snapshot contains unproven source lineage")
            digest.update((json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode())
        payload["input_manifest_hash"] = digest.hexdigest()
        payload["definition_version"] = "1"
        payload["business_as_of"] = period_end.isoformat()
        stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        run_key = "hospital:" + hashlib.sha256(stable.encode()).hexdigest()
        old = db.query(ReportSnapshotModel).filter(ReportSnapshotModel.run_key == run_key).first()
        if old:
            return old
        payload["snapshot_id"] = run_key
        payload["input_manifest_ref"] = "hospital_snapshot_pages:" + run_key
        payload["knowledge_cutoff"] = datetime.now(timezone.utc).isoformat()
        db.add(ReportRunModel(run_key=run_key, clinic_id=scope.clinic_id,
            definition_id="HSP01_LINES_V1", definition_version="1",
            period_start=period_start.astimezone(timezone.utc).replace(tzinfo=None),
            period_end=period_end.astimezone(timezone.utc).replace(tzinfo=None),
            cutoff_time=datetime.now(timezone.utc).replace(tzinfo=None), status="partial"))
        db.flush()
        row = ReportSnapshotModel(snapshot_id=run_key, run_key=run_key, clinic_id=scope.clinic_id,
            report_code="HSP01_LINES_V1", payload_json=json.dumps(payload, sort_keys=True),
            summary_metrics_json=json.dumps(payload["metrics"]), completeness_status="PARTIAL", revision=1)
        db.add(row)
        db.flush()
        page_rows, page_number = [], 0
        for item in self._hospital_rows(db, scope, period_start, period_end, currency):
            page_rows.append(item)
            if len(page_rows) == 100:
                page = HospitalSnapshotPage(snapshot_id=run_key, page_number=page_number, rows_json=json.dumps(page_rows))
                db.add(page); db.flush(); db.expunge(page)
                page_rows, page_number = [], page_number + 1
        if page_rows:
            db.add(HospitalSnapshotPage(snapshot_id=run_key, page_number=page_number, rows_json=json.dumps(page_rows)))
            db.flush()
        return row

    def generate_owner_summary(
        self,
        clinic_id: str,
        period_start: datetime,
        period_end: datetime,
        currency: str = "AED",
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            claims = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
            ).all()

            total_charges = sum(c.billed_amount or Decimal("0") for c in claims)
            submitted_claims = [c for c in claims if c.status in ("submitted", "adjudicated", "paid", "denied")]
            submitted_amount = sum(c.billed_amount or Decimal("0") for c in submitted_claims)
            remittance_paid = sum(c.paid_amount or Decimal("0") for c in claims if c.paid_amount is not None)
            patient_share = sum(c.patient_share or Decimal("0") for c in claims if c.patient_share is not None)

            # Reconciled Receipts
            receipts = session.query(FinancialPaymentReceiptModel).filter(
                FinancialPaymentReceiptModel.clinic_id == clinic_id,
                FinancialPaymentReceiptModel.received_date >= period_start,
                FinancialPaymentReceiptModel.received_date <= period_end,
            ).all()

            confirmed_receipts = sum(r.amount for r in receipts if r.bank_confirmed)
            total_cash_receipts = sum(r.amount for r in receipts)

            # Adjustments & refunds
            adjustments = session.query(FinancialAdjustmentModel).filter(
                FinancialAdjustmentModel.claim_id.in_([c.claim_id for c in claims]) if claims else False
            ).all()
            total_adjustments = sum(a.amount for a in adjustments if a.adjustment_type in ("contractual", "charity", "write_off"))
            total_refunds = sum(a.amount for a in adjustments if a.adjustment_type == "refund")

            # Outstanding Balance = Charges - Paid - Adjustments
            outstanding_balance = max(Decimal("0"), total_charges - remittance_paid - total_adjustments)

            completeness = "COMPLETE" if claims or receipts else "EMPTY_PERIOD"

            return {
                "report_code": "FR01",
                "report_name": "Daily and Monthly Owner Summary",
                "clinic_id": clinic_id,
                "currency": currency,
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
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            claims = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
            ).all()

            payer_groups: Dict[str, Dict[str, Any]] = {}
            for c in claims:
                key = f"{c.payer_id}:{c.tpa_id or 'NO_TPA'}"
                if key not in payer_groups:
                    payer_groups[key] = {
                        "payer_id": c.payer_id,
                        "tpa_id": c.tpa_id or "Direct",
                        "claim_count": 0,
                        "billed_total": Decimal("0"),
                        "allowed_total": Decimal("0"),
                        "paid_total": Decimal("0"),
                        "denied_count": 0,
                        "paid_count": 0,
                        "partial_count": 0,
                    }
                g = payer_groups[key]
                g["claim_count"] += 1
                g["billed_total"] += c.billed_amount or Decimal("0")
                g["allowed_total"] += c.allowed_amount or Decimal("0")
                g["paid_total"] += c.paid_amount or Decimal("0")

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
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            claims = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
            ).order_by(FinancialClaimModel.service_date.desc()).all()

            rows = []
            for c in claims:
                attempts = session.query(FinancialSubmissionAttemptModel).filter(
                    FinancialSubmissionAttemptModel.claim_id == c.claim_id
                ).order_by(FinancialSubmissionAttemptModel.attempt_number.asc()).all()

                rows.append({
                    "claim_id": c.claim_id,
                    "patient_id": c.patient_id,
                    "payer_id": c.payer_id,
                    "service_date": c.service_date.isoformat(),
                    "submission_date": c.submission_date.isoformat() if c.submission_date else None,
                    "adjudication_date": c.adjudication_date.isoformat() if c.adjudication_date else None,
                    "status": c.status,
                    "billed_amount": round(c.billed_amount or Decimal("0"), 2),
                    "allowed_amount": round(c.allowed_amount, 2) if c.allowed_amount is not None else None,
                    "paid_amount": round(c.paid_amount, 2) if c.paid_amount is not None else None,
                    "attempt_count": len(attempts),
                    "latest_rejection": attempts[-1].rejection_reason if attempts and attempts[-1].rejection_reason else None,
                })

            return {
                "report_code": "FR03",
                "report_name": "Claim Status and Processing Register",
                "clinic_id": clinic_id,
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
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            lines = session.query(
                FinancialClaimLineModel, FinancialClaimModel
            ).join(
                FinancialClaimModel, FinancialClaimLineModel.claim_id == FinancialClaimModel.claim_id
            ).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
                FinancialClaimLineModel.line_status.in_(["submitted", "partially_paid", "denied"]),
            ).all()

            rows = []
            for line, claim in lines:
                allowed = line.allowed_amount or Decimal("0")
                paid = line.paid_amount or Decimal("0")
                adjusted = line.adjusted_amount or Decimal("0")
                supported_balance = max(Decimal("0"), (line.billed_amount or Decimal("0")) - paid - adjusted)

                rows.append({
                    "line_id": line.line_id,
                    "claim_id": line.claim_id,
                    "procedure_code": line.procedure_code,
                    "code_system": line.code_system,
                    "code_version": line.code_version,
                    "units": line.units,
                    "payer_id": claim.payer_id,
                    "service_date": claim.service_date.isoformat(),
                    "billed_amount": round(line.billed_amount or Decimal("0"), 2),
                    "allowed_amount": round(allowed, 2) if line.allowed_amount is not None else None,
                    "paid_amount": round(paid, 2) if line.paid_amount is not None else None,
                    "supported_balance": round(supported_balance, 2),
                    "line_status": line.line_status,
                    "denial_code": line.denial_code,
                    "denial_reason": line.denial_reason,
                })

            return {
                "report_code": "FR04",
                "report_name": "Unpaid and Partially Paid Procedure Lines",
                "clinic_id": clinic_id,
                "rows": rows,
                "unpaid_lines_count": len(rows),
                "total_unpaid_balance": round(sum(r["supported_balance"] for r in rows), 2),
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
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            # Payer Adjudication Denials (from Claim Lines)
            lines = session.query(
                FinancialClaimLineModel, FinancialClaimModel
            ).join(
                FinancialClaimModel, FinancialClaimLineModel.claim_id == FinancialClaimModel.claim_id
            ).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.service_date >= period_start,
                FinancialClaimModel.service_date <= period_end,
                FinancialClaimLineModel.line_status == "denied",
            ).all()

            denial_groups: Dict[str, Dict[str, Any]] = {}
            for line, claim in lines:
                code = line.denial_code or "UNKNOWN_DENIAL"
                key = f"{claim.payer_id}:{code}"
                if key not in denial_groups:
                    denial_groups[key] = {
                        "category": "Payer Adjudication Denial",
                        "payer_id": claim.payer_id,
                        "code": code,
                        "reason": line.denial_reason or "Reason unavailable from payer",
                        "affected_lines_count": 0,
                        "total_denied_amount": Decimal("0"),
                    }
                denial_groups[key]["affected_lines_count"] += 1
                denial_groups[key]["total_denied_amount"] += line.billed_amount or Decimal("0")

            # Technical Rejections (from Submission Attempts)
            attempts = session.query(
                FinancialSubmissionAttemptModel, FinancialClaimModel
            ).join(
                FinancialClaimModel, FinancialSubmissionAttemptModel.claim_id == FinancialClaimModel.claim_id
            ).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialSubmissionAttemptModel.outcome == "rejected_technical",
            ).all()

            for att, claim in attempts:
                key = f"{claim.payer_id}:TECH_REJECT:{att.rejection_reason or 'UNKNOWN'}"
                if key not in denial_groups:
                    denial_groups[key] = {
                        "category": "Technical Clearinghouse Rejection",
                        "payer_id": claim.payer_id,
                        "code": "TECH_REJECT",
                        "reason": att.rejection_reason or "Technical format validation failure",
                        "affected_lines_count": 0,
                        "total_denied_amount": Decimal("0"),
                    }
                denial_groups[key]["affected_lines_count"] += 1
                denial_groups[key]["total_denied_amount"] += claim.billed_amount or Decimal("0")

            rows = list(denial_groups.values())
            for r in rows:
                r["total_denied_amount"] = round(r["total_denied_amount"], 2)

            return {
                "report_code": "FR05",
                "report_name": "Rejection and Denial Reasons",
                "clinic_id": clinic_id,
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
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        RA05 Invariant:
        A remittance of AED 100, clinic payment of AED 100 and bank receipt of AED 100
        for the same settlement shows AED 100 advised and AED 100 received (NOT AED 300).
        """
        session = db or SessionLocal()
        try:
            remittances = session.query(FinancialRemittanceModel).filter(
                FinancialRemittanceModel.clinic_id == clinic_id,
                FinancialRemittanceModel.remittance_date >= period_start,
                FinancialRemittanceModel.remittance_date <= period_end,
            ).all()

            receipts = session.query(FinancialPaymentReceiptModel).filter(
                FinancialPaymentReceiptModel.clinic_id == clinic_id,
                FinancialPaymentReceiptModel.received_date >= period_start,
                FinancialPaymentReceiptModel.received_date <= period_end,
            ).all()

            # Link receipts to remittances by remittance_id
            matched_remittance_ids = set()
            reconciled_rows = []

            for rem in remittances:
                matched_receipts = [r for r in receipts if r.remittance_id == rem.remittance_id]
                receipt_amount = sum(r.amount for r in matched_receipts)
                is_bank_confirmed = any(r.bank_confirmed for r in matched_receipts)

                if matched_receipts:
                    matched_remittance_ids.add(rem.remittance_id)

                reconciled_rows.append({
                    "remittance_id": rem.remittance_id,
                    "payment_reference": rem.payment_reference,
                    "payer_id": rem.payer_id,
                    "remittance_date": rem.remittance_date.isoformat(),
                    "payer_advised_amount": round(rem.total_paid, 2),
                    "received_cash_amount": round(receipt_amount, 2),
                    "reconciliation_status": "MATCHED" if abs(rem.total_paid - receipt_amount) < Decimal("0.01") else "DISCREPANCY",
                    "bank_confirmed": is_bank_confirmed,
                })

            # Unallocated receipts
            unallocated_receipts = [r for r in receipts if not r.remittance_id or r.remittance_id not in matched_remittance_ids]
            unallocated_amount = sum(r.amount for r in unallocated_receipts)

            return {
                "report_code": "FR06",
                "report_name": "Payer Receipts and Reconciliation",
                "clinic_id": clinic_id,
                "reconciled_items": reconciled_rows,
                "unallocated_receipts_count": len(unallocated_receipts),
                "unallocated_cash_total": round(unallocated_amount, 2),
                "total_payer_advised": round(sum(r.total_paid for r in remittances), 2),
                "total_cash_received": round(sum(r.amount for r in receipts), 2),
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
        as_of_cutoff: Optional[datetime] = None,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        session = db or SessionLocal()
        try:
            cutoff = as_of_cutoff or datetime.now(timezone.utc)
            claims = session.query(FinancialClaimModel).filter(
                FinancialClaimModel.clinic_id == clinic_id,
                FinancialClaimModel.status.in_(["submitted", "adjudicated", "partially_paid"]),
            ).all()

            buckets = {
                "0_30_days": {"count": 0, "amount": Decimal("0")},
                "31_60_days": {"count": 0, "amount": Decimal("0")},
                "61_90_days": {"count": 0, "amount": Decimal("0")},
                "91_120_days": {"count": 0, "amount": Decimal("0")},
                "120_plus_days": {"count": 0, "amount": Decimal("0")},
            }

            for c in claims:
                # Age basis: service_date
                claim_date = c.service_date.replace(tzinfo=timezone.utc) if c.service_date.tzinfo is None else c.service_date
                age_days = (cutoff - claim_date).days
                unpaid = max(Decimal("0"), (c.billed_amount or Decimal("0")) - (c.paid_amount or Decimal("0")))

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

                buckets[b]["count"] += 1
                buckets[b]["amount"] += unpaid

            for b in buckets.values():
                b["amount"] = round(b["amount"], 2)

            return {
                "report_code": "FR07",
                "report_name": "Outstanding Items and Ageing",
                "clinic_id": clinic_id,
                "as_of_cutoff": cutoff.isoformat(),
                "ageing_basis": "service_date",
                "buckets": buckets,
                "total_outstanding": round(sum(b["amount"] for b in buckets.values()), 2),
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
                # Preserve the legacy numeric JSON contract. Hospital snapshots
                # use exact decimal strings in freeze_hospital_summary instead.
                payload_json=json.dumps(payload, default=float),
                summary_metrics_json=json.dumps(payload.get("metrics") or {}, default=float),
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
