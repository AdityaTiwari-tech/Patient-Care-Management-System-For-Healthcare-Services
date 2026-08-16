"""
services/prescription_service.py
Creating and reading prescriptions. A prescription is one doctor→patient
event with an overall advice note and one-or-more medicine lines. Creating
one atomically reduces each medicine's stock, in the SAME transaction, so
stock and the prescription can never disagree: if any line is short on
stock, nothing is written.

All reads return plain dicts assembled inside the session to avoid
DetachedInstanceError once the session closes.
"""
from datetime import datetime
from typing import List, Optional

from core.database import get_session
from models.models import (
    Prescription, PrescriptionItem, Medicine, Doctor, User,
)


class PrescriptionError(Exception):
    pass


def create_prescription(
    patient_id: int, doctor_id: int, items: List[dict],
    diagnosis: str = "", advice_note: str = "",
) -> int:
    """
    items: list of dicts, each:
        {
          "medicine_id": int,      # required — must exist in the catalog
          "dosage": str, "frequency": str, "duration": str,
          "quantity": int,         # units dispensed; reduces stock
          "instructions": str,
        }
    Runs in one transaction: validates stock for every line first, then
    writes the prescription, its items, and the stock decrements together.
    """
    clean = [i for i in items if i.get("medicine_id")]
    if not clean:
        raise PrescriptionError("Add at least one medicine to the prescription.")

    with get_session() as db:
        # 1. Load + validate every medicine and its stock up front.
        for i in clean:
            med = db.query(Medicine).get(i["medicine_id"])
            if not med:
                raise PrescriptionError("One of the selected medicines no longer exists.")
            qty = int(i.get("quantity") or 0)
            if qty < 0:
                raise PrescriptionError("Quantity cannot be negative.")
            if qty > (med.stock_quantity or 0):
                raise PrescriptionError(
                    f"Not enough stock for {med.name}: "
                    f"{med.stock_quantity or 0} left, {qty} requested."
                )

        # 2. All good — write the prescription header.
        rx = Prescription(
            patient_id=patient_id, doctor_id=doctor_id,
            diagnosis=(diagnosis or "").strip(),
            advice_note=(advice_note or "").strip(),
            created_at=datetime.utcnow(),
        )
        db.add(rx)
        db.flush()  # assigns rx.id

        # 3. Write each line and decrement stock in the same transaction.
        for i in clean:
            med = db.query(Medicine).get(i["medicine_id"])
            qty = int(i.get("quantity") or 0)
            db.add(PrescriptionItem(
                prescription_id=rx.id,
                medicine_id=med.id,
                medicine_name=med.name,
                dosage=(i.get("dosage") or "").strip(),
                frequency=(i.get("frequency") or "").strip(),
                duration=(i.get("duration") or "").strip(),
                quantity=qty,
                instructions=(i.get("instructions") or "").strip(),
            ))
            med.stock_quantity = max(0, (med.stock_quantity or 0) - qty)

        return rx.id


def _rx_to_dict(db, rx: Prescription) -> dict:
    doctor = db.query(Doctor).get(rx.doctor_id)
    doctor_user = db.query(User).get(doctor.user_id) if doctor else None
    patient = db.query(User).get(rx.patient_id)
    items = (
        db.query(PrescriptionItem)
        .filter(PrescriptionItem.prescription_id == rx.id)
        .all()
    )
    return {
        "id": rx.id,
        "patient_id": rx.patient_id,
        "patient_name": patient.full_name if patient else "",
        "doctor_id": rx.doctor_id,
        "doctor_name": doctor_user.full_name if doctor_user else "",
        "diagnosis": rx.diagnosis or "",
        "advice_note": rx.advice_note or "",
        "created_at": rx.created_at,
        "items": [
            {
                "medicine_name": it.medicine_name,
                "dosage": it.dosage or "",
                "frequency": it.frequency or "",
                "duration": it.duration or "",
                "quantity": it.quantity or 0,
                "instructions": it.instructions or "",
            }
            for it in items
        ],
    }


def list_for_patient(patient_id: int) -> List[dict]:
    with get_session() as db:
        rows = (
            db.query(Prescription)
            .filter(Prescription.patient_id == patient_id)
            .order_by(Prescription.created_at.desc())
            .all()
        )
        return [_rx_to_dict(db, rx) for rx in rows]


def list_by_doctor(doctor_id: int, patient_id: Optional[int] = None) -> List[dict]:
    with get_session() as db:
        q = db.query(Prescription).filter(Prescription.doctor_id == doctor_id)
        if patient_id is not None:
            q = q.filter(Prescription.patient_id == patient_id)
        rows = q.order_by(Prescription.created_at.desc()).all()
        return [_rx_to_dict(db, rx) for rx in rows]


def list_all(limit: int = 200) -> List[dict]:
    """Every prescription in the system, newest first — the admin's
    oversight view (read-only; admins never write prescriptions)."""
    with get_session() as db:
        rows = (
            db.query(Prescription)
            .order_by(Prescription.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_rx_to_dict(db, rx) for rx in rows]


def get_prescription(prescription_id: int) -> Optional[dict]:
    with get_session() as db:
        rx = db.query(Prescription).get(prescription_id)
        return _rx_to_dict(db, rx) if rx else None
