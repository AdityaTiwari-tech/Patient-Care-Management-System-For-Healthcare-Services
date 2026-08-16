"""
services/doctor_service.py
Doctor directory, weekly slots, and prescribing (stored as a diagnosis +
notes entry in health_records, since that is the table available for it).
"""
from datetime import datetime, time
from typing import List, Optional

from core.database import get_session
from models.models import Doctor, User, Specialty, DoctorSlot, HealthRecord

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def list_specialties() -> List[dict]:
    with get_session() as db:
        rows = db.query(Specialty).order_by(Specialty.name).all()
        return [{"id": s.id, "name": s.name, "description": s.description, "icon": s.icon} for s in rows]


def add_specialty(name: str, description: str = "", icon: str = "") -> int:
    """Admin adds a new specialty to the directory. Raises ValueError on a
    duplicate name so the UI can surface it."""
    name = name.strip()
    with get_session() as db:
        existing = db.query(Specialty).filter(Specialty.name.ilike(name)).first()
        if existing:
            raise ValueError(f"A specialty named '{name}' already exists.")
        spec = Specialty(name=name, description=description.strip(), icon=icon.strip())
        db.add(spec)
        db.flush()
        return spec.id


def list_doctors(specialty_id: Optional[int] = None, search: str = "") -> List[dict]:
    with get_session() as db:
        q = db.query(Doctor, User, Specialty).join(User, Doctor.user_id == User.id).outerjoin(
            Specialty, Doctor.specialty_id == Specialty.id
        )
        if specialty_id:
            q = q.filter(Doctor.specialty_id == specialty_id)
        if search:
            like = f"%{search.lower()}%"
            q = q.filter(User.full_name.ilike(like))
        rows = q.order_by(Doctor.experience_years.desc()).all()
        return [
            {
                "doctor_id": d.id, "user_id": u.id, "name": u.full_name,
                "specialty": s.name if s else "General Medicine",
                "experience_years": d.experience_years or 0,
                "fee": float(d.consultation_fee) if d.consultation_fee else 0.0,
                "bio": d.bio, "avatar_url": d.avatar_url,
            }
            for d, u, s in rows
        ]


def list_doctor_accounts() -> List[dict]:
    """Every doctor with their login status — used by the admin's
    'Manage doctor accounts' panel to activate/deactivate a doctor's login.
    Unlike list_doctors(), this includes inactive accounts and the email."""
    with get_session() as db:
        rows = (
            db.query(Doctor, User, Specialty)
            .join(User, Doctor.user_id == User.id)
            .outerjoin(Specialty, Doctor.specialty_id == Specialty.id)
            .order_by(User.full_name)
            .all()
        )
        return [
            {
                "doctor_id": d.id, "user_id": u.id, "name": u.full_name,
                "email": u.email,
                "specialty": s.name if s else "General Medicine",
                "experience_years": d.experience_years or 0,
                "fee": float(d.consultation_fee) if d.consultation_fee else 0.0,
                "is_active": bool(u.is_active),
            }
            for d, u, s in rows
        ]


def get_doctor_profile(doctor_id: int) -> Optional[dict]:
    doctors = list_doctors()
    for d in doctors:
        if d["doctor_id"] == doctor_id:
            return d
    return None


def get_doctor_by_user(user_id: int) -> Optional[dict]:
    """Doctor profile dict for the given login (users.id) — used by
    ai/smartcare_agent.py to bind the doctor toolset to this doctor's own
    doctor_id (never the other way around)."""
    with get_session() as db:
        row = (
            db.query(Doctor, User, Specialty)
            .join(User, Doctor.user_id == User.id)
            .outerjoin(Specialty, Doctor.specialty_id == Specialty.id)
            .filter(Doctor.user_id == user_id)
            .first()
        )
        if not row:
            return None
        d, u, s = row
        return {
            "doctor_id": d.id, "user_id": u.id, "name": u.full_name,
            "specialty": s.name if s else "General Medicine",
            "experience_years": d.experience_years or 0,
            "fee": float(d.consultation_fee) if d.consultation_fee else 0.0,
            "bio": d.bio,
        }


def display_name(doctor: dict) -> str:
    """Consistent 'Dr. X (Specialty)' formatting for tool output text."""
    name = doctor.get("name", "")
    specialty = doctor.get("specialty")
    return f"Dr. {name} ({specialty})" if specialty else f"Dr. {name}"


def update_doctor_profile(
    doctor_id: int,
    specialty_id: Optional[int] = None,
    experience_years: Optional[int] = None,
    consultation_fee: Optional[float] = None,
    bio: Optional[str] = None,
) -> None:
    """Used by the admin's doctor-management screen to update a doctor's details."""
    with get_session() as db:
        doctor = db.query(Doctor).get(doctor_id)
        if not doctor:
            return
        if specialty_id is not None:
            doctor.specialty_id = specialty_id
        if experience_years is not None:
            doctor.experience_years = experience_years
        if consultation_fee is not None:
            doctor.consultation_fee = consultation_fee
        if bio is not None:
            doctor.bio = bio


def get_weekly_slots(doctor_id: int) -> List[dict]:
    with get_session() as db:
        rows = (
            db.query(DoctorSlot)
            .filter(DoctorSlot.doctor_id == doctor_id, DoctorSlot.is_active.is_(True))
            .order_by(DoctorSlot.day_of_week, DoctorSlot.start_time)
            .all()
        )
        return [
            {
                "id": r.id, "day": DAY_NAMES[r.day_of_week],
                "day_of_week": r.day_of_week,
                "start_time": r.start_time, "end_time": r.end_time,
            }
            for r in rows
        ]


def add_slot(doctor_id: int, day_of_week: int, start_time: time, end_time: time) -> None:
    with get_session() as db:
        db.add(DoctorSlot(
            doctor_id=doctor_id, day_of_week=day_of_week,
            start_time=start_time, end_time=end_time, is_active=True,
        ))


def deactivate_slot(slot_id: int) -> None:
    with get_session() as db:
        slot = db.query(DoctorSlot).get(slot_id)
        if slot:
            slot.is_active = False


def prescribe(patient_id: int, doctor_id: int, diagnosis: str, medicines_notes: str) -> int:
    """
    Records a prescription as a health_records row: `diagnosis` holds the
    condition, `notes` holds the free-text medicine / dosage plan.
    """
    with get_session() as db:
        rec = HealthRecord(
            patient_id=patient_id,
            recorded_at=datetime.utcnow(),
            diagnosis=diagnosis,
            notes=medicines_notes,
            doctor_id=doctor_id,
        )
        db.add(rec)
        db.flush()
        return rec.id