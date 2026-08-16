"""
services/analytics_service.py
Aggregate numbers for the admin dashboard's KPI cards + charts.
"""
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func

from core.database import get_session
from models.models import User, Doctor, Appointment, HealthRecord, Specialty


def get_kpis() -> dict:
    with get_session() as db:
        total_patients = db.query(User).filter(User.role == "patient").count()
        total_doctors = db.query(User).filter(User.role == "doctor").count()
        total_appointments = db.query(Appointment).count()
        pending = db.query(Appointment).filter(Appointment.status == "pending").count()
        confirmed = db.query(Appointment).filter(Appointment.status == "confirmed").count()
        completed = db.query(Appointment).filter(Appointment.status == "completed").count()
        cancelled = db.query(Appointment).filter(Appointment.status == "cancelled").count()
        return {
            "total_patients": total_patients,
            "total_doctors": total_doctors,
            "total_appointments": total_appointments,
            "pending": pending, "confirmed": confirmed,
            "completed": completed, "cancelled": cancelled,
        }


def appointments_last_n_days(n: int = 14) -> dict:
    start = date.today() - timedelta(days=n - 1)
    with get_session() as db:
        rows = (
            db.query(Appointment.scheduled_date)
            .filter(Appointment.scheduled_date >= start)
            .all()
        )
    counts = Counter(r[0] for r in rows)
    days = [start + timedelta(days=i) for i in range(n)]
    return {"dates": days, "counts": [counts.get(d, 0) for d in days]}


def doctors_per_specialty() -> dict:
    with get_session() as db:
        rows = (
            db.query(Specialty.name, Doctor.id)
            .outerjoin(Doctor, Doctor.specialty_id == Specialty.id)
            .all()
        )
    counts = Counter(r[0] for r in rows if r[1] is not None)
    return {"labels": list(counts.keys()), "values": list(counts.values())}


def appointments_per_specialty() -> dict:
    """Appointment volume per specialty, busiest first — admin analytics."""
    with get_session() as db:
        rows = (
            db.query(Specialty.name)
            .join(Doctor, Doctor.specialty_id == Specialty.id)
            .join(Appointment, Appointment.doctor_id == Doctor.id)
            .all()
        )
    counts = Counter(r[0] for r in rows)
    ordered = counts.most_common()
    return {"labels": [l for l, _ in ordered], "values": [v for _, v in ordered]}


def appointment_status_breakdown() -> dict:
    k = get_kpis()
    return {
        "labels": ["Pending", "Confirmed", "Completed", "Cancelled"],
        "values": [k["pending"], k["confirmed"], k["completed"], k["cancelled"]],
    }


def revenue_summary() -> dict:
    """Consultation-fee revenue for the admin's Analytics tab.

    `collected` = sum of each completed appointment's doctor consultation
    fee (money already earned). `pipeline` = the same sum for appointments
    still open (pending/confirmed) — expected revenue not yet realised.
    Arithmetic is done in SQL, never left to the model."""
    with get_session() as db:
        base = (
            db.query(func.coalesce(func.sum(Doctor.consultation_fee), 0))
            .select_from(Appointment)
            .join(Doctor, Appointment.doctor_id == Doctor.id)
        )
        collected = base.filter(Appointment.status == "completed").scalar() or 0
        pipeline = (
            base.filter(Appointment.status.in_(("pending", "confirmed"))).scalar() or 0
        )
        completed_count = (
            db.query(Appointment).filter(Appointment.status == "completed").count()
        )
    return {
        "collected": float(collected),
        "pipeline": float(pipeline),
        "completed_count": completed_count,
    }


def top_doctors(limit: int = 6) -> dict:
    """Busiest doctors by total appointment volume, most first — feeds the
    admin leaderboard bar chart."""
    with get_session() as db:
        rows = (
            db.query(User.full_name, func.count(Appointment.id))
            .join(Doctor, Doctor.user_id == User.id)
            .join(Appointment, Appointment.doctor_id == Doctor.id)
            .group_by(User.id)
            .order_by(func.count(Appointment.id).desc())
            .limit(limit)
            .all()
        )
    return {
        "labels": [f"Dr. {name}" for name, _ in rows],
        "values": [int(count) for _, count in rows],
    }


def user_counts() -> dict:
    """Role counts only — no names, no patient rows. Safe for the admin
    chatbot toolset, which is deliberately aggregates-only."""
    with get_session() as db:
        return {
            "patients": db.query(User).filter(User.role == "patient").count(),
            "doctors": db.query(User).filter(User.role == "doctor").count(),
            "admins": db.query(User).filter(User.role == "admin").count(),
        }


def doctors_available_today() -> int:
    """Count of doctors with at least one open slot today — a single
    aggregate number, not tied to any patient or doctor identity."""
    from services.appointment_service import get_available_slots
    from services.doctor_service import list_doctors

    today = date.today()
    return sum(1 for d in list_doctors() if get_available_slots(d["doctor_id"], today))