"""
views/doctors_view.py
Browse the hospital's doctors by specialty / experience, shown as a card
grid — used inside both the patient and doctor dashboards' "Doctors" tab.

Filters (added): availability, consultation fee, and years of experience,
layered on top of the existing specialty + name search. Availability
reuses services/appointment_service.get_available_slots() — the same
function the booking flow trusts — so "Available today" here means the
same thing it means when a patient actually tries to book.
"""
import os
from datetime import date, timedelta

import streamlit as st

from services.doctor_service import list_specialties, list_doctors
from services.appointment_service import get_available_slots
from views.components import doctor_grid

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_DOCTOR_IMAGE_PATH = os.path.join(_ASSET_ROOT, "doctor.png")

_AVAILABILITY_OPTIONS = ["Any time", "Available today", "Available this week"]


def render():
    st.image(_DOCTOR_IMAGE_PATH, width=140, use_container_width=False)
    st.subheader("Find a doctor")

    specs = list_specialties()
    spec_options = ["All specialties"] + [s["name"] for s in specs]
    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("Search by name", placeholder="e.g. Rao")
    with col2:
        chosen_spec = st.selectbox("Specialty", spec_options)

    specialty_id = None
    if chosen_spec != "All specialties":
        specialty_id = next((s["id"] for s in specs if s["name"] == chosen_spec), None)

    doctors = list_doctors(specialty_id=specialty_id, search=search)

    if not doctors:
        st.info("No doctors match that filter yet.")
        return

    with st.expander("🔍 Filter by availability, fee & experience"):
        doctors = _apply_filters(doctors)

    if not doctors:
        st.info("No doctors match these filters — try widening your range.")
        return

    st.caption(f"{len(doctors)} doctor{'s' if len(doctors) != 1 else ''} found")
    doctor_grid(doctors, columns=2)


def _apply_filters(doctors: list[dict]) -> list[dict]:
    """Renders the three filter controls and returns the subset of
    `doctors` that match all of them. Fee/experience slider bounds are
    derived from the doctors actually on screen, so the sliders are
    always meaningful — never wider than the real data, and never
    invalid (min < max) even on a tiny catalog."""
    fee_min, fee_max = _slider_bounds([d["fee"] for d in doctors], pad=100)
    exp_min, exp_max = _slider_bounds([d["experience_years"] for d in doctors], pad=1)

    c1, c2, c3 = st.columns(3)
    with c1:
        availability = st.selectbox(
            "Availability", _AVAILABILITY_OPTIONS, key="doc_filter_availability",
        )
    with c2:
        fee_range = st.slider(
            "Consultation fee (₹)", fee_min, fee_max, (fee_min, fee_max),
            step=50, key="doc_filter_fee",
        )
    with c3:
        exp_range = st.slider(
            "Experience (years)", exp_min, exp_max, (exp_min, exp_max),
            key="doc_filter_experience",
        )

    filtered = [
        d for d in doctors
        if fee_range[0] <= d["fee"] <= fee_range[1]
        and exp_range[0] <= d["experience_years"] <= exp_range[1]
    ]

    if availability != "Any time":
        window_days = 1 if availability == "Available today" else 7
        filtered = [d for d in filtered if _has_open_slot(d["doctor_id"], window_days)]

    return filtered


def _slider_bounds(values: list, pad: int) -> tuple[int, int]:
    """(min, max) for a range slider from real data, widened by `pad`
    when every value is identical — st.slider requires min < max."""
    values = values or [0]
    lo, hi = int(min(values)), int(max(values))
    return (lo, hi) if hi > lo else (lo, lo + pad)


def _has_open_slot(doctor_id: int, window_days: int) -> bool:
    """True if this doctor has at least one open slot within the next
    `window_days` days (1 = today only). Short-circuits on the first day
    with an opening rather than scanning the whole window — cheap for
    "today" (1 query), and stops early most of the time for "this week"."""
    today = date.today()
    return any(
        get_available_slots(doctor_id, today + timedelta(days=offset))
        for offset in range(window_days)
    )