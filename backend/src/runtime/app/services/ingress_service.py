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
from app.services.cursor_store import cursor_store

from app.integrations.hospital_read.intake import HospitalIntakeMixin

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


class IngressBatchResult(dict):
    """
    Dual sync/async compatible dictionary result.
    Can be used directly as a dict or awaited in async contexts.
    """

    def __await__(self):
        async def _coro():
            return self
        return _coro().__await__()


class CanonicalIngressService(HospitalIntakeMixin):
    """
    G5 Replay-Safe Ingestion Service with Durable Ingestion Ledger.
    Sequence:
    Receive page / batch
     -> authenticate + validate source owner
     -> normalize + preserve source version
     -> stage source observation + canonical fact
     -> commit durable facts and ingest ledger
     -> advance checkpoint (consistent commit / outbox)
     -> publish only eligible, lineage-complete facts
     -> governed evaluation on qualified event
    """

    def __init__(self):
        self.logger = logging.getLogger("CanonicalIngress")

    def submit_batch(
        self,
        connector_id: str,
        records: List[Dict[str, Any]],
        clinic_id: Optional[str] = None,
        checkpoint: Optional[str] = None,
        is_opaque_cursor: bool = False,
    ) -> Dict[str, Any]:
        """
        Accepts a batch of canonical evidence context records.
        Validates source authority, stages facts, commits atomically to repository
        and ingestion ledger, and advances checkpoints consistently.
        """
        if not records:
            return IngressBatchResult({"status": "Success", "processed": 0, "gated": 0, "tombstoned": 0, "duplicates_skipped": 0})

        # G3/G5 Input Safety: Payload size enforcement
        payload_bytes = json.dumps(records).encode("utf-8")
        validate_payload_safety(payload_bytes)

        # Resolve authoritative clinic scope
        binding = connector_store.get(connector_id)
        resolved_clinic = clinic_id or (binding.allowed_clinic_id if binding else "default")

        from app.models.evidence import EvidenceModel, IngestionLedgerModel
        from app.db.database import SessionLocal

        processed = 0
        gated = 0
        tombstoned = 0
        last_context_type = "Unknown"

        with SessionLocal() as db:
            seen_in_batch = set()
            for r in records:
                context_type = r.get("context_type", "Unknown")
                last_context_type = context_type

                # G3 Gate: Source Authority Check (Registered, AUTHORIZED_READY, Clinic Scope, Permitted Resource)
                validate_source_authority(
                    instance_id=connector_id,
                    verified_clinic_id=resolved_clinic,
                    resource_kind=context_type,
                )

                detail = r.get("detail", {})
                resource_id = detail.get("id", "")
                source_version = _compute_source_version(detail)

                # G5 Idempotency dedupe_key = hash(clinic_id, connector_instance_id, resource_kind, external_record_id, source_version)
                source_key_str = (
                    f"{resolved_clinic}_{connector_id}_{context_type}_{resource_id}_{source_version}"
                )
                fact_key = hashlib.sha256(source_key_str.encode()).hexdigest()

                # Within-batch duplicate check
                if fact_key in seen_in_batch:
                    continue

                # G5 Event Case 1: Deduplication / Replay check against DB
                existing = db.query(EvidenceModel).filter(
                    EvidenceModel.source_connector == connector_id,
                    EvidenceModel.fact_key == fact_key,
                ).first()

                if existing:
                    seen_in_batch.add(fact_key)
                    continue  # ALREADY_PROCESSED — idempotent no-op; retain original evidence ID

                # G5 Event Case 3: Deletion / Revocation / Tombstone
                is_deleted = (
                    detail.get("deleted") is True
                    or r.get("is_deleted") is True
                    or str(detail.get("status", "")).lower() in ("entered-in-error", "revoked", "deleted")
                )
                if is_deleted:
                    tombstoned += 1

                # G5 Event Case 2: Out-of-order correction & version chain
                # Check for prior version of same entity without overwriting history
                prior_evidence = db.query(EvidenceModel).filter(
                    EvidenceModel.source_connector == connector_id,
                    EvidenceModel.canonical_entity == context_type,
                    EvidenceModel.external_record_id == resource_id,
                ).order_by(EvidenceModel.version.desc()).first()

                version = (prior_evidence.version + 1) if prior_evidence else 1
                previous_version_id = prior_evidence.evidence_id if prior_evidence else None

                # G3 Semantic Mapping & Quality Gate / Publication Boundary
                mapping_id = r.get("mapping_id")
                mapping_version = r.get("mapping_version", "1.0")
                raw_status = detail.get("raw_status") or detail.get("status")
                quality_state = "VALID"
                canonical_val = None

                if is_deleted:
                    quality_state = "TOMBSTONED"
                elif mapping_id and raw_status is not None:
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

                # G3/G5 Publication Boundary: Only VALID facts (not tombstoned/ambiguous) are eligible for decision publication
                is_eligible = (quality_state == "VALID" and not is_deleted)

                # Parse effective time if present
                effective_dt = None
                raw_effective = detail.get("effective_time") or detail.get("period", {}).get("start")
                if raw_effective:
                    try:
                        val_str = str(raw_effective).replace("Z", "+00:00")
                        effective_dt = datetime.fromisoformat(val_str)
                    except Exception:
                        pass

                # Stage and commit evidence model
                evidence = EvidenceModel(
                    evidence_id=evidence_id,
                    canonical_entity=context_type,
                    fact_key=fact_key,
                    fact_value_str=fact_value,
                    source_connector=connector_id,
                    retrieval_timestamp=datetime.utcnow(),
                    evidence_type="Sync",
                    version=version,
                    previous_version_id=previous_version_id,
                    tombstone=is_deleted,
                    effective_time=effective_dt,
                    clinic_id=resolved_clinic,
                    external_record_id=resource_id,
                    resource_kind=context_type,
                    source_version=source_version,
                    metadata_json=json.dumps({
                        "source_version": source_version,
                        "resource_id": resource_id,
                        "clinic_id": resolved_clinic,
                        "quality_state": quality_state,
                        "mapping_version": mapping_version,
                        "eligible_for_decision": is_eligible,
                    }),
                )

                # Stage and commit Ingestion Ledger entry
                ledger_entry = IngestionLedgerModel(
                    id=fact_key,
                    clinic_id=resolved_clinic,
                    connector_instance_id=connector_id,
                    resource_kind=context_type,
                    external_record_id=resource_id,
                    source_version=source_version,
                    status="TOMBSTONED" if is_deleted else "COMMITTED",
                    evidence_id=evidence_id,
                    effective_at=effective_dt,
                )

                try:
                    with db.begin_nested():
                        db.add(evidence)
                        db.add(ledger_entry)
                    processed += 1
                    seen_in_batch.add(fact_key)
                except exc.IntegrityError:
                    pass  # Idempotent duplicate at DB constraint level

            db.commit()

        # Advance checkpoint only AFTER durable facts and ledger are committed
        if checkpoint:
            cursor_store.commit(
                connector_id=connector_id,
                resource_type=last_context_type,
                checkpoint=checkpoint,
                clinic_id=resolved_clinic,
                is_opaque=is_opaque_cursor,
            )

        duplicates_skipped = len(records) - processed
        self.logger.info(
            f"[{connector_id}] Processed {processed} records ({duplicates_skipped} duplicates skipped, "
            f"{tombstoned} tombstoned, {gated} ambiguous/gated)."
        )
        return IngressBatchResult({
            "status": "Success",
            "processed": processed,
            "gated": gated,
            "tombstoned": tombstoned,
            "duplicates_skipped": duplicates_skipped,
            "connector_id": connector_id,
            "clinic_id": resolved_clinic,
        })


canonical_ingress = CanonicalIngressService()
