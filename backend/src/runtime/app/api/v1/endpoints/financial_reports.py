from fastapi import APIRouter, Depends, Query, Response
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.financial_reporting_service import financial_reporting_service

router = APIRouter()


def _default_dates():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2027, 12, 31, tzinfo=timezone.utc)
    return start, end


@router.get("/daily-receipts")
def get_daily_receipts(
    days: int = Query(7, ge=1, le=31),
    currency: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Payments received per day for the user's clinic over the last `days` days (today included).

    Feeds the dashboard revenue trend. Only the caller's own clinic is used; System
    Administrators without a clinic get every clinic.
    """
    from datetime import timedelta
    from fastapi import HTTPException
    from sqlalchemy import func
    from app.models.financial_reporting import FinancialPaymentReceiptModel
    from app.models.user import UserRole
    from app.core.config import get_deployment_currency

    eff_currency = currency or get_deployment_currency()

    clinic_id = getattr(current_user, "clinic_id", None)
    if not clinic_id and current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        raise HTTPException(status_code=403, detail="Missing required clinic scope")

    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=days - 1)
    day_col = func.date(FinancialPaymentReceiptModel.received_date)
    q = db.query(day_col, func.sum(FinancialPaymentReceiptModel.amount)).filter(
        FinancialPaymentReceiptModel.currency == eff_currency,
        FinancialPaymentReceiptModel.received_date >= datetime.combine(first_day, datetime.min.time()),
        FinancialPaymentReceiptModel.received_date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
    )
    if clinic_id:
        q = q.filter(FinancialPaymentReceiptModel.clinic_id == clinic_id)
    totals = {str(day): float(amount or 0.0) for day, amount in q.group_by(day_col).all()}
    series = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        series.append({"date": day.isoformat(), "amount": round(totals.get(day.isoformat(), 0.0), 2)})
    return {"currency": eff_currency, "clinic_id": clinic_id, "days": series}


@router.get("/summary")
def get_owner_summary(
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    currency: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    from app.core.config import get_deployment_currency
    curr = currency or get_deployment_currency()

    return financial_reporting_service.generate_owner_summary(
        clinic_id=target_clinic,
        period_start=s_date,
        period_end=e_date,
        currency=curr,
        db=db,
    )


@router.get("/payer-summary")
def get_payer_summary(
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    currency: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    return financial_reporting_service.generate_payer_plan_summary(
        clinic_id=target_clinic,
        period_start=s_date,
        period_end=e_date,
        currency=currency,
        db=db,
    )


@router.get("/claim-register")
def get_claim_register(
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    currency: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    return financial_reporting_service.generate_claim_register(
        clinic_id=target_clinic,
        period_start=s_date,
        period_end=e_date,
        currency=currency,
        db=db,
    )


@router.get("/unpaid-lines")
def get_unpaid_lines(
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    currency: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    return financial_reporting_service.generate_unpaid_procedure_lines(
        clinic_id=target_clinic,
        period_start=s_date,
        period_end=e_date,
        currency=currency,
        db=db,
    )


@router.get("/denials")
def get_denials(
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    currency: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    return financial_reporting_service.generate_denials_and_rejections(
        clinic_id=target_clinic,
        period_start=s_date,
        period_end=e_date,
        currency=currency,
        db=db,
    )


@router.get("/reconciliation")
def get_reconciliation(
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    return financial_reporting_service.generate_reconciliation_report(
        clinic_id=target_clinic,
        period_start=s_date,
        period_end=e_date,
        db=db,
    )


@router.get("/ageing")
def get_ageing(
    clinic_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    return financial_reporting_service.generate_ageing_report(
        clinic_id=target_clinic,
        db=db,
    )


@router.get("/completeness")
def get_completeness(
    clinic_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    return financial_reporting_service.generate_completeness_exceptions(
        clinic_id=target_clinic,
        db=db,
    )


@router.post("/snapshot")
def create_report_snapshot(
    report_code: str = Query(..., pattern="^FR0[1-8]$"),
    clinic_id: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_clinic = clinic_id or getattr(current_user, "clinic_id", None) or "DXB-CLINIC-01"
    s_date, e_date = start_date, end_date
    if not s_date or not e_date:
        def_s, def_e = _default_dates()
        s_date = s_date or def_s
        e_date = e_date or def_e

    snapshot = financial_reporting_service.create_snapshot(
        clinic_id=target_clinic,
        report_code=report_code,
        period_start=s_date,
        period_end=e_date,
        db=db,
    )
    return {
        "status": "Success",
        "snapshot_id": snapshot.snapshot_id,
        "run_key": snapshot.run_key,
        "revision": snapshot.revision,
        "completeness_status": snapshot.completeness_status,
    }


@router.get("/export/{snapshot_id}/csv")
def download_snapshot_csv(
    snapshot_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    csv_data = financial_reporting_service.export_csv(snapshot_id=snapshot_id, db=db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="report_{snapshot_id}.csv"'}
    )
