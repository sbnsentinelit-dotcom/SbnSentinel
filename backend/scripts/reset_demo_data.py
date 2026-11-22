"""
Reset the synthetic demo data of a SBN Sentinel demo environment.

Deletes every table except user accounts, recreates them, and runs the normal startup
seeding again (governance seeds, demo accounts if missing, Dubai demo dataset).

Safety:
- Refuses to run when ENVIRONMENT is PRODUCTION.
- Refuses to run unless SYNTHETIC_TEST_ENABLED is true (synthetic demo environments only).
- Does nothing without --yes.
- Stop the backend before running it and start it again afterwards: the running backend
  keeps governance data in memory.

Usage (from the backend folder, with the same environment settings as the backend):
    python scripts/reset_demo_data.py --yes
"""
import argparse
import os
import sys

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "runtime"))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from app.core.config import settings  # noqa: E402

# User accounts (and their passwords) survive a reset.
KEEP_TABLES = {"users"}


def _masked(url: str) -> str:
    if "://" in url and "@" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.rsplit('@', 1)[1]}"
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset synthetic demo data (demo environments only).")
    parser.add_argument("--yes", action="store_true", help="confirm the reset")
    args = parser.parse_args()

    if str(getattr(settings, "ENVIRONMENT", "")).upper() == "PRODUCTION":
        print("REFUSED: ENVIRONMENT is PRODUCTION. Demo data is never reset on a production database.")
        return 2
    if not getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
        print("REFUSED: SYNTHETIC_TEST_ENABLED is not true. This command is only for synthetic demo environments.")
        return 2

    from app.db.database import Base, engine, SQLALCHEMY_DATABASE_URL

    print(f"Database: {_masked(SQLALCHEMY_DATABASE_URL)}")
    if not args.yes:
        print("Nothing changed. Run again with --yes to delete all demo data (user accounts are kept).")
        return 1

    tables = [t for t in Base.metadata.sorted_tables if t.name not in KEEP_TABLES]
    Base.metadata.drop_all(bind=engine, tables=tables)
    Base.metadata.create_all(bind=engine)
    print(f"Cleared {len(tables)} tables (kept: {', '.join(sorted(KEEP_TABLES))}).")

    # The same seeding the backend runs at startup, against the empty tables.
    from app.main import run_startup_seeding
    run_startup_seeding()

    from app.db.database import SessionLocal
    from app.models.encounter import EncounterModel
    from app.models.signal import SignalModel

    db = SessionLocal()
    try:
        print(f"Seeded: {db.query(EncounterModel).count()} encounters, {db.query(SignalModel).count()} signals.")
    finally:
        db.close()
    print("Done. Start the backend again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
