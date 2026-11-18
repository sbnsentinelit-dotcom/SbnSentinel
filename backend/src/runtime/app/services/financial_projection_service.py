"""H05 source projection, never a calculation or recommendation engine."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from app.models.evidence import EvidenceModel, IngestionLedgerModel
from app.models.organization import OrganizationClinicModel
from app.models.hospital_dimensions import HospitalDepartment
from app.models.hospital_processing import HospitalProjectionReceipt, HospitalSettlementLink, HospitalSettlementObservation
from app.models.financial_reporting import (
    FinancialClaimModel, FinancialClaimLineModel, FinancialSubmissionAttemptModel,
    FinancialRemittanceModel, FinancialPaymentReceiptModel,
)
from app.integrations.hospital_read.mappings import exact_money


@dataclass(frozen=True)
class ProjectionResult:
    status: str
    reason: str | None = None


def parsed_time(value):
    if value is None:
        return None
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Source time is unknown")
    # Shared financial timestamp columns are legacy UTC-without-timezone fields.
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def apply_committed(db, evidence, *, consumer_version):
    if consumer_version != "hospital-financial-v1":
        return ProjectionResult("DEFERRED", "Unsupported projection consumer version")
    ledger = db.scalars(select(IngestionLedgerModel).where(
        IngestionLedgerModel.evidence_id == evidence.evidence_id)).first()
    if not ledger or ledger.status not in {"COMMITTED", "TOMBSTONED"}:
        return ProjectionResult("DEFERRED", "Committed ledger missing")
    facts = json.loads(evidence.fact_value_str)
    if facts.get("identity_scheme") != "hospital-source-v1" or facts.get("quality_state") != "VALID":
        return ProjectionResult("DEFERRED", "Validated hospital facts unavailable")
    db.scalars(select(OrganizationClinicModel).where(
        OrganizationClinicModel.id == ledger.clinic_id).with_for_update()).one()
    receipt = db.scalars(select(HospitalProjectionReceipt).where(
        HospitalProjectionReceipt.ledger_id == ledger.id,
        HospitalProjectionReceipt.consumer_version == consumer_version)).first()
    if receipt:
        return ProjectionResult("DUPLICATE")
    kind = evidence.resource_kind
    key = facts["id"]
    parent = facts.get("parent_claim_key")
    model = {"Claim": FinancialClaimModel, "ClaimLine": FinancialClaimLineModel,
             "SubmissionAttempt": FinancialSubmissionAttemptModel, "Remittance": FinancialRemittanceModel,
             "Receipt": FinancialPaymentReceiptModel}.get(kind)
    if model is None:
        return ProjectionResult("DEFERRED", "Unsupported projection resource")
    if kind in {"ClaimLine", "SubmissionAttempt"}:
        claim = db.get(FinancialClaimModel, parent)
        if not claim or claim.clinic_id != ledger.clinic_id or claim.tombstone:
            return ProjectionResult("DEFERRED", "Parent claim unavailable")
    settlement = None
    if kind == "Receipt":
        settlement = db.get(HospitalSettlementLink, key)
        if not settlement or settlement.clinic_id != ledger.clinic_id or not settlement.basis_ref:
            return ProjectionResult("DEFERRED", "Verified economic settlement crosswalk required")
        if facts.get("deleted"):
            return ProjectionResult("DEFERRED", "Settlement correction requires explicit reconciliation")
        key = settlement.economic_receipt_key
        prior_observation = db.scalars(select(HospitalSettlementObservation).join(EvidenceModel,
                                                                                  EvidenceModel.evidence_id == HospitalSettlementObservation.evidence_id).where(
            HospitalSettlementObservation.receipt_id == key,
            EvidenceModel.external_record_id == facts["id"],
            EvidenceModel.evidence_id != evidence.evidence_id).limit(1)).first()
        if prior_observation:
            # A new source version is not a second economic receipt. Reversals
            # and changed confirmations need the approved reconciliation basis;
            # never OR an old confirmation into a corrected current observation.
            return ProjectionResult("DEFERRED", "Settlement observation correction requires reconciliation")
    row = db.get(model, key)
    sequence = facts.get("source_sequence")
    if row and kind != "Receipt" and row.source_evidence_id != evidence.evidence_id:
        if sequence is None or row.source_sequence is None:
            return ProjectionResult("DEFERRED", "Authoritative source ordering unavailable")
        if sequence == row.source_sequence:
            return ProjectionResult("DEFERRED", "Conflicting equal source order")
        if sequence < row.source_sequence:
            db.add(HospitalProjectionReceipt(ledger_id=ledger.id, evidence_id=evidence.evidence_id,
                                             consumer_version=consumer_version, result="HISTORICAL", applied_at=datetime.now(timezone.utc)))
            db.flush()
            return ProjectionResult("HISTORICAL")
    if facts.get("deleted"):
        if row is None:
            return ProjectionResult("DEFERRED", "Deleted parent fact unavailable")
        if not hasattr(row, "tombstone"):
            return ProjectionResult("DEFERRED", "Deletion semantics unavailable for resource")
        row.tombstone = True
    else:
        required = {"Claim": ("patient_id", "provider_id", "payer_id", "billed_amount", "currency", "service_date"),
                    "ClaimLine": ("procedure_code", "code_system", "code_version", "units", "billed_amount"),
                    "SubmissionAttempt": ("attempt_number", "submission_date"),
                    "Remittance": ("payer_id", "payment_reference", "amount", "currency", "received_date"),
                    "Receipt": ("amount", "currency", "received_date", "payment_reference", "receipt_type", "bank_confirmed")}[kind]
        if any(facts.get(name) is None for name in required):
            return ProjectionResult("DEFERRED", "Required source facts unavailable")
        department_id = facts.get("department_id")
        attribution_version_id, attribution_time = None, None
        if department_id is not None:
            department = db.get(HospitalDepartment, department_id)
            if not department or department.clinic_id != ledger.clinic_id or not facts.get("attribution_basis"):
                return ProjectionResult("DEFERRED", "Department attribution unresolved")
            if kind == "ClaimLine":
                basis = facts.get("attribution_time_basis")
                attribution_time = (claim.service_date if basis == "parent_claim_service_date" else
                                    parsed_time(facts.get("service_date" if basis == "line_service_date" else "effective_time")))
                if attribution_time is None:
                    return ProjectionResult("DEFERRED", "Attribution business time unavailable")
                from app.services.hospital_directory_service import HospitalDirectoryService
                resolved = HospitalDirectoryService(db).resolve_department(
                    facts["org_id"], ledger.clinic_id, department_id,
                    attribution_time.replace(tzinfo=timezone.utc))
                if resolved.status != "RESOLVED":
                    return ProjectionResult("DEFERRED", "Department attribution not effective at business time")
                attribution_version_id = resolved.records[0].id

        def money(name):
            return Decimal(exact_money(facts[name])) if facts.get(name) is not None else None
        if kind == "Claim":
            values = dict(clinic_id=ledger.clinic_id, org_id=facts["org_id"],
                          patient_id=facts["patient_id"], provider_id=facts["provider_id"], payer_id=facts["payer_id"],
                          tpa_id=facts.get("tpa_id"), status=facts["status"], billed_amount=money("billed_amount"),
                          allowed_amount=money("allowed_amount"), paid_amount=money("paid_amount"),
                          patient_share=money("patient_share"), currency=facts["currency"],
                          service_date=parsed_time(facts["service_date"]), submission_date=parsed_time(facts.get("submission_date")),
                          adjudication_date=parsed_time(facts.get("adjudication_date")), rejection_reason=facts.get("rejection_reason"), tombstone=False)
            primary = "claim_id"
        elif kind == "ClaimLine":
            if facts.get("currency") != claim.currency:
                return ProjectionResult("DEFERRED", "Line currency differs from parent")
            values = dict(claim_id=parent, clinic_id=ledger.clinic_id, department_id=department_id,
                          currency=facts["currency"], attribution_version_id=attribution_version_id,
                          attribution_time=attribution_time,
                          attribution_basis=facts.get("attribution_basis"), procedure_code=facts["procedure_code"],
                          code_system=facts["code_system"], code_version=facts["code_version"], units=facts["units"],
                          billed_amount=money("billed_amount"), allowed_amount=money("allowed_amount"),
                          paid_amount=money("paid_amount"), adjusted_amount=money("adjusted_amount"),
                          line_status=facts["status"], denial_code=facts.get("denial_code"), denial_reason=facts.get("denial_reason"), tombstone=False)
            primary = "line_id"
        elif kind == "SubmissionAttempt":
            values = dict(claim_id=parent, attempt_number=facts["attempt_number"],
                          submission_date=parsed_time(facts["submission_date"]), outcome=facts["status"],
                          rejection_reason=facts.get("rejection_reason"))
            primary = "attempt_id"
        elif kind == "Remittance":
            values = dict(clinic_id=ledger.clinic_id, payer_id=facts["payer_id"],
                          payment_reference=facts["payment_reference"], total_paid=money("amount"),
                          currency=facts["currency"], remittance_date=parsed_time(facts["received_date"]))
            primary = "remittance_id"
        else:
            if facts["receipt_type"] not in {"payer_payment", "patient_copay", "patient_selfpay"}:
                return ProjectionResult("DEFERRED", "Receipt type unmapped")
            if parent and not db.get(FinancialClaimModel, parent):
                return ProjectionResult("DEFERRED", "Receipt parent claim unavailable")
            allocated_department = department_id if facts.get("allocation_basis") else None
            if row:
                if (row.clinic_id != ledger.clinic_id or row.amount != money("amount") or row.currency != facts["currency"]
                        or row.department_id != allocated_department or row.claim_id != parent):
                    return ProjectionResult("DEFERRED", "Settlement observations require reconciliation")
                values = {"bank_confirmed": bool(row.bank_confirmed or facts["bank_confirmed"])}
            else:
                values = dict(clinic_id=ledger.clinic_id, amount=money("amount"), currency=facts["currency"],
                              received_date=parsed_time(facts["received_date"]), receipt_type=facts["receipt_type"],
                              bank_confirmed=facts["bank_confirmed"], claim_id=parent, department_id=allocated_department,
                              allocation_basis=facts.get("allocation_basis"), tombstone=False)
            primary = "receipt_id"
        if row is None:
            row = model(**{primary: key}, **values)
            db.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
    row.source_evidence_id = evidence.evidence_id
    row.source_sequence = sequence
    if kind == "Receipt":
        db.flush()
        db.add(HospitalSettlementObservation(evidence_id=evidence.evidence_id, receipt_id=key,
                                             basis_ref=settlement.basis_ref))
    db.add(HospitalProjectionReceipt(ledger_id=ledger.id, evidence_id=evidence.evidence_id,
                                     consumer_version=consumer_version, result="APPLIED", applied_at=datetime.now(timezone.utc)))
    db.flush()
    return ProjectionResult("APPLIED")
