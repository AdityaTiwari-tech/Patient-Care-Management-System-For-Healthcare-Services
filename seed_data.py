"""
seed_data.py
Run once after creating the schema in MySQL Workbench:

    python seed_data.py

Creates a starter set of specialties and a bootstrap admin account
(reads ADMIN_EMAIL / ADMIN_PASSWORD from .env, with safe fallbacks).
Safe to re-run — it skips anything that already exists.
"""
import os
from datetime import datetime, date

from core.config import settings
from core.database import Base, engine, get_session, test_connection
from core.security import hash_password
from models.models import User, Specialty, Doctor  # noqa: F401 (ensures all tables are registered)

SPECIALTIES = [
    ("Cardiology", "Heart rhythm, coronary disease and heart failure care", "🫀"),
    ("General Medicine", "Everyday illnesses, checkups and referrals", "🩺"),
    ("Endocrinology", "Diabetes, thyroid and hormone-related conditions", "🧪"),
    ("Nephrology", "Kidney health, closely tied to cardiovascular risk", "🫘"),
    ("Pulmonology", "Lungs and breathing, often co-managed with heart care", "🫁"),
]

DEFAULT_DOCTOR_PASSWORD = os.getenv("DEFAULT_DOCTOR_PASSWORD", "Doctor@123")
DEFAULT_PATIENT_PASSWORD = os.getenv("DEFAULT_PATIENT_PASSWORD", "Patient@123")

# (full_name, email, gender, dob, phone, specialty, experience_years, fee, bio)
DOCTORS = [
    ("Dr. Arjun Mehta", "arjun.mehta@smartcare.local", "Male", date(1978, 3, 14), "9810011122",
     "Cardiology", 22, 1200, "Interventional cardiologist focused on coronary artery disease and angioplasty."),
    ("Dr. Kavita Rao", "kavita.rao@smartcare.local", "Female", date(1985, 7, 2), "9810011123",
     "Cardiology", 14, 900, "Manages heart failure, arrhythmias and preventive cardiac care."),

    ("Dr. Sameer Kulkarni", "sameer.kulkarni@smartcare.local", "Male", date(1980, 11, 9), "9810011124",
     "General Medicine", 18, 500, "General physician for everyday illness, checkups and specialist referrals."),
    ("Dr. Priya Nair", "priya.nair@smartcare.local", "Female", date(1990, 2, 21), "9810011125",
     "General Medicine", 9, 400, "Focuses on preventive care, common infections and chronic-disease follow-up."),

    ("Dr. Rohit Malhotra", "rohit.malhotra@smartcare.local", "Male", date(1983, 5, 30), "9810011126",
     "Endocrinology", 16, 800, "Treats diabetes, thyroid disorders and metabolic syndrome."),
    ("Dr. Anjali Deshpande", "anjali.deshpande@smartcare.local", "Female", date(1988, 9, 17), "9810011127",
     "Endocrinology", 11, 700, "Specializes in diabetes management and hormonal health in women."),

    ("Dr. Vikram Chawla", "vikram.chawla@smartcare.local", "Male", date(1975, 1, 25), "9810011128",
     "Nephrology", 24, 1000, "Manages chronic kidney disease and hypertension linked to heart risk."),
    ("Dr. Neha Bhatt", "neha.bhatt@smartcare.local", "Female", date(1992, 4, 12), "9810011129",
     "Nephrology", 7, 650, "Focuses on early-stage kidney disease and dialysis planning."),

    ("Dr. Manish Kapoor", "manish.kapoor@smartcare.local", "Male", date(1979, 6, 6), "9810011130",
     "Pulmonology", 20, 900, "Treats asthma, COPD and breathing conditions often co-managed with cardiac care."),
    ("Dr. Sunita Iyer", "sunita.iyer@smartcare.local", "Female", date(1987, 12, 3), "9810011131",
     "Pulmonology", 12, 750, "Specializes in sleep-related breathing disorders and chronic cough."),
]

# (full_name, email, gender, dob, phone)
PATIENTS = [
    ("Ravi Kumar", "ravi.kumar@example.com", "Male", date(1965, 4, 11), "9820011201"),
    ("Sunil Sharma", "sunil.sharma@example.com", "Male", date(1958, 8, 23), "9820011202"),
    ("Meena Gupta", "meena.gupta@example.com", "Female", date(1972, 1, 5), "9820011203"),
    ("Anita Verma", "anita.verma@example.com", "Female", date(1980, 6, 19), "9820011204"),
    ("Rajesh Singh", "rajesh.singh@example.com", "Male", date(1969, 10, 30), "9820011205"),
    ("Pooja Agarwal", "pooja.agarwal@example.com", "Female", date(1991, 3, 8), "9820011206"),
    ("Deepak Yadav", "deepak.yadav@example.com", "Male", date(1975, 12, 14), "9820011207"),
    ("Kiran Joshi", "kiran.joshi@example.com", "Female", date(1988, 5, 27), "9820011208"),
    ("Amit Trivedi", "amit.trivedi@example.com", "Male", date(1963, 9, 2), "9820011209"),
    ("Shalini Menon", "shalini.menon@example.com", "Female", date(1995, 7, 16), "9820011210"),
    ("Vijay Reddy", "vijay.reddy@example.com", "Male", date(1970, 2, 28), "9820011211"),
    ("Nisha Pillai", "nisha.pillai@example.com", "Female", date(1983, 11, 4), "9820011212"),
    ("Suresh Pandey", "suresh.pandey@example.com", "Male", date(1955, 6, 21), "9820011213"),
    ("Rekha Choudhary", "rekha.choudhary@example.com", "Female", date(1978, 4, 9), "9820011214"),
    ("Manoj Tiwari", "manoj.tiwari@example.com", "Male", date(1993, 8, 30), "9820011215"),
    ("Swati Bansal", "swati.bansal@example.com", "Female", date(1967, 1, 17), "9820011216"),
    ("Ashok Mishra", "ashok.mishra@example.com", "Male", date(1960, 10, 6), "9820011217"),
    ("Divya Saxena", "divya.saxena@example.com", "Female", date(1998, 2, 12), "9820011218"),
    ("Naveen Kumar", "naveen.kumar@example.com", "Male", date(1985, 5, 23), "9820011219"),
    ("Geeta Rathore", "geeta.rathore@example.com", "Female", date(1973, 9, 28), "9820011220"),
]


def create_tables():
    """Creates any tables not already present. Existing tables are left untouched."""
    Base.metadata.create_all(bind=engine)
    print("✓ Verified/created tables from models/models.py")


def seed_specialties():
    with get_session() as db:
        existing = {s.name for s in db.query(Specialty).all()}
        added = 0
        for name, desc, icon in SPECIALTIES:
            if name not in existing:
                db.add(Specialty(name=name, description=desc, icon=icon))
                added += 1
        print(f"✓ Specialties: {added} added, {len(existing)} already present")


def seed_admin():
    admin_email = os.getenv("ADMIN_EMAIL", "admin@smartcare.local")
    admin_password = os.getenv("ADMIN_PASSWORD", "Admin@123")

    with get_session() as db:
        if db.query(User).filter(User.email == admin_email).first():
            print(f"✓ Admin already exists ({admin_email})")
            return
        db.add(User(
            full_name="Hospital Administrator",
            email=admin_email,
            password_hash=hash_password(admin_password),
            role="admin",
            is_active=True,
            created_at=datetime.utcnow(),
        ))
        print(f"✓ Created admin account -> {admin_email} / {admin_password}")
        print("  (change this password after first login, or set ADMIN_EMAIL / ADMIN_PASSWORD in .env)")


def seed_doctors():
    with get_session() as db:
        specialty_ids = {s.name: s.id for s in db.query(Specialty).all()}
        existing_emails = {u.email for u in db.query(User).filter(User.role == "doctor").all()}

        added = 0
        for full_name, email, gender, dob, phone, specialty_name, exp_years, fee, bio in DOCTORS:
            if email in existing_emails:
                continue
            user = User(
                full_name=full_name, email=email,
                password_hash=hash_password(DEFAULT_DOCTOR_PASSWORD),
                role="doctor", gender=gender, dob=dob, phone=phone,
                is_active=True, created_at=datetime.utcnow(),
            )
            db.add(user)
            db.flush()  # get user.id

            db.add(Doctor(
                user_id=user.id,
                specialty_id=specialty_ids.get(specialty_name),
                bio=bio, experience_years=exp_years, consultation_fee=fee,
            ))
            added += 1

        print(f"✓ Doctors: {added} added, {len(existing_emails)} already present")
        if added:
            print(f"  All new doctor accounts use the password: {DEFAULT_DOCTOR_PASSWORD}")


def seed_patients():
    with get_session() as db:
        existing_emails = {u.email for u in db.query(User).filter(User.role == "patient").all()}

        added = 0
        for full_name, email, gender, dob, phone in PATIENTS:
            if email in existing_emails:
                continue
            db.add(User(
                full_name=full_name, email=email,
                password_hash=hash_password(DEFAULT_PATIENT_PASSWORD),
                role="patient", gender=gender, dob=dob, phone=phone,
                is_active=True, created_at=datetime.utcnow(),
            ))
            added += 1

        print(f"✓ Patients: {added} added, {len(existing_emails)} already present")
        if added:
            print(f"  All new patient accounts use the password: {DEFAULT_PATIENT_PASSWORD}")


if __name__ == "__main__":
    print(f"Connecting to {settings.DB_USER}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME} ...")
    if not test_connection():
        print(
            "\n✗ Could not reach MySQL with the values currently in your .env file.\n"
            "  Double-check, one at a time:\n"
            "   - DB_HOST is just a hostname, e.g. `localhost` (no @ or password in it)\n"
            "   - DB_USER is just your MySQL username, e.g. `root`\n"
            "   - DB_PASSWORD is just your MySQL password, with no quotes around it\n"
            "   - DB_PORT is `3306` unless you changed it\n"
            "   - MySQL itself is actually running (check MySQL Workbench connects)\n"
        )
        raise SystemExit(1)
    print("✓ Connected.\n")

    create_tables()
    seed_specialties()
    seed_admin()
    seed_doctors()
    seed_patients()
    print("\nSeeding complete. Run the app with:  streamlit run app.py")
    print(
        f"Example logins — doctor: {DOCTORS[0][1]} / {DEFAULT_DOCTOR_PASSWORD}, "
        f"patient: {PATIENTS[0][1]} / {DEFAULT_PATIENT_PASSWORD}"
    )