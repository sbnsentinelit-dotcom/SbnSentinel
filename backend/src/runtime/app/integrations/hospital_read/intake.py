"""Hospital extension of the existing CanonicalIngressService, not a second intake."""
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select
from app.integrations.hospital_read.identity import delivery_identity


class HospitalIntakeMixin:
    hospital_contract_revision = "hsp01-v1"

    def _hospital_session_factory(self):
        from app.db.database import SessionLocal
        return getattr(self, "hospital_session_factory", SessionLocal)

    def _hospital_authority(self, profile, resource_kind, db=None):
        override = getattr(self, "hospital_authority_override", None)
        if override is not None:
            override(profile, resource_kind)
            return
        if db is None:
            with self._hospital_session_factory()() as session:
                return self._hospital_authority(profile, resource_kind, db=session)
        else:
            from app.services.hospital_source_service import assert_profile_ready
            from app.core.config import settings
            if settings.DEPLOYMENT_REGION != "UAE":
                raise PermissionError("Hospital deployment jurisdiction not approved")
            assert_profile_ready(db, profile, resource_kind)
            actor_id = getattr(self, "hospital_actor_id", None)
            if actor_id is not None:
                from app.models.user import User
                from app.services.hospital_scope_service import resolve_hospital_scope, require_capability
                actor = db.get(User, actor_id)
                scope = resolve_hospital_scope(db, actor, {"clinic_id": profile.clinic_id})
                require_capability(scope, "source.read")
                if not scope.all_departments:
                    raise PermissionError("Facility-wide import grant required")
            return

    def get_hospital_checkpoint(self, profile, resource_kind):
        from app.models.hospital_processing import HospitalCheckpoint
        with self._hospital_session_factory()() as db:
            row = db.get(HospitalCheckpoint, profile.cursor_binding(resource_kind))
            if row is None and not profile.resync_basis_ref:
                previous = db.scalars(select(HospitalCheckpoint).where(
                    HospitalCheckpoint.connector_id == profile.connector_id,
                    HospitalCheckpoint.clinic_id == profile.clinic_id,
                    HospitalCheckpoint.resource_kind == resource_kind)).yield_per(100)
                for old in previous:
                    if json.loads(old.checkpoint_json).get("transport", "rest") == "rest":
                        raise ValueError("Extraction profile changed; controlled resync authorization required")
            return json.loads(row.checkpoint_json) if row else None

    def submit_hospital_page(self, profile, resource_kind, records, rejected, checkpoint, *, raw_rows=None):
        from app.models.evidence import EvidenceModel, IngestionLedgerModel
        from app.models.event import OperationalEventModel
        from app.models.organization import OrganizationClinicModel
        from app.models.hospital_processing import HospitalCheckpoint, HospitalQuarantine, SourceIdentityCrosswalk, FacilityCrosswalk
        from app.models.hospital_processing import HospitalTransportObservation
        from app.models.hospital_dimensions import HospitalFacilityEnrollment
        self._hospital_authority(profile, resource_kind)
        binding_key = profile.cursor_binding(resource_kind, checkpoint.get("transport", "rest"))
        if checkpoint["binding"] != binding_key or len(records) + len(rejected) > profile.max_rows:
            raise ValueError("Invalid bounded checkpoint")
        if len(json.dumps(records, ensure_ascii=False).encode()) > profile.max_bytes:
            raise ValueError("Normalized batch exceeds byte limit; reduce the reviewed page row bound")
        counts = dict.fromkeys(("accepted", "duplicate", "quarantined", "deferred", "failed"), 0)
        raw_rows = raw_rows if raw_rows is not None else [record["detail"] for record in records] + [row.get("payload") for row in rejected]
        if len(json.dumps(raw_rows, ensure_ascii=False).encode()) > profile.max_bytes:
            raise ValueError("Protected raw page exceeds byte limit")
        page_id = checkpoint.get("page_id") or hashlib.sha256(json.dumps(
            [binding_key, checkpoint.get("token"), raw_rows, rejected], sort_keys=True).encode()).hexdigest()
        now = datetime.now(timezone.utc)
        with self._hospital_session_factory()() as db:
            facility = db.scalars(select(OrganizationClinicModel).where(
                OrganizationClinicModel.id == profile.clinic_id,
                OrganizationClinicModel.organization_id == profile.org_id).with_for_update()).first()
            if not facility:
                raise PermissionError("Canonical source facility unavailable")
            if not db.get(HospitalFacilityEnrollment, profile.clinic_id):
                raise PermissionError("Explicit hospital enrollment required before ingestion")

            def quarantine(index, reason, payload):
                serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                if len(serialized.encode()) > profile.max_bytes:
                    raise ValueError("Quarantine payload exceeds protected row bound")
                digest = hashlib.sha256(serialized.encode()).hexdigest()
                identity = hashlib.sha256(json.dumps([binding_key, page_id, index, digest, reason]).encode()).hexdigest()
                if not db.scalar(select(HospitalQuarantine.id).where(HospitalQuarantine.quarantine_key == identity)):
                    source = payload if isinstance(payload, dict) else {}
                    db.add(HospitalQuarantine(binding=binding_key, row_index=index, reason=reason, created_at=now,
                                              quarantine_key=identity, page_id=page_id, payload_digest=digest, payload_json=serialized,
                                              source_id=str(source.get("id"))[:1000] if source.get("id") is not None else None,
                                              source_version=str(source.get("source_version"))[:1000] if source.get("source_version") is not None else None))
                    db.flush()

            def observe(ledger_id, facts):
                transport = checkpoint.get("transport", "rest")
                identity = hashlib.sha256(json.dumps([ledger_id, transport, facts["source_digest"]]).encode()).hexdigest()
                if not db.get(HospitalTransportObservation, identity):
                    db.add(HospitalTransportObservation(id=identity, ledger_id=ledger_id,
                                                        transport=transport, raw_digest=facts["source_digest"]))
            for rejected_row in rejected:
                if "payload" not in rejected_row:
                    raise ValueError("Recoverable quarantine payload required")
                quarantine(rejected_row["row_index"], rejected_row["reason"], rejected_row["payload"])
                counts["quarantined"] += 1
            for index, record in enumerate(records):
                row_index = checkpoint.get("record_positions", list(range(len(records))))[index]
                protected_payload = raw_rows[row_index]
                facts = record["detail"]
                if record["context_type"] != resource_kind or facts["clinic_id"] != profile.clinic_id or facts["org_id"] != profile.org_id:
                    raise PermissionError("Source record scope mismatch")
                key = delivery_identity(facts["id"], facts["source_version"])
                existing = db.get(IngestionLedgerModel, key)
                if existing:
                    if existing.payload_hash is not None and existing.payload_hash in {facts.get("semantic_digest"), facts["source_digest"]}:
                        counts["duplicate"] += 1
                        observe(key, facts)
                    else:
                        quarantine(row_index, "SAME_VERSION_PAYLOAD_COLLISION", protected_payload)
                        counts["quarantined"] += 1
                    continue
                legacy_facilities = [profile.clinic_id, *db.scalars(select(FacilityCrosswalk.legacy_clinic_id).where(
                    FacilityCrosswalk.clinic_id == profile.clinic_id, FacilityCrosswalk.status == "RESOLVED")).all()]
                legacy = db.scalars(select(IngestionLedgerModel).where(
                    IngestionLedgerModel.clinic_id.in_(legacy_facilities),
                    IngestionLedgerModel.connector_instance_id == profile.connector_id,
                    IngestionLedgerModel.resource_kind == resource_kind,
                    IngestionLedgerModel.external_record_id == facts["raw_id"],
                    IngestionLedgerModel.source_version.in_([facts["source_version"], "vid:" + facts["source_version"]]),
                ).limit(1)).first()
                if legacy:
                    # Do not mint new keys/money over old, incompletely scoped
                    # evidence. A reviewed legacy crosswalk is required first.
                    quarantine(row_index, "LEGACY_IDENTITY_RECONCILIATION_REQUIRED", protected_payload)
                    counts["quarantined"] += 1
                    continue
                if not db.get(SourceIdentityCrosswalk, facts["id"]):
                    db.add(SourceIdentityCrosswalk(entity_key=facts["id"], org_id=profile.org_id,
                                                   clinic_id=profile.clinic_id, connector_id=profile.connector_id,
                                                   resource_kind=resource_kind, raw_id=facts["raw_id"], parent_raw_id=facts.get("claim_id")))
                evidence_id = "hsp01-evidence:" + key
                effective = facts.get("effective_time")
                effective = datetime.fromisoformat(effective).astimezone(timezone.utc).replace(tzinfo=None) if effective else None
                db.add(EvidenceModel(evidence_id=evidence_id, canonical_entity=resource_kind,
                                     fact_key=key, fact_value_str=json.dumps(facts, sort_keys=True),
                                     source_connector=profile.connector_id, retrieval_timestamp=now.replace(tzinfo=None),
                                     effective_time=effective,
                                     evidence_type="HospitalRead", tombstone=facts.get("deleted", False),
                                     clinic_id=profile.clinic_id, external_record_id=facts["id"], resource_kind=resource_kind,
                                     source_version=facts["source_version"], metadata_json=json.dumps({
                                         "org_id": profile.org_id, "mapping_id": profile.mapping_id,
                                         "mapping_version": profile.mapping_version, "profile_revision": profile.revision,
                                         "extraction_binding": profile.cursor_binding(resource_kind),
                                         "quality_state": facts["quality_state"], "raw_id": facts["raw_id"]})))
                db.flush()
                db.add(IngestionLedgerModel(id=key, clinic_id=profile.clinic_id,
                                            connector_instance_id=profile.connector_id, resource_kind=resource_kind,
                                            external_record_id=facts["id"], source_version=facts["source_version"],
                                            evidence_id=evidence_id, payload_hash=facts.get("semantic_digest", facts["source_digest"]), effective_at=effective, status="COMMITTED"))
                db.flush()
                observe(key, facts)
                delivery = hashlib.sha256(json.dumps([key, "hospital-financial", "hospital-financial-v1"]).encode()).hexdigest()
                db.add(OperationalEventModel(id="hsp01-event:" + delivery, delivery_key=delivery,
                                             ledger_id=key, workflow_key="hospital-financial", consumer_version="hospital-financial-v1",
                                             event_type="Billing", source=profile.connector_id, raw_payload={"evidence_id": evidence_id},
                                             state="Queued", org_id=profile.org_id, clinic_id=profile.clinic_id))
                counts["accepted"] += 1
            # Authority can be revoked while a page is being fetched/normalized.
            self._hospital_authority(profile, resource_kind, db=db)
            cursor = db.get(HospitalCheckpoint, binding_key)
            if not cursor:
                cursor = HospitalCheckpoint(binding=binding_key, connector_id=profile.connector_id,
                                            resource_kind=resource_kind, clinic_id=profile.clinic_id)
                db.add(cursor)
            cursor.checkpoint_json = json.dumps(checkpoint)
            cursor.updated_at = now
            db.commit()
        return counts
