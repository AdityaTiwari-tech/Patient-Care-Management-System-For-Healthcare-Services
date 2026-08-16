"""
services/report_service.py
CRUD for a "patient report" — one saved Prescription (medicines table +
diagnosis + advice note) OPTIONALLY paired with the vitals/clinical note
captured at the same visit (a HealthRecord, linked via the new nullable
Prescription.health_record_id).

Deliberately does NOT reimplement or modify prescription creation logic:
create_report() calls services.prescription_service.create_prescription()
as-is (that's the one place that knows how to deduct catalog stock
correctly) and only adds the health_record_id link afterward. Everything
else here — get/update/delete — works directly against the models this
file DOES fully own the meaning of (Prescription, PrescriptionItem,
HealthRecord, Medicine for stock reversal on delete).

Design choice — medicine lines are immutable once saved: update_report()
can change diagnosis, advice note, and vitals/clinical notes, but NOT the
medicine table on an existing report. Real prescriptions aren't usually
edited after the fact either — if the medicines need to change, delete
the report (which reverses the stock deduction) and create a new one.
"""
from typing import List, Optional

from core.database import get_session
from models.models import Prescription, HealthRecord, Medicine
from services import health_service, prescription_service


class ReportError(Exception):
    pass


def create_report(
    doctor_id: int, patient_id: int, items: List[dict],
    diagnosis: str = "", advice_note: str = "",
    heart_rate: Optional[int] = None, blood_pressure: Optional[str] = None,
    pulse_oximetry: Optional[int] = None, ejection_fraction: Optional[int] = None,
    ecg_note: str = "", clinical_notes: str = "",
) -> int:
    """
    Creates the Prescription (via prescription_service, untouched — this
    still deducts stock exactly as it always has) and, if any vital or
    clinical note was actually provided, a linked HealthRecord too. If
    none were provided, the report is medicines-only — health_record_id
    stays NULL and the PDF simply omits the vitals section.

    Returns the new prescription id.
    """
    if not items:
        raise ReportError("Add at least one medicine before saving the report.")

    health_record_id = None
    has_vitals = any([
        heart_rate, blood_pressure, pulse_oximetry, ejection_fraction,
        ecg_note.strip() if ecg_note else "", clinical_notes.strip() if clinical_notes else "",
    ])
    if has_vitals:
        health_record_id = health_service.add_health_record(
            patient_id=patient_id, doctor_id=doctor_id,
            heart_rate=heart_rate or None, blood_pressure=blood_pressure or None,
            pulse_oximetry=pulse_oximetry or None, ejection_fraction=ejection_fraction or None,
            ecg_note=ecg_note, diagnosis=diagnosis, notes=clinical_notes,
        )

    prescription_id = prescription_service.create_prescription(
        patient_id=patient_id, doctor_id=doctor_id,
        items=items, diagnosis=diagnosis, advice_note=advice_note,
    )

    if health_record_id:
        with get_session() as db:
            p = db.get(Prescription, prescription_id)
            if p:
                p.health_record_id = health_record_id

    return prescription_id


def get_report(prescription_id: int) -> Optional[dict]:
    """Full, PDF-ready view of one report: prescription + items + patient
    + doctor + (optional) vitals, all resolved inside the session so the
    dict is safe to use after it closes."""
    with get_session() as db:
        p = db.get(Prescription, prescription_id)
        if not p:
            return None

        doctor_user = p.doctor.user if p.doctor else None
        specialty = p.doctor.specialty if p.doctor and p.doctor.specialty else None

        vitals = None
        if p.health_record_id and p.health_record:
            hr = p.health_record
            vitals = {
                "heart_rate": hr.heart_rate,
                "blood_pressure": hr.blood_pressure,
                "pulse_oximetry": hr.pulse_oximetry,
                "ejection_fraction": hr.ejection_fraction,
                "ecg_note": hr.ecg_note or "",
                "notes": hr.notes or "",
                "recorded_at": hr.recorded_at,
            }

        return {
            "id": p.id,
            "created_at": p.created_at,
            "diagnosis": p.diagnosis or "",
            "advice_note": p.advice_note or "",
            "patient_id": p.patient_id,
            "patient_name": p.patient.full_name if p.patient else "Patient",
            "doctor_name": doctor_user.full_name if doctor_user else "Doctor",
            "doctor_specialty": specialty.name if specialty else "General Medicine",
            "items": [
                {
                    "medicine_name": it.medicine_name,
                    "dosage": it.dosage or "",
                    "frequency": it.frequency or "",
                    "duration": it.duration or "",
                    "quantity": it.quantity or 0,
                    "instructions": it.instructions or "",
                }
                for it in p.items
            ],
            "vitals": vitals,
        }


def update_report(
    prescription_id: int, diagnosis: Optional[str] = None, advice_note: Optional[str] = None,
    heart_rate: Optional[int] = None, blood_pressure: Optional[str] = None,
    pulse_oximetry: Optional[int] = None, ejection_fraction: Optional[int] = None,
    ecg_note: Optional[str] = None, clinical_notes: Optional[str] = None,
) -> None:
    """Edits diagnosis / advice note / vitals / clinical notes on an
    existing report. Medicine lines are NOT editable here — see the
    module docstring. Every arg follows the rest of this codebase's
    convention of None = "leave unchanged"."""
    with get_session() as db:
        p = db.get(Prescription, prescription_id)
        if not p:
            raise ReportError("Report not found.")

        if diagnosis is not None:
            p.diagnosis = diagnosis
        if advice_note is not None:
            p.advice_note = advice_note

        if p.health_record_id:
            hr = db.get(HealthRecord, p.health_record_id)
            if hr:
                if heart_rate is not None:
                    hr.heart_rate = heart_rate
                if blood_pressure is not None:
                    hr.blood_pressure = blood_pressure
                if pulse_oximetry is not None:
                    hr.pulse_oximetry = pulse_oximetry
                if ejection_fraction is not None:
                    hr.ejection_fraction = ejection_fraction
                if ecg_note is not None:
                    hr.ecg_note = ecg_note
                if clinical_notes is not None:
                    hr.notes = clinical_notes
                if diagnosis is not None:
                    hr.diagnosis = diagnosis  # keep the two diagnoses in sync


def delete_report(prescription_id: int) -> None:
    """Deletes the report and reverses its stock deduction — the exact
    inverse of what prescription_service.create_prescription() did when
    it saved these items, using medicine_service's own adjust_stock
    logic path (directly against the Medicine model here, since we're
    already inside this transaction). Also deletes the linked
    HealthRecord, if this report had one — it was created specifically
    for this report by create_report(), so it belongs to the report's
    lifecycle, not the patient's independent clinical history."""
    with get_session() as db:
        p = db.get(Prescription, prescription_id)
        if not p:
            raise ReportError("Report not found.")

        for item in p.items:
            if item.medicine_id:
                medicine = db.get(Medicine, item.medicine_id)
                if medicine:
                    medicine.stock_quantity = (medicine.stock_quantity or 0) + item.quantity

        health_record_id = p.health_record_id
        db.delete(p)  # cascades to PrescriptionItem rows (cascade="all, delete-orphan")

        if health_record_id:
            hr = db.get(HealthRecord, health_record_id)
            if hr:
                db.delete(hr)