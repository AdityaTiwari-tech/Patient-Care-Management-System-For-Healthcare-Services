"""
services/health_service.py
Reading and writing cardiac health records / vitals.
"""
from datetime import datetime
from typing import List, Optional

from core.database import get_session
from models.models import HealthRecord, Doctor, User


def add_health_record(
    patient_id: int, doctor_id: Optional[int] = None,
    heart_rate: Optional[int] = None, blood_pressure: Optional[str] = None,
    troponin: Optional[float] = None, ejection_fraction: Optional[int] = None,
    cardiac_output: Optional[float] = None, pulse_oximetry: Optional[int] = None,
    ecg_note: str = "", diagnosis: str = "", notes: str = "",
) -> int:
    with get_session() as db:
        rec = HealthRecord(
            patient_id=patient_id, doctor_id=doctor_id,
            recorded_at=datetime.utcnow(),
            heart_rate=heart_rate, blood_pressure=blood_pressure,
            troponin=troponin, ejection_fraction=ejection_fraction,
            cardiac_output=cardiac_output, pulse_oximetry=pulse_oximetry,
            ecg_note=ecg_note, diagnosis=diagnosis, notes=notes,
        )
        db.add(rec)
        db.flush()
        return rec.id


def get_patient_records(patient_id: int) -> List[dict]:
    with get_session() as db:
        rows = (
            db.query(HealthRecord, Doctor, User)
            .outerjoin(Doctor, HealthRecord.doctor_id == Doctor.id)
            .outerjoin(User, Doctor.user_id == User.id)
            .filter(HealthRecord.patient_id == patient_id)
            .order_by(HealthRecord.recorded_at.desc())
            .all()
        )
        return [
            {
                "id": r.id, "recorded_at": r.recorded_at,
                "heart_rate": r.heart_rate, "blood_pressure": r.blood_pressure,
                "troponin": float(r.troponin) if r.troponin is not None else None,
                "ejection_fraction": r.ejection_fraction,
                "cardiac_output": float(r.cardiac_output) if r.cardiac_output is not None else None,
                "pulse_oximetry": r.pulse_oximetry, "ecg_note": r.ecg_note,
                "diagnosis": r.diagnosis, "notes": r.notes,
                "doctor_name": u.full_name if u else "Self-reported",
            }
            for r, d, u in rows
        ]


def get_all_patients() -> List[dict]:
    """For the admin dashboard patient directory."""
    with get_session() as db:
        rows = db.query(User).filter(User.role == "patient").order_by(User.full_name).all()
        return [{"id": u.id, "name": u.full_name, "email": u.email,
                 "gender": u.gender, "phone": u.phone} for u in rows]


def get_latest_vitals(patient_id: int) -> Optional[dict]:
    records = get_patient_records(patient_id)
    return records[0] if records else None


# Aliases matching the blueprint's naming, used by ai/smartcare_agent.py's
# patient tools (my_health_summary / my_health_trend).
def latest_vitals(patient_id: int) -> Optional[dict]:
    return get_latest_vitals(patient_id)


def history(patient_id: int) -> List[dict]:
    return get_patient_records(patient_id)


# (label, record field, unit, "lower"|"higher" = which direction is GOOD).
# This is the one place that decides whether a falling number is good news
# (heart rate, troponin) or bad news (ejection fraction, SpO2, cardiac
# output) — the LLM is never trusted to work that out itself, and neither
# is arithmetic across rows left to it (Rule A: Python computes, the model
# only narrates).
TREND_METRICS = [
    ("Heart rate", "heart_rate", "bpm", "lower"),
    ("Blood pressure (systolic)", "bp_systolic", "mmHg", "lower"),
    ("SpO2", "pulse_oximetry", "%", "higher"),
    ("Ejection fraction", "ejection_fraction", "%", "higher"),
    ("Troponin", "troponin", "ng/mL", "lower"),
    ("Cardiac output", "cardiac_output", "L/min", "higher"),
]


def get_health_trend(patient_id: int) -> Optional[dict]:
    """
    Computes, in Python, the change between the patient's earliest and
    latest non-null value for each vital — including whether that change
    counts as "improving" or "worsening" per TREND_METRICS' polarity.
    Returns None if there are fewer than 2 records to compare.
    """
    records = get_patient_records(patient_id)
    if len(records) < 2:
        return None

    chronological = list(reversed(records))  # get_patient_records is newest-first
    for r in chronological:
        bp = r.get("blood_pressure") or ""
        try:
            r["bp_systolic"] = int(bp.split("/")[0])
        except (ValueError, IndexError):
            r["bp_systolic"] = None

    deltas = []
    for label, field, unit, polarity in TREND_METRICS:
        present = [r for r in chronological if r.get(field) is not None]
        if len(present) < 2:
            continue
        first, last = present[0][field], present[-1][field]
        change = round(last - first, 2)
        if change == 0:
            direction = "stable"
        elif (change < 0 and polarity == "lower") or (change > 0 and polarity == "higher"):
            direction = "improving"
        else:
            direction = "worsening"
        deltas.append({
            "label": label, "unit": unit, "first": first, "last": last,
            "change": change, "direction": direction,
        })

    if not deltas:
        return None

    return {
        "start_date": chronological[0]["recorded_at"],
        "end_date": chronological[-1]["recorded_at"],
        "record_count": len(chronological),
        "deltas": deltas,
    }