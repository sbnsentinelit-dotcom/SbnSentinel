"""
Records the waiting-room case in a clinic's queue data (as the queue feed would deliver it) and applies
corrections, so the Patient Flow screen can be checked against the auditor's example. The screen
calculates everything from the stored events; this command only delivers or corrects source events.

Usage (from the backend folder, with the backend's settings):
  python scripts/record_waiting_room_case.py                    # cohort arrived 45 minutes ago
  python scripts/record_waiting_room_case.py --arrival 10:00    # cohort arrived at 10:00 UAE today
  python scripts/record_waiting_room_case.py --remove-nursing   # correction: nursing record no longer counts
  python scripts/record_waiting_room_case.py --restore-nursing  # undo that correction
  python scripts/record_waiting_room_case.py --redeliver        # the feed sends one event again (stored once)
  python scripts/record_waiting_room_case.py --patient-left     # the waiting patient leaves before being seen
Then open Patient Flow -> Live Flow Queue -> Filter -> Day Clinic.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "runtime"))

UAE_OFFSET = timedelta(hours=4)
CLINIC_ID = "DXB-CLINIC-01"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clinic", default=CLINIC_ID)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--arrival", help="cohort arrival, local UAE time today (HH:MM)")
    group.add_argument("--remove-nursing", action="store_true")
    group.add_argument("--restore-nursing", action="store_true")
    group.add_argument("--redeliver", action="store_true")
    group.add_argument("--patient-left", action="store_true")
    args = parser.parse_args(argv)

    from app.db.database import SessionLocal
    from app.models.patient_flow import NURSING_DONE
    from app.services import waiting_room_case as case

    db = SessionLocal()
    try:
        now = datetime.utcnow().replace(second=0, microsecond=0)
        if args.remove_nursing or args.restore_nursing or args.redeliver or args.patient_left:
            ids = case.latest_case(db, args.clinic)
            if not ids:
                print("No waiting-room case recorded yet. Run the command without options first.")
                return 1
            waiting = ids[-1]
            if args.remove_nursing:
                n = case.supersede(db, waiting, NURSING_DONE, "data correction (nursing record withdrawn)", now)
                print(f"{waiting}: nursing record superseded ({n} event). Readiness should show 'Readiness unconfirmed'.")
            elif args.restore_nursing:
                n = case.restore(db, waiting, NURSING_DONE)
                print(f"{waiting}: nursing record counts again ({n} event).")
            elif args.redeliver:
                stored = case.redeliver(db, waiting, NURSING_DONE)
                print(f"{waiting}: nursing event delivered again -> " +
                      ("stored twice (unexpected)" if stored else "already stored; counted once"))
            else:
                ok = case.patient_left(db, args.clinic, waiting, now)
                outcome = "left before being seen (recorded by reception)." if ok else "already recorded as left."
                print(f"{waiting}: {outcome}")
            return 0
        if args.arrival:
            hh, mm = (int(x) for x in args.arrival.split(":"))
            arrival = (now + UAE_OFFSET).replace(hour=hh, minute=mm) - UAE_OFFSET
        else:
            arrival = now - timedelta(minutes=45)
        ids = case.record_case(db, args.clinic, arrival)
        local = arrival + UAE_OFFSET
        print(f"Recorded {len(ids)} visits in department '{case.CASE_DEPARTMENT}' arriving at {local:%H:%M} UAE "
              f"({ids[0]} .. {ids[-1]}).")
        print(f"At {(local + timedelta(minutes=45)):%H:%M} UAE: 9 consultations started, average wait 10 minutes, "
              "1 still waiting 45 minutes since arrival and 30 minutes since ready.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
