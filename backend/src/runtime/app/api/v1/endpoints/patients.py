import math
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Query, Response, Depends
from app.api.deps import get_scoped_user
from app.models.user import User

router = APIRouter()

_cached_patients = None

# Deterministic specifications matching the UAE Dubai Demo Dataset
EMIRATES = ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Fujairah", "Ras Al Khaimah", "Umm Al Quwain", "Al Ain"]
PAYERS = ["Daman", "Orient Insurance", "MetLife", "Bupa Arabia", "Self Pay", "Sukoon", "Cigna", "ADNIC", "AXA Gulf", "NMC Insurance"]
PLANS = ["Gold", "Silver", "Premium", "Standard", "Basic", "Self Pay"]
NATIONALITIES = ["UAE", "India", "Pakistan", "Egypt", "Jordan", "Philippines", "Bangladesh", "UK"]
FIRST_NAMES = ["Mariam", "Reem", "Aisha", "Mona", "Huda", "Khalid", "Tariq", "Mohammed", "Ahmed", "Rashid", "Layla", "Hassan", "Sara", "Ali", "Omar", "Amira", "Fatima", "Saeed", "Noor"]
LAST_NAMES = ["Ali", "Malik", "Verma", "Sharma", "George", "Nair", "Al-Mansoori", "Rahman", "Al-Hassan", "Ahmed", "Al-Nuaimi", "Patel", "Siddiqui", "Khan", "Hussain", "Thomas"]


FEMALE_NAMES = {
    "mariam", "reem", "aisha", "mona", "huda", "layla", "sara", "sarah",
    "fatima", "amira", "noor", "nour", "zainab", "salma", "yasmin", "dina",
    "farah", "samira", "lina", "rania", "hessa", "amna", "moza", "shamsa"
}

MALE_NAMES = {
    "khalid", "tariq", "mohammed", "ahmed", "rashid", "hassan", "ali",
    "omar", "saeed", "ibrahim", "abdullah", "zayed", "hamad", "sultan",
    "mansour", "salem", "faisal", "michael", "david", "james"
}


def _convert_excel_date(val: Any) -> Optional[str]:
    if val is None or val == "":
        return None
    val_str = str(val).strip()
    if val_str.isdigit():
        try:
            d = datetime(1899, 12, 30) + timedelta(days=int(val_str))
            return d.strftime("%Y-%m-%d")
        except Exception:
            return val_str
    return val_str


def _resolve_gender(first_name: Optional[str], raw_gender: Optional[str]) -> str:
    """
    Authoritative source gender preservation (B-76):
    1. If source raw_gender is provided and non-empty:
       - 'M' / 'MALE' -> 'M'
       - 'F' / 'FEMALE' -> 'F'
       - 'OTHER' / 'UNKNOWN' / 'U' -> 'Unknown'
       - other valid string -> preserve it.
    2. If source raw_gender is missing/null/empty:
       - Return 'Unknown'. Do NOT infer from first name.
    """
    if raw_gender is not None and str(raw_gender).strip():
        rg = str(raw_gender).strip().upper()
        if rg in ("M", "MALE"):
            return "M"
        if rg in ("F", "FEMALE"):
            return "F"
        if rg in ("U", "UNKNOWN", "OTHER"):
            return "Unknown"
        return rg

    return "Unknown"


def _get_all_raw_patients() -> List[Dict[str, Any]]:
    global _cached_patients
    if _cached_patients:
        return _cached_patients

    # Try to load exact records from client_excel.json
    for path in [
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'client_excel.json'),
        '/app/client_excel.json',
        'client_excel.json'
    ]:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    p_list = data.get('Patients', [])
                    if p_list:
                        formatted = []
                        for p in p_list:
                            formatted.append({
                                "patient_id": p.get("Patient_ID"),
                                "mrn": p.get("MRN"),
                                "first_name": p.get("First_Name"),
                                "last_name": p.get("Last_Name"),
                                "name": f"{p.get('First_Name')} {p.get('Last_Name')}",
                                "dob": _convert_excel_date(p.get("DOB")),
                                "gender": _resolve_gender(p.get("First_Name"), p.get("Gender")),
                                "nationality": p.get("Nationality"),
                                "emirate": p.get("Emirate"),
                                "mobile": p.get("Mobile"),
                                "email": p.get("Email"),
                                "payer": p.get("Payer"),
                                "plan": p.get("Plan"),
                                "member_id": p.get("Member_ID"),
                                "policy_start": _convert_excel_date(p.get("Policy_Start")),
                                "policy_end": _convert_excel_date(p.get("Policy_End")),
                                "registration_date": _convert_excel_date(p.get("Registration_Date")),
                                "status": p.get("Status", "Active")
                            })
                        _cached_patients = formatted
                        return formatted
            except Exception:
                pass

    patients = []
    base_dob = datetime(1960, 1, 1)

    for i in range(1, 501):
        f = FIRST_NAMES[(i * 7) % len(FIRST_NAMES)]
        last_n = LAST_NAMES[(i * 11) % len(LAST_NAMES)]
        name = f"{f} {last_n}"
        em = EMIRATES[(i * 3) % len(EMIRATES)]
        payer = PAYERS[(i * 5) % len(PAYERS)]
        plan = PLANS[(i * 2) % len(PLANS)] if payer != "Self Pay" else "Self Pay"
        nat = NATIONALITIES[(i * 13) % len(NATIONALITIES)]
        pat_id = f"PAT2026{i:04d}"
        mrn = f"MRN-{i:06d}"
        mem_id = f"TEST{10000000 + i * 173 % 89999999}"
        dob = (base_dob + timedelta(days=i * 53)).strftime("%Y-%m-%d")
        gender = "F" if i % 2 == 0 else "M"
        mobile = f"+97150{i * 137 % 9000000 + 1000000:07d}"
        email = f"{f.lower()}.{last_n.lower().replace('-', '').replace(' ', '')}@example.test"
        p_start = f"2026-{(i % 9) + 1:02d}-{(i * 3 % 25) + 1:02d}"
        p_end = f"2027-{(i % 9) + 1:02d}-{(i * 3 % 25) + 1:02d}"

        patients.append({
            "patient_id": pat_id,
            "mrn": mrn,
            "first_name": f,
            "last_name": last_n,
            "name": name,
            "dob": dob,
            "gender": gender,
            "nationality": nat,
            "emirate": em,
            "mobile": mobile,
            "email": email,
            "payer": payer,
            "plan": plan,
            "member_id": mem_id,
            "policy_start": p_start,
            "policy_end": p_end,
            "status": "Active"
        })

    _cached_patients = patients
    return patients


@router.get("", response_model=List[Dict[str, Any]])
@router.get("/", response_model=List[Dict[str, Any]])
def get_all_uae_patients(
    response: Response,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    search: Optional[str] = Query(None),
    emirate: Optional[str] = Query(None),
    payer: Optional[str] = Query(None),
    current_user: User = Depends(get_scoped_user),
):
    """
    Returns UAE Patient Master Directory matching the Dubai Clinic dataset with backend pagination.
    """
    patients = _get_all_raw_patients()

    if search:
        s = search.lower().strip()
        patients = [
            p for p in patients
            if s in (p.get("name") or "").lower()
            or s in (p.get("patient_id") or "").lower()
            or s in (p.get("mrn") or "").lower()
            or s in (p.get("mobile") or "")
            or s in (p.get("member_id") or "").lower()
            or s in (p.get("emirate") or "").lower()
            or s in (p.get("nationality") or "").lower()
        ]

    if emirate and emirate != "All":
        patients = [p for p in patients if p.get("emirate") == emirate]

    if payer and payer != "All":
        patients = [p for p in patients if p.get("payer") == payer]

    total_count = len(patients)
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Total-Pages"

    if page is not None:
        p_size = page_size or 20
        total_pages = max(1, math.ceil(total_count / p_size))
        response.headers["X-Total-Pages"] = str(total_pages)
        start = (page - 1) * p_size
        return patients[start:start + p_size]

    return patients
