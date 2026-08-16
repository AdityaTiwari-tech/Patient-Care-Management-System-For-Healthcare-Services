"""
models/models.py
ORM models mapped 1:1 onto the tables already created in patient_care_db.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, Time,
    Text, Numeric, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship

from core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(160), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)  # 'patient' | 'doctor' | 'admin'
    gender = Column(String(20))
    dob = Column(Date)
    phone = Column(String(30))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    doctor_profile = relationship("Doctor", back_populates="user", uselist=False)
    appointments = relationship(
        "Appointment", back_populates="patient",
        foreign_keys="Appointment.patient_id"
    )
    health_records = relationship(
        "HealthRecord", back_populates="patient",
        foreign_keys="HealthRecord.patient_id"
    )
    chat_messages = relationship("ChatMessage", back_populates="user")


class Specialty(Base):
    __tablename__ = "specialties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(80), nullable=False, unique=True)
    description = Column(String(255))
    icon = Column(String(16))

    doctors = relationship("Doctor", back_populates="specialty")
    medicines = relationship("Medicine", back_populates="specialty")


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    specialty_id = Column(Integer, ForeignKey("specialties.id"))
    bio = Column(Text)
    experience_years = Column(Integer)
    consultation_fee = Column(Numeric(10, 2))
    avatar_url = Column(String(255))

    user = relationship("User", back_populates="doctor_profile")
    specialty = relationship("Specialty", back_populates="doctors")
    slots = relationship("DoctorSlot", back_populates="doctor")
    appointments = relationship("Appointment", back_populates="doctor")
    health_records = relationship("HealthRecord", back_populates="doctor")


class DoctorSlot(Base):
    __tablename__ = "doctor_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday .. 6=Sunday
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_active = Column(Boolean, default=True)

    doctor = relationship("Doctor", back_populates="slots")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("doctor_id", "scheduled_date", "start_time", name="uq_doctor_slot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    scheduled_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time)
    status = Column(String(20), default="pending")  # pending|confirmed|completed|cancelled
    reason = Column(String(255))
    source = Column(String(20), default="patient")   # patient|admin
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("User", back_populates="appointments", foreign_keys=[patient_id])
    doctor = relationship("Doctor", back_populates="appointments")


class HealthRecord(Base):
    __tablename__ = "health_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    heart_rate = Column(Integer)
    blood_pressure = Column(String(20))
    troponin = Column(Numeric(6, 3))
    ejection_fraction = Column(Integer)
    cardiac_output = Column(Numeric(5, 2))
    pulse_oximetry = Column(Integer)
    ecg_note = Column(String(120))
    diagnosis = Column(String(255))
    notes = Column(Text)
    doctor_id = Column(Integer, ForeignKey("doctors.id"))

    patient = relationship("User", back_populates="health_records", foreign_keys=[patient_id])
    doctor = relationship("Doctor", back_populates="health_records")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(12))  # 'user' | 'assistant'
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="chat_messages")


class Medicine(Base):
    """The pharmacy catalog the admin manages. Doctors prescribe from these
    rows, and prescribing reduces stock_quantity. Patients can also buy
    directly from the catalog — see MedicineOrder."""
    __tablename__ = "medicines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(160), nullable=False)
    description = Column(String(255))          # form/strength, e.g. "Tablet 75mg"
    price = Column(Numeric(10, 2), default=0)  # price per unit
    stock_quantity = Column(Integer, default=0)
    # Nullable: older/general-purpose medicines (e.g. antibiotics,
    # painkillers) may not map cleanly onto one clinical specialty. The
    # catalog and shop dropdowns both treat a NULL here as "General".
    specialty_id = Column(Integer, ForeignKey("specialties.id"))
    # Either a local path under assets/medicines/ or an http(s) URL — see
    # services/medicine_service.py. We store the reference, never the bytes.
    image_path = Column(String(500))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    specialty = relationship("Specialty", back_populates="medicines")
    prescription_items = relationship("PrescriptionItem", back_populates="medicine")


class Prescription(Base):
    """One prescribing event: a doctor, a patient, an overall advice note,
    and one-or-more PrescriptionItem lines."""
    __tablename__ = "prescriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    diagnosis = Column(String(255))
    advice_note = Column(Text)
    # Nullable, set by services/report_service.py when a doctor captures
    # vitals/clinical notes together with the medicines in one "patient
    # report" — see ai note in report_service.py for why this is a loose
    # link (nullable FK) rather than folding HealthRecord's columns
    # directly onto Prescription: HealthRecord already has its own
    # independent life (it drives the patient's vitals trend charts on
    # views/patient_dashboard.py) and predates this feature.
    health_record_id = Column(Integer, ForeignKey("health_records.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("User", foreign_keys=[patient_id])
    doctor = relationship("Doctor", foreign_keys=[doctor_id])
    health_record = relationship("HealthRecord", foreign_keys=[health_record_id])
    items = relationship(
        "PrescriptionItem", back_populates="prescription",
        cascade="all, delete-orphan",
    )


class PrescriptionItem(Base):
    """A single medicine line on a prescription. medicine_name is snapshotted
    so the prescription still reads correctly even if the catalog row is
    later edited or deactivated."""
    __tablename__ = "prescription_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prescription_id = Column(Integer, ForeignKey("prescriptions.id"), nullable=False)
    medicine_id = Column(Integer, ForeignKey("medicines.id"))
    medicine_name = Column(String(160), nullable=False)
    dosage = Column(String(120))       # e.g. "1 tablet", "5 ml"
    frequency = Column(String(120))    # e.g. "Twice daily"
    duration = Column(String(120))     # e.g. "7 days"
    quantity = Column(Integer, default=0)   # units dispensed -> reduces stock
    instructions = Column(String(255))      # e.g. "After food"

    prescription = relationship("Prescription", back_populates="items")
    medicine = relationship("Medicine", back_populates="prescription_items")


class MedicineOrder(Base):
    """A patient's direct purchase from the pharmacy catalog — separate
    from Prescription, which is a doctor's clinical record of what they
    prescribed. Payment is simulated entirely in the UI (views/pharmacy_view.py);
    this row is only written once that fake payment step reports success,
    by services/order_service.place_order()."""
    __tablename__ = "medicine_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="paid")  # paid|failed|refunded
    total_amount = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("User", foreign_keys=[patient_id])
    items = relationship(
        "MedicineOrderItem", back_populates="order",
        cascade="all, delete-orphan",
    )


class MedicineOrderItem(Base):
    """A single medicine line on a purchase. medicine_name/unit_price are
    snapshotted at purchase time — same reasoning as PrescriptionItem: the
    order should still read correctly even if the catalog row is later
    edited, repriced, or deactivated."""
    __tablename__ = "medicine_order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("medicine_orders.id"), nullable=False)
    medicine_id = Column(Integer, ForeignKey("medicines.id"))
    medicine_name = Column(String(160), nullable=False)
    unit_price = Column(Numeric(10, 2), default=0)
    quantity = Column(Integer, default=0)
    line_total = Column(Numeric(10, 2), default=0)

    order = relationship("MedicineOrder", back_populates="items")
    medicine = relationship("Medicine")


