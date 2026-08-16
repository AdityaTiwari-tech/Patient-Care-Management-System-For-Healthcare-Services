"""
views/doctors_view.py
Browse the hospital's doctors by specialty / experience, shown as a card
grid — used inside both the patient and doctor dashboards' "Doctors" tab.
"""
import os

import streamlit as st

from services.doctor_service import list_specialties, list_doctors
from views.components import doctor_grid

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_DOCTOR_IMAGE_PATH = os.path.join(_ASSET_ROOT, "doctor.png")


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

    doctor_grid(doctors, columns=2)