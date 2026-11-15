import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import exc

from app.integrations.core.source_authority import (
    validate_source_authority,
    validate_payload_safety,
    connector_store,
)
from app.integrations.core.mapping_registry import mapping_registry

logger = logging.getLogger(__name__)


def _extract_canonical_facts(
    context_type: str, resource: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract only the minimum canonical facts Sentinel needs.
    Does NOT store the full FHIR payload — avoids excessive PHI in fact_value_str.
    Stores only: resource_type, id, status, last_updated, source_version.
    """
    meta = resource.get("meta", {})
    facts = {
        "resource_type": context_type,
        "resource_id": resource.get("id", ""),
        "status": resource.get("status", ""),
        "last_updated": meta.get("lastUpdated", ""),
        "version_id": meta.get("versionId", ""),
    }
    # Add resource-type-specific minimal fields
    if context_type == "Patient":
        names = resource.get("name")
        if isinstance(names, list) and names and isinstance(names[0], dict):
            facts["family"] = names[0].get("family", "")
        elif isinstance(names, str):
            facts["family"] = names
        else:
            facts["family"] = ""
        facts["birth_date"] = resource.get("birthDate", "")
    elif context_type == "Encounter":
        facts["class"] = resource.get("class", {}).get("code", "")
        facts["period_start"] = resource.get("period", {}).get("start", "")
        facts["subject_reference"] = resource.get("subject", {}).get("reference", "")
    elif context_type == "Coverage":
        facts["payor"] = (
            resource.get("payor", [{}])[0].get("reference", "")
            if resource.get("payor") else ""
        )
        facts["beneficiary_reference"] = resource.get("beneficiary", {}).get("reference", "")
    elif context_type == "Claim":
        facts["status"] = resource.get("status", "")
        facts["total"] = resource.get("total", {}).get("value", "")
        facts["patient_reference"] = resource.get("patient", {}).get("reference", "")
    elif context_type == "Remittance":
        facts["payment_status"] = resource.get("payment_status", "")
        facts["payment_amount"] = resource.get("payment_amount", "")
    return facts


def _compute_source_version(resource: Dict[str, Any]) -> str:
    """
    Determines the most reliable source version for idempotency.
    Priority: versionId > lastUpdated > deterministic payload hash.
    Prevents duplicate ingestion even when lastUpdated is absent.
    """
    meta = resource.get("meta", {})
    version_id = meta.get("versionId", "")
    last_updated = meta.get("lastUpdated", "")

    if version_id:
        return f"vid:{version_id}"
    if last_updated:
        return f"ts:{last_updated}"

    # Fallback: deterministic hash of the sorted payload
    payload_bytes = json.dumps(resource, sort_keys=True).encode()
    return f"hash:{hashlib.sha256(payload_bytes).hexdigest()[:16]}"


class CanonicalIngressService:
    """
    G3 Canonical Ingress Service (Evidence Input & Authority Boundary).
    Handles ingestion of canonical evidence records from verified external connectors.
    Enforces:
    - Secure intake authority check: registered, ready, matching clinic scope, permitted resource.
    - Input safety: payload size limit and safe JSON parsing.
    - Idempotency dedupe key: hash(clinic_id, connector_id, resource_kind, resource_id, source_version).
    - Publication boundary: Ambiguous or unmapped statuses are gated from decision emission.
    """

    def __init__(self):
        self.logger = logging.getLogger("CanonicalIngress")

    async def submit_batch(
        self,
        connector_id: str,
        records: List[Dict[str, Any]],
        clinic_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Accepts a batch of canonical evidence context records.
        Validates source authority and semantic mappings before durable storage.
        """
        if not records:
            return {"status": "Success", "processed": 0, "gated": 0}

        # G3 Input Safety: Payload size enforcement
        payload_bytes = json.dumps(records).encode("utf-8")
        validate_payload_safety(payload_bytes)

        # Resolve authoritative clinic scope
        binding = connector_store.get(connector_id)
        resolved_clinic = clinic_id or (binding.allowed_clinic_id if binding else "default")

        from app.models.evidence import EvidenceModel
        from app.db.database import SessionLocal

        processed = 0
        gated = 0

        with SessionLocal() as db:
            for r in records:
                context_type = r.get("context_type", "Unknown")

                # G3 Gate: Source Authority Check (Registered, AUTHORIZED_READY, Clinic Scope, Permitted Resource)
                validate_source_authority(
                    instance_id=connector_id,
                    verified_clinic_id=resolved_clinic,
                    resource_kind=context_type,
                )

                detail = r.get("detail", {})
                resource_id = detail.get("id", "")
                source_version = _compute_source_version(detail)

                # G3 Idempotency key: clinic + connector + type + id + version
                source_key_str = (
                    f"{resolved_clinic}_{connector_id}_{context_type}_{resource_id}_{source_version}"
                )
                fact_key = hashlib.sha256(source_key_str.encode()).hexdigest()

                # Deduplication check
                existing = db.query(EvidenceModel).filter(
                    EvidenceModel.source_connector == connector_id,
                    EvidenceModel.fact_key == fact_key,
                ).first()

                if existing:
                    continue  # ALREADY_PROCESSED — idempotent

                # G3 Semantic Mapping & Quality Gate / Publication Boundary
                mapping_id = r.get("mapping_id")
                mapping_version = r.get("mapping_version", "1.0")
                raw_status = detail.get("raw_status") or detail.get("status")
                quality_state = "VALID"
                canonical_val = None

                if mapping_id and raw_status is not None:
                    map_status, canonical_val = mapping_registry.evaluate_mapping(
                        mapping_id=mapping_id,
                        version=mapping_version,
                        source_value=raw_status,
                        context=detail,
                    )
                    if map_status in ("STATUS_UNRESOLVED", "NOT_EVALUABLE"):
                        quality_state = map_status
                        gated += 1

                # Extract only minimum-necessary canonical facts
                canonical_facts = _extract_canonical_facts(context_type, detail)
                if canonical_val is not None:
                    canonical_facts["mapped_value"] = canonical_val
                canonical_facts["quality_state"] = quality_state

                fact_value = json.dumps(canonical_facts)
                evidence_id = f"evd_{uuid.uuid4().hex}"

                # G3 Publication Boundary: Only VALID facts are eligible for decision generation
                is_eligible = (quality_state == "VALID")

                evidence = EvidenceModel(
                    evidence_id=evidence_id,
                    canonical_entity=context_type,
                    fact_key=fact_key,
                    fact_value_str=fact_value,
                    source_connector=connector_id,
                    retrieval_timestamp=datetime.utcnow(),
                    evidence_type="Sync",
                    metadata_json=json.dumps({
                        "source_version": source_version,
                        "resource_id": resource_id,
                        "clinic_id": resolved_clinic,
                        "quality_state": quality_state,
                        "mapping_version": mapping_version,
                        "eligible_for_decision": is_eligible,
                    }),
                )
                try:
                    with db.begin_nested():
                        db.add(evidence)
                    processed += 1
                except exc.IntegrityError:
                    pass  # Idempotent duplicate

            db.commit()

        duplicates_skipped = len(records) - processed
        self.logger.info(
            f"[{connector_id}] Processed {processed} records ({duplicates_skipped} duplicates skipped, {gated} ambiguous/gated)."
        )
        return {
            "status": "Success",
            "processed": processed,
            "gated": gated,
            "duplicates_skipped": duplicates_skipped,
            "connector_id": connector_id,
            "clinic_id": resolved_clinic,
        }


canonical_ingress = CanonicalIngressService()
