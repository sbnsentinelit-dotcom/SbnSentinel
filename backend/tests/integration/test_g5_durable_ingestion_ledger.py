import pytest
from app.db.database import SessionLocal
from app.models.evidence import EvidenceModel, IngestionLedgerModel
from app.services.cursor_store import CursorModel, cursor_store
from app.integrations.core.source_authority import (
    connector_store,
    ConnectorBinding,
)
from app.services.ingress_service import canonical_ingress


@pytest.fixture(autouse=True)
def clean_db():
    connector_store.clear()
    with SessionLocal() as db:
        db.query(CursorModel).delete()
        db.query(IngestionLedgerModel).delete()
        db.query(EvidenceModel).delete()
        db.commit()
    yield
    connector_store.clear()
    with SessionLocal() as db:
        db.query(CursorModel).delete()
        db.query(IngestionLedgerModel).delete()
        db.query(EvidenceModel).delete()
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Event Case: Duplicate / Replayed Message
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_replayed_message_idempotent_noop():
    """Duplicate/replayed message produces idempotent no-op and retains original evidence ID."""
    cid = "conn_sync_01"
    clinic = "clinic_alpha"
    connector_store.register(ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Patient"},
    ))

    record = {
        "context_type": "Patient",
        "detail": {
            "id": "pat_100",
            "name": [{"family": "Smith"}],
            "birthDate": "1980-01-01",
            "meta": {"versionId": "v1", "lastUpdated": "2026-11-01T10:00:00Z"},
        }
    }

    # Initial ingestion
    res1 = await canonical_ingress.submit_batch(cid, [record], clinic_id=clinic)
    assert res1["processed"] == 1
    assert res1["duplicates_skipped"] == 0

    with SessionLocal() as db:
        ev1 = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "pat_100").first()
        orig_evidence_id = ev1.evidence_id
        ledger1 = db.query(IngestionLedgerModel).filter(IngestionLedgerModel.external_record_id == "pat_100").first()
        assert ledger1 is not None

    # Replay same message
    res2 = await canonical_ingress.submit_batch(cid, [record], clinic_id=clinic)
    assert res2["processed"] == 0
    assert res2["duplicates_skipped"] == 1

    # Verify original evidence record is unchanged
    with SessionLocal() as db:
        records = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "pat_100").all()
        assert len(records) == 1
        assert records[0].evidence_id == orig_evidence_id


# ─────────────────────────────────────────────────────────────────────────────
# 2. Event Case: Out-of-Order Correction & Version Preservation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_out_of_order_correction_preserves_history():
    """Corrections create versioned records without overwriting historical rows."""
    cid = "conn_sync_02"
    clinic = "clinic_alpha"
    connector_store.register(ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Encounter"},
    ))

    # Version 1: Initial Encounter
    rec_v1 = {
        "context_type": "Encounter",
        "detail": {
            "id": "enc_200",
            "status": "planned",
            "period": {"start": "2026-11-02T10:00:00Z"},
            "meta": {"versionId": "1"},
        }
    }
    await canonical_ingress.submit_batch(cid, [rec_v1], clinic_id=clinic)

    # Version 2: Corrected Encounter
    rec_v2 = {
        "context_type": "Encounter",
        "detail": {
            "id": "enc_200",
            "status": "arrived",
            "period": {"start": "2026-11-02T10:00:00Z"},
            "meta": {"versionId": "2"},
        }
    }
    await canonical_ingress.submit_batch(cid, [rec_v2], clinic_id=clinic)

    with SessionLocal() as db:
        history = db.query(EvidenceModel).filter(
            EvidenceModel.external_record_id == "enc_200"
        ).order_by(EvidenceModel.version.asc()).all()

        assert len(history) == 2
        assert history[0].version == 1
        assert history[1].version == 2
        assert history[1].previous_version_id == history[0].evidence_id
        # Historical truth not overwritten
        assert "planned" in history[0].fact_value_str
        assert "arrived" in history[1].fact_value_str


# ─────────────────────────────────────────────────────────────────────────────
# 3. Event Case: Deletion / Revocation / Tombstone
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deletion_creates_tombstone_and_restricts_use():
    """Tombstoned record retains audit trail but restricts downstream decision emission."""
    cid = "conn_sync_03"
    clinic = "clinic_alpha"
    connector_store.register(ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    ))

    rec_del = {
        "context_type": "Claim",
        "detail": {
            "id": "claim_300",
            "status": "entered-in-error",  # FHIR deletion indicator
            "meta": {"versionId": "del_1"},
        }
    }
    res = await canonical_ingress.submit_batch(cid, [rec_del], clinic_id=clinic)
    assert res["tombstoned"] == 1

    with SessionLocal() as db:
        ev = db.query(EvidenceModel).filter(EvidenceModel.external_record_id == "claim_300").first()
        assert ev is not None
        assert ev.tombstone is True
        ledger = db.query(IngestionLedgerModel).filter(IngestionLedgerModel.external_record_id == "claim_300").first()
        assert ledger.status == "TOMBSTONED"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Checkpoints & Opaque Token Safety
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opaque_cursor_preserved_without_lexical_sorting():
    """Opaque cursor tokens are preserved without lexical sorting."""
    cid = "conn_rest_billing"
    clinic = "clinic_beta"
    connector_store.register(ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Claim"},
    ))

    # Commit opaque pagination token (e.g. hash/base64 token)
    token_1 = "opaque_page_token_Z"
    cursor_store.commit(cid, "Claim", token_1, clinic_id=clinic, is_opaque=True)
    assert cursor_store.get(cid, "Claim", clinic_id=clinic) == token_1

    # Second token begins with 'A' (which would lexically sort lower than 'Z')
    token_2 = "opaque_page_token_A"
    cursor_store.commit(cid, "Claim", token_2, clinic_id=clinic, is_opaque=True)
    # Must preserve token_2 regardless of lexical order
    assert cursor_store.get(cid, "Claim", clinic_id=clinic) == token_2


# ─────────────────────────────────────────────────────────────────────────────
# 5. Crash Injection & Consistent Commit Recovery
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crash_recovery_produces_no_duplicate_facts():
    """Crash injection between data commit and restart recovery produces no duplicate facts."""
    cid = "conn_crash_01"
    clinic = "clinic_gamma"
    connector_store.register(ConnectorBinding(
        connector_instance_id=cid,
        vendor_id="synthetic_rest",
        allowed_clinic_id=clinic,
        state="AUTHORIZED_READY",
        permitted_resources={"Patient"},
    ))

    initial_checkpoint = "2026-11-01T00:00:00+00:00"
    cursor_store.commit(cid, "Patient", initial_checkpoint, clinic_id=clinic)

    batch_1 = [
        {"context_type": "Patient", "detail": {"id": "p_crash_1", "meta": {"lastUpdated": "2026-11-01T01:00:00Z"}}},
    ]

    # Ingest batch without advancing cursor (simulating crash before cursor commit)
    await canonical_ingress.submit_batch(cid, batch_1, clinic_id=clinic)

    # Cursor is still at initial_checkpoint
    assert cursor_store.get(cid, "Patient", clinic_id=clinic) == initial_checkpoint

    # Recovery: Re-fetch from initial_checkpoint replays batch_1 and adds batch_2
    batch_replay_and_next = [
        {"context_type": "Patient", "detail": {"id": "p_crash_1", "meta": {"lastUpdated": "2026-11-01T01:00:00Z"}}},
        {"context_type": "Patient", "detail": {"id": "p_crash_2", "meta": {"lastUpdated": "2026-11-01T02:00:00Z"}}},
    ]
    next_checkpoint = "2026-11-01T02:00:00+00:00"

    res_recovery = await canonical_ingress.submit_batch(
        cid, batch_replay_and_next, clinic_id=clinic, checkpoint=next_checkpoint
    )
    assert res_recovery["processed"] == 1  # Only p_crash_2 processed
    assert res_recovery["duplicates_skipped"] == 1  # p_crash_1 skipped

    # Cursor successfully advanced to next_checkpoint
    assert cursor_store.get(cid, "Patient", clinic_id=clinic) == next_checkpoint

    # Verify exact row counts in DB (no duplicates, no lost facts)
    with SessionLocal() as db:
        facts = db.query(EvidenceModel).filter(EvidenceModel.source_connector == cid).all()
        assert len(facts) == 2
        ext_ids = {f.external_record_id for f in facts}
        assert ext_ids == {"p_crash_1", "p_crash_2"}
