from fastapi import APIRouter
from typing import List, Dict, Any
from datetime import datetime, timedelta

router = APIRouter()

# Deterministic specifications matching the UAE Dubai Demo Dataset
EMIRATES = ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Fujairah", "Ras Al Khaimah", "Umm Al Quwain", "Al Ain"]
PAYERS = ["Daman", "Orient Insurance", "MetLife", "Bupa Arabia", "Self Pay", "Sukoon", "Cigna", "ADNIC", "AXA Gulf", "NMC Insurance"]
PLANS = ["Gold", "Silver", "Premium", "Standard", "Basic", "Self Pay"]
NATIONALITIES = ["UAE", "India", "Pakistan", "Egypt", "Jordan", "Philippines", "Bangladesh", "UK"]
FIRST_NAMES = ["Mariam", "Reem", "Aisha", "Mona", "Huda", "Khalid", "Tariq", "Mohammed", "Ahmed", "Rashid", "Layla", "Hassan", "Sara", "Ali", "Omar", "Amira", "Fatima", "Saeed", "Noor"]
LAST_NAMES = ["Ali", "Malik", "Verma", "Sharma", "George", "Nair", "Al-Mansoori", "Rahman", "Al-Hassan", "Ahmed", "Al-Nuaimi", "Patel", "Siddiqui", "Khan", "Hussain", "Thomas"]


@router.get("/", response_model=List[Dict[str, Any]])
def get_all_uae_patients():
    """
    Returns the complete 500 UAE Patient Master Directory matching the Dubai Clinic dataset.
    """
    patients = []
    base_dob = datetime(1960, 1, 1)

    for i in range(1, 501):
        f = FIRST_NAMES[(i * 7) % len(FIRST_NAMES)]
        last_n = LAST_NAMES[(i * 11) % len(LAST_NAMES)]
        name = f"{f} {last_n}"
        em = EMIRATES[i % len(EMIRATES)]
        nat = NATIONALITIES[(i * 3) % len(NATIONALITIES)]
        payer = PAYERS[(i * 5) % len(PAYERS)]
        plan = PLANS[(i * 2) % len(PLANS)] if payer != "Self Pay" else "Self Pay"
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

    return patients
