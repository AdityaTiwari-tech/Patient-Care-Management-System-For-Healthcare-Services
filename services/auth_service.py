"""
services/auth_service.py
Registration, login and role-lookup logic.
"""
from datetime import datetime, date
from typing import Optional

from core.database import get_session
from core.security import hash_password, verify_password, validate_password_strength
from models.models import User, Doctor
from services import notification_service


class AuthError(Exception):
    pass


def register_user(
    full_name: str,
    email: str,
    password: str,
    role: str,
    gender: Optional[str] = None,
    dob: Optional[date] = None,
    phone: Optional[str] = None,
    # doctor-only fields
    specialty_id: Optional[int] = None,
    experience_years: Optional[int] = None,
    consultation_fee: Optional[float] = None,
    bio: Optional[str] = None,
) -> User:
    role = role.lower().strip()
    if role not in ("patient", "doctor"):
        raise AuthError("Self-registration is only available for patients and doctors.")

    password_errors = validate_password_strength(password)
    if password_errors:
        raise AuthError("Password must have " + ", ".join(password_errors) + ".")

    with get_session() as db:
        existing = db.query(User).filter(User.email == email.lower().strip()).first()
        if existing:
            raise AuthError("An account with this email already exists.")

        user = User(
            full_name=full_name.strip(),
            email=email.lower().strip(),
            password_hash=hash_password(password),
            role=role,
            gender=gender,
            dob=dob,
            phone=phone,
            is_active=True,
            created_at=datetime.utcnow(),
        )
        db.add(user)
        db.flush()  # get user.id before commit

        if role == "doctor":
            doctor = Doctor(
                user_id=user.id,
                specialty_id=specialty_id,
                bio=bio,
                experience_years=experience_years or 0,
                consultation_fee=consultation_fee or 0,
            )
            db.add(doctor)

        db.flush()
        db.refresh(user)
        # Detach-safe copy of the fields the UI needs
        return _detached_copy(user)


def login_user(email: str, password: str) -> User:
    with get_session() as db:
        user = db.query(User).filter(User.email == email.lower().strip()).first()
        if not user or not verify_password(password, user.password_hash):
            raise AuthError("Incorrect email or password.")
        if not user.is_active:
            raise AuthError("This account has been deactivated. Contact the admin.")
        return _detached_copy(user)


def _detached_copy(user: User) -> User:
    """Return a plain, session-independent snapshot for storing in st.session_state."""
    return User(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        gender=user.gender,
        dob=user.dob,
        phone=user.phone,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def create_doctor_account(
    full_name: str,
    email: str,
    password: str,
    specialty_id: Optional[int] = None,
    experience_years: Optional[int] = None,
    consultation_fee: Optional[float] = None,
    bio: Optional[str] = None,
    admin_name: Optional[str] = None,
    admin_email: Optional[str] = None,
) -> User:
    """Admin-only: create a new doctor login + doctor profile in one step.
    Thin wrapper over register_user so the admin portal doesn't depend on
    the 'role' argument directly.

    admin_name/admin_email should be the CURRENTLY LOGGED-IN admin's own
    name/email (pass st.session_state.user.full_name / .email from
    views/admin_portal.py) — they're used only to personalize the
    notification email (display name + Reply-To), not stored anywhere.

    On success, emails the new doctor their login details — this is the
    doctor's only notice the account exists, since they didn't set the
    password themselves. Email failure (e.g. SMTP not configured) never
    blocks account creation; it's a best-effort notification, not part
    of the transaction."""
    user = register_user(
        full_name=full_name, email=email, password=password, role="doctor",
        specialty_id=specialty_id, experience_years=experience_years,
        consultation_fee=consultation_fee, bio=bio,
    )
    notification_service.send_doctor_account_created_email(
        doctor_email=user.email, doctor_name=user.full_name, password=password,
        admin_name=admin_name, admin_email=admin_email,
    )
    return user


def set_account_active(user_id: int, is_active: bool) -> None:
    """Admin-only: enable or disable a login. A deactivated user can no
    longer sign in (login_user rejects is_active == False)."""
    with get_session() as db:
        user = db.query(User).get(user_id)
        if user:
            user.is_active = bool(is_active)


def get_doctor_id_for_user(user_id: int) -> Optional[int]:
    with get_session() as db:
        doc = db.query(Doctor).filter(Doctor.user_id == user_id).first()
        return doc.id if doc else None


def user_to_dict(user: User) -> dict:
    """
    Plain-dict view of the logged-in user, for anything that shouldn't
    depend on our ORM/session-detached User object — chiefly
    ai/smartcare_agent.py, which binds tool closures to user["id"] /
    user["role"].
    """
    return {
        "id": user.id,
        "role": user.role,
        "full_name": user.full_name,
        "email": user.email,
    }