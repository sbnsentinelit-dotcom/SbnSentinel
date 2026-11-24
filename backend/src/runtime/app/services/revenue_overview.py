"""
Revenue Intelligence figures for one reporting period, calculated from the clinic's encounters.

Every number on the Revenue Intelligence screen comes from here: the four cards, their trends and
sparklines, the daily revenue chart and the claim-status ring. Nothing is estimated or fixed: a value
is a sum or a count over the encounters whose service date falls in the period.

Charges, pending and denied claims and the ring count the claims whose service date falls in the
period; the paid card counts the payments received in the period (payment receipts), because a payer
pays days or weeks after the visit.

Periods end today (clinic local time) and always have their full length, so the chart shows every day:
  today -> today;  week -> the last 7 days;  month -> the last 30 days.
A trend compares the period with the one just before it (yesterday; the 7 days before; the 30 days before).
The chart has one point per day; for Today it has one point per clinic hour so far, each visit counted in
the hour it checked out (or arrived, while still in the clinic) and each payment in the hour it arrived.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func

from app.models.encounter import EncounterModel

PERIODS = {"today": "Today", "week": "This Week", "month": "This Month"}

# Billing status groups. Every status the source sends is counted exactly once: in its group, or
# under its own name when it belongs to none of them (e.g. "Held - Auth Denied").
PAID_STATUSES = ("Paid", "Settled")
PENDING_STATUSES = ("Pending", "Billed", "Submitted")
DENIED_STATUSES = ("Claim Denied", "Denied")
STATUS_GROUPS = (("paid", "Paid", PAID_STATUSES), ("pending", "Pending", PENDING_STATUSES),
                 ("denied", "Denied", DENIED_STATUSES))


def clinic_offset() -> timedelta:
    from app.core.config import settings
    is_uae = getattr(settings, "DEPLOYMENT_REGION", "") == "UAE" or getattr(settings, "CLINIC_TIMEZONE", "") == "GST"
    return timedelta(hours=4) if is_uae else timedelta(0)


def clinic_today(now_utc: Optional[datetime] = None) -> date:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone(clinic_offset())).date()


def _receipts_by_day(query, start: date, end: date, currency: str) -> Dict[str, float]:
    """Payments received per local day for the clinics in the (already scoped) encounter query."""
    from app.models.financial_reporting import FinancialPaymentReceiptModel as R
    clinics = [c for (c,) in query.with_entities(EncounterModel.clinic_id).distinct().all() if c]
    if not clinics:
        return {}
    offset = clinic_offset()
    lo = datetime.combine(start, datetime.min.time()) - offset
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time()) - offset
    out: Dict[str, float] = {}
    for received, amount in db_session(query).query(R.received_date, R.amount).filter(
            R.clinic_id.in_(clinics), R.currency == currency, R.received_date >= lo, R.received_date < hi).all():
        key = (received + offset).date().isoformat()
        out[key] = out.get(key, 0.0) + float(amount or 0.0)
    return out


def db_session(query):
    return query.session


def normalize_period(period: Optional[str]) -> str:
    key = (period or "week").strip().lower().replace("this ", "").replace("this_", "")
    if key not in PERIODS:
        raise ValueError(f"Unknown period '{period}'. Use one of: {', '.join(PERIODS)}")
    return key


PERIOD_DAYS = {"today": 1, "week": 7, "month": 30}


def period_bounds(period: str, today: date) -> Tuple[date, date, date, date]:
    """(start, end, previous_start, previous_end), all inclusive."""
    n = PERIOD_DAYS[period]
    start = today - timedelta(days=n - 1)
    return start, today, start - timedelta(days=n), start - timedelta(days=1)


def _day_col():
    return func.substr(EncounterModel.date, 1, 10)


NOT_A_CLAIM = ("No Charge",)  # a visit that ended without a consultation is not billed


def in_dates(query, start: date, end: date):
    """Billing records with a service date in the period (visits with no charge are not claims)."""
    day = _day_col()
    return query.filter(day >= start.isoformat(), day <= end.isoformat(),
                        EncounterModel.billing_status.is_(None) | ~EncounterModel.billing_status.in_(NOT_A_CLAIM))


def status_filter(query, billing_status: Optional[str]):
    """'all' (or empty) keeps everything; a group key (paid / pending / denied) keeps its statuses;
    anything else is an exact billing status (e.g. 'Claim Denied', 'Held - Auth Denied')."""
    if not billing_status or billing_status == "all":
        return query
    for key, _label, statuses in STATUS_GROUPS:
        if billing_status == key:
            return query.filter(EncounterModel.billing_status.in_(statuses))
    return query.filter(EncounterModel.billing_status == billing_status)


def _days(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _rows_by_day(query) -> List[Tuple[str, Optional[str], int, float]]:
    day = _day_col()
    return query.with_entities(
        day, EncounterModel.billing_status, func.count(EncounterModel.id),
        func.coalesce(func.sum(EncounterModel.billing_amount), 0.0),
    ).group_by(day, EncounterModel.billing_status).all()


def _totals(rows: Iterable[Tuple[str, Optional[str], int, float]], received: float = 0.0) -> Dict[str, float]:
    t = {"charges": 0.0, "paid_amount": received, "pending": 0, "denied": 0, "claims": 0}
    for _day, st, count, amount in rows:
        st = st or "Pending"
        t["claims"] += count
        t["charges"] += float(amount or 0.0)
        if st in PENDING_STATUSES:
            t["pending"] += count
        elif st in DENIED_STATUSES:
            t["denied"] += count
    return t


def _trend(current: float, previous: float, higher_is_good: bool) -> Dict:
    if not previous:
        return {"trend": None, "previous": previous, "good": None}
    change = (current - previous) / previous * 100
    return {"trend": f"{'+' if change >= 0 else '-'}{abs(change):.1f}%", "previous": round(previous, 2),
            "good": (change >= 0) == higher_is_good if change else None}


def percentages(counts: List[int]) -> List[int]:
    """Whole percentages that add up to exactly 100 (largest remainder)."""
    total = sum(counts)
    if not total:
        return [0 for _ in counts]
    raw = [c * 100 / total for c in counts]
    out = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[:100 - sum(out)]:
        out[i] += 1
    return out


def status_breakdown(rows) -> List[Dict]:
    counts: Dict[str, int] = {}
    order: List[Tuple[str, str]] = []
    for _day, st, count, _amount in rows:
        st = st or "Pending"
        key, label = next(((k, lab) for k, lab, sts in STATUS_GROUPS if st in sts), (st, st))
        if key not in counts:
            counts[key] = 0
            order.append((key, label))
        counts[key] += count
    keys = [(k, lab) for k, lab, _s in STATUS_GROUPS] + sorted(
        (kl for kl in order if kl[0] not in {k for k, _l, _s in STATUS_GROUPS}), key=lambda kl: kl[1])
    pcts = percentages([counts.get(k, 0) for k, _l in keys])
    return [{"key": k, "label": lab, "count": counts.get(k, 0), "pct": p} for (k, lab), p in zip(keys, pcts)]


def _hourly_series(query, today: date, currency: str, now_utc: Optional[datetime]) -> List[Dict]:
    """Today's points by clinic hour (opening hour up to the current hour), from visit and payment times."""
    from app.models.financial_reporting import FinancialPaymentReceiptModel as R
    from app.models.patient_flow import ARRIVED, CHECKED_OUT, VisitEventModel
    from app.services.patient_flow_policy import DEFAULT_FLOW_POLICY as policy
    offset = clinic_offset()
    now_local = (now_utc or datetime.utcnow()) + offset
    last = policy.close_hour - 1 if now_local.date() > today else now_local.hour
    hours = list(range(policy.open_hour, max(policy.open_hour, min(last, policy.close_hour - 1)) + 1))

    def bucket(local_dt: Optional[datetime]) -> int:
        h = local_dt.hour if local_dt and local_dt.date() == today else hours[0]
        return min(max(h, hours[0]), hours[-1])

    encs = in_dates(query, today, today).with_entities(
        EncounterModel.id, EncounterModel.billing_status, EncounterModel.billing_amount).all()
    times: Dict[str, Dict[str, datetime]] = {}
    ids = [e[0] for e in encs]
    for i in range(0, len(ids), 500):
        for visit_id, kind, at in db_session(query).query(
                VisitEventModel.visit_id, VisitEventModel.event_type, VisitEventModel.event_time).filter(
                VisitEventModel.visit_id.in_(ids[i:i + 500]), VisitEventModel.event_type.in_([ARRIVED, CHECKED_OUT]),
                VisitEventModel.superseded_at.is_(None)).all():
            times.setdefault(visit_id, {})[kind] = at
    rows_by_hour: Dict[int, List] = {h: [] for h in hours}
    for enc_id, st, amount in encs:
        t = times.get(enc_id, {})
        at = t.get(CHECKED_OUT) or t.get(ARRIVED)
        rows_by_hour[bucket(at + offset if at else None)].append((None, st, 1, amount or 0.0))

    received: Dict[int, float] = {h: 0.0 for h in hours}
    clinics = [c for (c,) in query.with_entities(EncounterModel.clinic_id).distinct().all() if c]
    if clinics:
        lo = datetime.combine(today, datetime.min.time()) - offset
        for at, amount in db_session(query).query(R.received_date, R.amount).filter(
                R.clinic_id.in_(clinics), R.currency == currency, R.received_date >= lo,
                R.received_date < lo + timedelta(days=1)).all():
            received[bucket(at + offset)] += float(amount or 0.0)

    series = []
    for h in hours:
        t = _totals(rows_by_hour[h], received[h])
        series.append({"date": today.isoformat(), "hour": f"{h:02d}:00", "charges": round(t["charges"], 2),
                       "paid_amount": round(t["paid_amount"], 2), "pending": t["pending"],
                       "denied": t["denied"], "claims": t["claims"]})
    return series


def revenue_overview(query, period: str, today: date, currency: str, now_utc: Optional[datetime] = None) -> Dict:
    """`query` is the caller's scoped encounter query (clinic / organisation access already applied)."""
    start, end, prev_start, prev_end = period_bounds(period, today)
    rows = _rows_by_day(in_dates(query, start, end))
    receipts = _receipts_by_day(query, start, end, currency)
    prev = _totals(_rows_by_day(in_dates(query, prev_start, prev_end)),
                   sum(_receipts_by_day(query, prev_start, prev_end, currency).values()))
    now = _totals(rows, sum(receipts.values()))

    per_day: Dict[str, List] = {}
    for row in rows:
        per_day.setdefault(row[0], []).append(row)
    series = []
    if period == "today":
        series = _hourly_series(query, today, currency, now_utc)
    else:
        for d in _days(start, end):
            t = _totals(per_day.get(d.isoformat(), []), receipts.get(d.isoformat(), 0.0))
            series.append({"date": d.isoformat(), "charges": round(t["charges"], 2),
                           "paid_amount": round(t["paid_amount"], 2), "pending": t["pending"],
                           "denied": t["denied"], "claims": t["claims"]})

    def card(key: str, value, higher_is_good: bool, money: bool):
        return {"value": round(value, 2) if money else value, **_trend(value, prev[key], higher_is_good),
                "spark": [p[key] for p in series]}

    return {
        "period": period,
        "period_label": PERIODS[period],
        "start": start.isoformat(), "end": end.isoformat(),
        "compare_start": prev_start.isoformat(), "compare_end": prev_end.isoformat(),
        "currency": currency,
        "cards": {
            "charges": card("charges", now["charges"], True, True),
            "paid_amount": card("paid_amount", now["paid_amount"], True, True),
            "pending": card("pending", now["pending"], False, False),
            "denied": card("denied", now["denied"], False, False),
        },
        "total_claims": now["claims"],
        "status_breakdown": status_breakdown(rows),
        "series": series,
    }


EXPORT_COLUMNS = ("Encounter ID", "Service Date", "Patient Name", "Patient ID", "Procedure Code (CPT)",
                  "Payer Network", "Amount", "Currency", "Billing Status", "Denial Reason")


def export_rows(query, currency: str) -> Iterable[List]:
    for e in query.order_by(_day_col(), EncounterModel.id).yield_per(500):
        yield [e.id, (e.date or "")[:10], e.patient_name or "", e.patient_ref or "",
               e.cpt_code or "", e.payer_network or "",
               "" if e.billing_amount is None else f"{e.billing_amount:.2f}", currency,
               e.billing_status or "Pending", e.denial_reason or ""]
