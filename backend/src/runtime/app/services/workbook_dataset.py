"""
The clinic's UAE billing workbook (client_excel.json, exported from the uploaded XLSX).

One loader for every reader: patients, encounters, charges and claims keep their own identifiers
(Patient_ID, MRN, Encounter_ID, Claim_ID), and Excel serial dates (e.g. "46037") are converted with
the workbook epoch (1899-12-30). Nothing is guessed: a missing value stays None.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

_EXCEL_EPOCH = date(1899, 12, 30)


def _candidate_paths() -> List[Path]:
    here = Path(__file__).resolve()
    return [
        here.parents[4] / "client_excel.json",   # backend/client_excel.json in the repository
        Path("/app/client_excel.json"),          # Docker image
        Path(os.getcwd()) / "client_excel.json",
    ]


def excel_date(value: Any) -> Optional[date]:
    """Excel serial number or ISO date string -> date. None when missing or unreadable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _EXCEL_EPOCH + timedelta(days=int(text))
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def to_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_workbook() -> Dict[str, List[Dict[str, Any]]]:
    for path in _candidate_paths():
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    return {}


def patients() -> List[Dict[str, Any]]:
    """Workbook patients with their identifiers and contact/insurance facts."""
    rows = []
    for p in load_workbook().get("Patients", []):
        name = f"{p.get('First_Name', '')} {p.get('Last_Name', '')}".strip()
        rows.append({
            "patient_ref": p.get("Patient_ID"),
            "mrn": p.get("MRN"),
            "name": name,
            "mobile": p.get("Mobile") or None,
            "payer": p.get("Payer") or None,
            "plan": p.get("Plan") or None,
            "member_id": p.get("Member_ID") or None,
        })
    return rows


def patients_by_ref() -> Dict[str, Dict[str, Any]]:
    return {p["patient_ref"]: p for p in patients() if p.get("patient_ref")}


def providers() -> List[str]:
    """The clinic's providers as the workbook records them, most visits first."""
    counts: Dict[str, int] = {}
    for e in load_workbook().get("Encounters", []):
        prov = e.get("Provider")
        if prov:
            counts[prov] = counts.get(prov, 0) + 1
    return [p for p, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def provider_specialty() -> Dict[str, str]:
    """Each provider's most frequent specialty in the workbook."""
    counts: Dict[str, Dict[str, int]] = {}
    for e in load_workbook().get("Encounters", []):
        prov, sp = e.get("Provider"), e.get("Specialty")
        if prov and sp:
            counts.setdefault(prov, {})[sp] = counts.setdefault(prov, {}).get(sp, 0) + 1
    return {p: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for p, c in counts.items()}


def specialties() -> List[str]:
    seen: Dict[str, int] = {}
    for e in load_workbook().get("Encounters", []):
        sp = e.get("Specialty")
        if sp:
            seen[sp] = seen.get(sp, 0) + 1
    return [s for s, _ in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


_CLAIM_BILLING_STATUS = {
    "Paid": "Paid",
    "Denied": "Claim Denied",
    "Rejected": "Claim Denied",
    "Submitted": "Billed",
    "Accepted": "Billed",
}


def encounters() -> List[Dict[str, Any]]:
    """Workbook encounters joined to their patient (by Patient_ID), charge and claim (by Encounter_ID)."""
    wb = load_workbook()
    pats = patients_by_ref()
    charges = {c.get("Encounter_ID"): c for c in wb.get("Charges", [])}
    claims = {c.get("Encounter_ID"): c for c in wb.get("Claims", [])}
    rows = []
    for e in wb.get("Encounters", []):
        pref = e.get("Patient_ID")
        pat = pats.get(pref, {})
        chg = charges.get(e.get("Encounter_ID"), {})
        clm = claims.get(e.get("Encounter_ID"))
        visit_date = excel_date(e.get("Encounter_Date"))
        denial_code = (clm or {}).get("Denial_Code") or None
        denial_reason = (clm or {}).get("Denial_Reason") or None
        if clm:
            billing_status = _CLAIM_BILLING_STATUS.get(clm.get("Claim_Status"), clm.get("Claim_Status"))
        else:
            billing_status = chg.get("Charge_Status") or None
        rows.append({
            "encounter_id": e.get("Encounter_ID"),
            "patient_ref": pref,
            "mrn": pat.get("mrn"),
            "patient_name": pat.get("name") or pref,
            "provider": e.get("Provider"),
            "specialty": e.get("Specialty"),
            "visit_type": e.get("Visit_Type"),
            "authorization_required": {"Yes": True, "No": False}.get(e.get("Authorization_Required")),
            "date": visit_date.isoformat() if visit_date else None,
            "status": e.get("Encounter_Status"),
            "diagnosis_code": e.get("Diagnosis_Code"),
            "diagnosis_description": e.get("Diagnosis_Description"),
            "cpt_code": chg.get("CPT_Code") or None,
            "billed_amount": to_float(chg.get("Billed_Amount")),
            "payer": (clm or {}).get("Payer") or pat.get("payer"),
            "patient_responsibility": to_float((clm or {}).get("Patient_Responsibility")),
            "billing_status": billing_status,
            "denial_reason": f"{denial_code}: {denial_reason}" if denial_code and denial_reason else None,
        })
    return rows
