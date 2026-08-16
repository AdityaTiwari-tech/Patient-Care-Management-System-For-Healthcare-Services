"""
app.py
Patient Care Management System for Healthcare Service — Streamlit entry
point. Handles session state, the left-side role nav, and routes to the
right dashboard section.
"""
import logging

import streamlit as st

from core.config import settings
from core.database import test_connection, init_db
from views import auth_view, patient_dashboard, doctor_portal, admin_portal
from views.components import load_css, ecg_divider, sidebar_nav

# --- Chatbot audit trail -------------------------------------------------
# Makes the agent's tool_start / tool_end / tool_error lines visible in the
# terminal. Setting the level alone is NOT enough: with no handler attached,
# Python's lastResort handler drops anything below WARNING, so every INFO
# trace vanishes silently. This StreamHandler is what makes the audit trail
# real — it is the only record of what the model asked for and what the DB
# returned.
_audit_log = logging.getLogger("smartcare")
if not _audit_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
    _audit_log.addHandler(_handler)
_audit_log.setLevel(logging.INFO)
_audit_log.propagate = False


st.set_page_config(
    page_title=settings.APP_NAME,
    page_icon="🫀",
    layout="wide",
    initial_sidebar_state="collapsed" if "user" not in st.session_state else "expanded",
)

load_css()

if "user" not in st.session_state:
    st.session_state.user = None

ROLE_NAV_ITEMS = {
    "patient": ["My Health 🫀", "Appointments", "Doctors", "Pharmacy 💊", "Smart Care AI", "Logout"],
    "doctor": ["Doctor Portal", "Doctors", "Smart Care AI", "Logout"],
    "admin": ["Admin Console", "Smart Care AI", "Logout"],
}


def _logout():
    st.session_state.user = None
    st.rerun()


def _sidebar(user) -> str:
    with st.sidebar:
        st.markdown(f"## 🫀 {settings.APP_NAME}")
        st.caption("Cardiac Patient Care Portal")
        ecg_divider()
        st.markdown(f"**{user.full_name}**")
        st.caption(f"{user.role.capitalize()} · {user.email}")
        st.write("")
        section = sidebar_nav(ROLE_NAV_ITEMS[user.role], nav_key=user.role, on_logout=_logout)
    return section


def main():
    if not test_connection():
        st.error(
            "Can't reach the MySQL database. Check `DB_HOST`, `DB_USER`, "
            "`DB_PASSWORD` and `DB_NAME` in your `.env` file, and confirm "
            "MySQL is running."
        )
        st.stop()

    # Create any tables added after the original schema (medicines,
    # prescriptions, prescription_items, medicine_orders,
    # medicine_order_items). Safe/no-op if they already exist.
    try:
        init_db()
    except Exception:
        pass

    user = st.session_state.user

    if user is None:
        auth_view.render()
        return

    section = _sidebar(user)

    if user.role == "patient":
        patient_dashboard.render(user, section)
    elif user.role == "doctor":
        doctor_portal.render(user, section)
    elif user.role == "admin":
        admin_portal.render(user, section)
    else:
        st.error("Unknown role on this account. Contact support.")


if __name__ == "__main__":
    main()