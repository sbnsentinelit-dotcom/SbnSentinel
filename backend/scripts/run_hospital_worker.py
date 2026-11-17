"""Bounded HSP01 worker command. Never defaults to a development database.

Set HSP01_WORKER_DATABASE_URL explicitly after the coordinated migration.
python -B scripts/run_hospital_worker.py --limit 100
"""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("limit must be between 1 and 10000")
    target = os.getenv("HSP01_WORKER_DATABASE_URL", "")
    if not target.startswith(("postgresql://", "postgresql+psycopg2://")):
        parser.error("Set the approved PostgreSQL HSP01_WORKER_DATABASE_URL explicitly")
    os.environ["DATABASE_URL"] = target
    os.environ["SENTINEL_DISABLE_SCHEMA_AUTOCREATE"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/runtime"))
    from app.core.config import settings
    if settings.DEPLOYMENT_REGION != "UAE":
        parser.error("An approved UAE deployment profile is required")
    from app.db.database import SessionLocal
    from app.models import event, signal, intelligence, evidence, organization, clinic, user, audit
    from app.models import financial_reporting, hospital_dimensions, hospital_membership, hospital_processing
    from app.models.connector import ConnectorModel
    from app.integrations.hospital_read.manifest import HospitalReadProfile
    from app.services.hospital_source_service import assert_profile_ready
    from app.services.hospital_worker import HospitalWorker
    import uuid
    def authorize(connector_id, clinic_id, resource_kind=None, revision=None):
        with SessionLocal() as db:
            connector = db.get(ConnectorModel, connector_id)
            if not connector or connector.clinic_id != clinic_id:
                raise PermissionError("Source unavailable")
            profile = HospitalReadProfile(**(connector.config or {})["hospital_profile"])
            if revision is not None and revision != profile.revision:
                raise PermissionError("Extraction profile changed; governed reprocessing required")
            for kind in [resource_kind] if resource_kind else profile.endpoints:
                assert_profile_ready(db, profile, kind)
    worker = HospitalWorker(SessionLocal, authorize, worker_id="hsp01-" + uuid.uuid4().hex)
    counts = {}
    for _ in range(args.limit):
        result = worker.run_once()
        counts[result] = counts.get(result, 0) + 1
        if result == "IDLE":
            break
    print(counts)


if __name__ == "__main__":
    main()
