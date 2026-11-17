"""Reviewed-profile normalization shared by REST and CSV; no guessed facts."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from app.integrations.hospital_read.identity import entity_key, required_id

MONEY = frozenset({"billed_amount", "allowed_amount", "paid_amount", "patient_share",
    "adjusted_amount", "amount", "payer_balance", "denied_amount"})
REFERENCES = frozenset({"patient_id", "provider_id", "payer_id", "tpa_id", "department_id",
    "location_id", "encounter_id", "encounter_class", "claim_id", "line_id", "remittance_id",
    "settlement_id", "payment_reference", "procedure_code", "code_system", "code_version",
    "denial_code", "denial_reason", "rejection_reason", "attribution_basis", "allocation_basis", "receipt_type", "department_code"})
TIME_FIELDS = frozenset({"source_time", "effective_time", "service_date", "submission_date",
    "adjudication_date", "received_date"})
FIELDS = MONEY | REFERENCES | TIME_FIELDS | {"id", "status", "source_version", "currency",
    "units", "attempt_number", "source_sequence", "deleted", "bank_confirmed"}


def exact_money(value):
    if isinstance(value, (bool, float)):
        raise ValueError("Money must be a decimal string or integer, never binary float")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid exact money") from None
    if (not number.is_finite() or len(number.as_tuple().digits) > 28
            or number.as_tuple().exponent < -8 or number.adjusted() >= 20):
        raise ValueError("Money exceeds supported precision/scale")
    return format(number, "f")


def source_time(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Source time must have an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_record(raw, *, resource_kind, context):
    profile = context
    if resource_kind not in profile.endpoints:
        raise ValueError("Resource capability unavailable")
    if not isinstance(raw, dict):
        raise ValueError("Source row must be an object")
    external_id = required_id(raw.get("id"), "id")
    key = entity_key(org_id=profile.org_id, clinic_id=profile.clinic_id,
        connector_id=profile.connector_id, resource_kind=resource_kind,
        external_id=external_id, parent_id=raw.get("claim_id") if resource_kind in {"ClaimLine", "SubmissionAttempt"} else None)
    version = required_id(raw.get("source_version"), "source_version")
    deleted = raw.get("deleted") is True
    status = raw.get("status")
    if status is not None or not deleted:
        status = required_id(status, "status")
    facts = {}
    for name in MONEY | REFERENCES | TIME_FIELDS:
        if name not in raw:
            continue  # absence and explicit null stay distinct
        value = raw[name]
        if value is not None:
            if name in MONEY:
                value = exact_money(value)
            elif name in TIME_FIELDS:
                value = source_time(value)
            else:
                value = required_id(value, name)
        facts[name] = value
    if any(facts.get(name) is not None for name in MONEY):
        currency = required_id(raw.get("currency"), "currency")
        if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
            raise ValueError("Explicit three-letter source currency required")
        facts["currency"] = currency
    elif "currency" in raw:
        facts["currency"] = raw["currency"]
    for name in ("source_sequence", "units", "attempt_number"):
        if name in raw and raw[name] is not None:
            value = raw[name]
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            if type(value) is not int or value < (0 if name == "source_sequence" else 1):
                raise ValueError(f"Invalid {name}")
            facts[name] = value
    for name in ("deleted", "bank_confirmed"):
        if name in raw:
            if type(raw[name]) is not bool:
                raise ValueError(f"Invalid boolean {name}")
            facts[name] = raw[name]
    normalized_status = profile.status_map.get(resource_kind, {}).get(status)
    source_department = facts.pop("department_code", None)
    if source_department is None and not profile.department_ids_are_canonical:
        source_department = facts.get("department_id")
    if source_department is not None:
        facts["source_department_code"] = source_department
        facts["department_id"] = profile.department_map.get(source_department)
        facts["department_mapping_state"] = "MAPPED" if facts["department_id"] else "UNMAPPED"
        if facts["department_id"]:
            facts["attribution_basis"] = f"{profile.mapping_id}@{profile.mapping_version}:{profile.revision}"
    for field, kind in (("patient_id", "Patient"), ("provider_id", "Provider"),
                        ("payer_id", "Payer"), ("tpa_id", "TPA")):
        if facts.get(field) is not None:
            facts["source_" + field] = facts[field]
            facts[field] = entity_key(org_id=profile.org_id, clinic_id=profile.clinic_id,
                connector_id=profile.connector_id, resource_kind=kind, external_id=facts[field])
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    detail = {**facts, "id": key, "raw_id": external_id, "status": normalized_status,
        "raw_status": status, "source_version": version, "source_digest": digest,
        "meta": {"versionId": version}, "org_id": profile.org_id, "clinic_id": profile.clinic_id,
        "quality_state": "VALID" if deleted or normalized_status else "STATUS_UNRESOLVED",
        "profile_revision": profile.revision, "identity_scheme": "hospital-source-v1"}
    if raw.get("claim_id") is not None:
        detail["parent_claim_key"] = entity_key(org_id=profile.org_id, clinic_id=profile.clinic_id,
            connector_id=profile.connector_id, resource_kind="Claim", external_id=raw["claim_id"])
    return ({"context_type": resource_kind, "mapping_id": profile.mapping_id,
        "mapping_version": profile.mapping_version, "detail": detail},)
