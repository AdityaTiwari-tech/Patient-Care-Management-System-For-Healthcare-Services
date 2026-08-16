"""
services/appointment_service.py
Booking, listing, updating and cancelling appointments.
"""
from datetime import datetime, date, time, timedelta
from typing import Optional, List

from sqlalchemy import and_
from sqlalchemy.orm import aliased
from sqlalchemy.exc import IntegrityError

from core.database import get_session
from models.models import Appointment, Doctor, User, Specialty
from services import notification_service

# The hospital's bookable half-hour slots, 9am-6pm. Defined once here —
# the booking UI, the chatbot's booking wizard, and the availability
# tools all import this instead of redefining it.
SLOT_TIMES = [time(h, m) for h in range(9, 18) for m in (0, 30)]


class AppointmentError(Exception):
    pass


def book_appointment(
    patient_id: int, doctor_id: int, scheduled_date: date,
    start_time: time, reason: str = "", source: str = "patient",
) -> int:
    with get_session() as db:
        appt = Appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            scheduled_date=scheduled_date,
            start_time=start_time,
            status="pending",
            reason=reason,
            source=source,
            created_at=datetime.utcnow(),
        )
        db.add(appt)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise AppointmentError("That time slot was just booked by someone else. Pick another.")
        appt_id = appt.id

        # Notify the doctor by email — done here, inside the one function
        # every booking path (the patient's own booking form AND the
        # chatbot's booking wizard) funnels through, so it fires
        # regardless of which UI made the booking. Looked up in the same
        # session/transaction rather than via a second query after
        # commit, since the patient/doctor rows are already loadable
        # here. send_email() never raises (it catches and logs
        # internally), so a failed/unconfigured SMTP setup can't block
        # the booking itself.
        patient = db.get(User, patient_id)
        doctor = db.get(Doctor, doctor_id)
        doctor_user = db.get(User, doctor.user_id) if doctor else None
        if doctor_user and doctor_user.email:
            notification_service.send_appointment_booked_email(
                doctor_email=doctor_user.email,
                doctor_name=doctor_user.full_name,
                patient_name=patient.full_name if patient else "A patient",
                scheduled_date=scheduled_date,
                start_time=start_time,
                reason=reason,
            )

        return appt_id


def list_patient_appointments(patient_id: int) -> List[dict]:
    with get_session() as db:
        rows = (
            db.query(Appointment, Doctor, User, Specialty)
            .join(Doctor, Appointment.doctor_id == Doctor.id)
            .join(User, Doctor.user_id == User.id)
            .outerjoin(Specialty, Doctor.specialty_id == Specialty.id)
            .filter(Appointment.patient_id == patient_id)
            .order_by(Appointment.scheduled_date.desc(), Appointment.start_time.desc())
            .all()
        )
        return [
            {
                "id": a.id, "doctor_name": u.full_name,
                "specialty": s.name if s else "General",
                "date": a.scheduled_date, "start_time": a.start_time,
                "status": a.status, "reason": a.reason,
            }
            for a, d, u, s in rows
        ]


def list_doctor_appointments(doctor_id: int, on_date: Optional[date] = None) -> List[dict]:
    with get_session() as db:
        q = (
            db.query(Appointment, User)
            .join(User, Appointment.patient_id == User.id)
            .filter(Appointment.doctor_id == doctor_id)
        )
        if on_date:
            q = q.filter(Appointment.scheduled_date == on_date)
        rows = q.order_by(Appointment.scheduled_date.desc(), Appointment.start_time).all()
        return [
            {
                "id": a.id, "patient_id": u.id, "patient_name": u.full_name,
                "date": a.scheduled_date, "start_time": a.start_time,
                "status": a.status, "reason": a.reason,
            }
            for a, u in rows
        ]


def list_all_appointments(status: Optional[str] = None) -> List[dict]:
    """For the admin dashboard."""
    with get_session() as db:
        DoctorUser = aliased(User)
        q = (
            db.query(Appointment, DoctorUser, User)
            .join(Doctor, Appointment.doctor_id == Doctor.id)
            .join(DoctorUser, Doctor.user_id == DoctorUser.id)
            .join(User, Appointment.patient_id == User.id)
        )
        if status:
            q = q.filter(Appointment.status == status)
        rows = q.order_by(Appointment.scheduled_date.desc()).all()
        return [
            {
                "id": a.id,
                "patient_name": patient_user.full_name,
                "doctor_name": doctor_user.full_name,
                "date": a.scheduled_date, "start_time": a.start_time,
                "status": a.status,
            }
            for a, doctor_user, patient_user in rows
        ]


def update_appointment_status(appointment_id: int, status: str) -> None:
    valid = {"pending", "confirmed", "completed", "cancelled"}
    if status not in valid:
        raise AppointmentError(f"Status must be one of {valid}")
    with get_session() as db:
        appt = db.query(Appointment).get(appointment_id)
        if not appt:
            raise AppointmentError("Appointment not found.")
        appt.status = status


def get_booked_slots(doctor_id: int, on_date: date) -> List[time]:
    with get_session() as db:
        rows = (
            db.query(Appointment.start_time)
            .filter(
                and_(
                    Appointment.doctor_id == doctor_id,
                    Appointment.scheduled_date == on_date,
                    Appointment.status != "cancelled",
                )
            )
            .all()
        )
        return [r[0] for r in rows]


def get_available_slots(doctor_id: int, on_date: date) -> List[time]:
    """Free slots for one doctor on one date — the ONLY source of times a
    patient (or the chatbot's booking wizard) can pick from, so an
    invented time like "3pm" can never reach book_appointment()."""
    booked = set(get_booked_slots(doctor_id, on_date))
    return [t for t in SLOT_TIMES if t not in booked]


def list_for_patient(patient_id: int, upcoming_only: bool = False) -> List[dict]:
    """Same shape as list_patient_appointments, with an upcoming-only
    filter — used by the chatbot's my_appointments tool."""
    appts = list_patient_appointments(patient_id)
    if upcoming_only:
        today = date.today()
        appts = [a for a in appts if a["date"] >= today and a["status"] != "cancelled"]
    return appts


def appointments_between(doctor_id: int, start_date: date, end_date: date) -> List[dict]:
    """All of one doctor's appointments across a date range in ONE query —
    used by the chatbot instead of calling appointments_on() once per day,
    which is what causes agent-loop recursion overruns on "how's my week"
    style questions."""
    with get_session() as db:
        rows = (
            db.query(Appointment, User)
            .join(User, Appointment.patient_id == User.id)
            .filter(
                Appointment.doctor_id == doctor_id,
                Appointment.scheduled_date >= start_date,
                Appointment.scheduled_date <= end_date,
            )
            .order_by(Appointment.scheduled_date, Appointment.start_time)
            .all()
        )
        return [
            {
                "id": a.id, "patient_id": u.id, "patient_name": u.full_name,
                "date": a.scheduled_date, "start_time": a.start_time,
                "status": a.status, "reason": a.reason,
            }
            for a, u in rows
        ]