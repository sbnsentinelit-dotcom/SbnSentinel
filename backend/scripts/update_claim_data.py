"""
Applies a source-system update to one claim's data (as a payer / clearinghouse feed would): a new
payer filing deadline, or a change in whether its recovery-route times are verified bounds or estimates.
Each update is kept on the claim (risk_inputs.source_updates) with its time, so the change is traceable.
The dashboard re-evaluates the claim from its data on the next read.

Usage (from the backend folder, with the backend's settings):
  python scripts/update_claim_data.py --claim CLM2026-DXB-0006 --deadline 17:00        # local UAE time today
  python scripts/update_claim_data.py --claim CLM2026-DXB-0006 --deadline-in 19        # minutes from now
  python scripts/update_claim_data.py --claim CLM2026-DXB-0006 --routes estimate       # or: verified
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "runtime"))

UAE_OFFSET = timedelta(hours=4)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claim", required=True, help="claim ID, e.g. CLM2026-DXB-0006")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--deadline", help="new payer filing deadline, local UAE time today (HH:MM)")
    group.add_argument("--deadline-in", type=int, help="new payer filing deadline, minutes from now")
    group.add_argument("--routes", choices=("verified", "estimate"), help="recovery route times are verified or estimates")
    args = parser.parse_args(argv)

    from app.db.database import SessionLocal
    from app.models.signal import SignalModel
    from app.services.risk_level_evaluator import current_evaluation

    db = SessionLocal()
    try:
        signal = next((s for s in db.query(SignalModel).all()
                       if ((s.metadata_data or {}).get("risk_inputs") or {}).get("claim_id") == args.claim), None)
        if signal is None:
            print(f"Claim {args.claim} not found.")
            return 1
        meta = dict(signal.metadata_data or {})
        inputs = dict(meta.get("risk_inputs") or {})
        now = datetime.utcnow().replace(second=0, microsecond=0)
        if args.deadline or args.deadline_in is not None:
            if args.deadline:
                hh, mm = (int(x) for x in args.deadline.split(":"))
                local = (now + UAE_OFFSET).replace(hour=hh, minute=mm)
                new_deadline = local - UAE_OFFSET
            else:
                new_deadline = now + timedelta(minutes=args.deadline_in)
            change = {"field": "resubmission_deadline", "from": inputs.get("resubmission_deadline"),
                      "to": new_deadline.isoformat() + "Z"}
            inputs["resubmission_deadline"] = change["to"]
        else:
            routes = [dict(r) for r in (inputs.get("recovery_routes") or [])]
            if not routes:
                print(f"Claim {args.claim} has no recovery routes in its data.")
                return 1
            for r in routes:
                r["verified"] = args.routes == "verified"
            change = {"field": "recovery_routes.verified", "to": args.routes == "verified"}
            inputs["recovery_routes"] = routes
        change["at"] = now.isoformat() + "Z"
        inputs["source_updates"] = list(inputs.get("source_updates") or []) + [change]
        meta["risk_inputs"] = inputs
        signal.metadata_data = meta
        db.commit()
        ev = current_evaluation(meta) or {}
        print(f"Updated {args.claim}: {change['field']} -> {change['to']}")
        print(f"Now: {ev.get('label') or ev.get('level')} | {ev.get('reason')}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
