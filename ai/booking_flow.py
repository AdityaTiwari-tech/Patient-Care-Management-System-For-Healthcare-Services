"""
ai/booking_flow.py
The deterministic booking wizard — the ONLY code path in the whole chat
surface that writes an appointment. The LLM has no book_appointment tool
and never sees a "confirm" button; it can only notice booking intent and
flip a signal (see smartcare_agent.start_booking_flow), which
chatbot_view.py turns into a call to render() below.

Reads can be wrong — that's just a bad answer. Writes put a wrong row in
a real clinic's database, so this is a plain state machine with real
Streamlit widgets, not a conversation:

    IDLE -> ASK_CONDITION -> PICK_DOCTOR -> PICK_SLOT -> CONFIRM -> DONE

Two things this buys us that a chat-only flow can't:
  1. The "3pm" bug is structurally impossible — the time dropdown is only
     ever populated from get_available_slots(), so the model (or a typo)
     can't put an invented time into the database.
  2. commit() re-checks the slot is still free immediately before writing,
     in case someone else took it while this patient sat on the confirm
     screen.
"""
from datetime import date, timedelta
import streamlit as st

from services.doctor_service import list_specialties, list_doctors
from services.appointment_service import (
    book_appointment, get_available_slots, AppointmentError,
)
from ai.llm import get_llm, is_configured

STATE_KEY = "booking_wizard_state"
DATA_KEY = "booking_wizard_data"


def start():
    """Called by chatbot_view.py when the agent's signals dict comes back
    with start_booking=True — resets the wizard to its first step."""
    st.session_state[STATE_KEY] = "ASK_CONDITION"
    st.session_state[DATA_KEY] = {}


def is_active() -> bool:
    return st.session_state.get(STATE_KEY, "IDLE") not in ("IDLE", "DONE")


def suggest_specialty(condition_text: str) -> str:
    """
    The one place in the booking flow the LLM genuinely helps: mapping
    free text ("persistent cough and mild fever") onto a specialty name.
    This is real semantic classification, not a database lookup — and
    it only pre-selects a dropdown the patient can freely override, so a
    wrong guess here costs nothing.
    """
    specs = [s["name"] for s in list_specialties()]
    if not specs:
        return "General Medicine"
    if not condition_text or not is_configured():
        return specs[0]
    try:
        llm = get_llm()
        prompt = (
            f"Specialties: {', '.join(specs)}.\n"
            f'Patient description: "{condition_text}"\n'
            "Reply with ONLY the single best matching specialty name from "
            "the list above, nothing else."
        )
        reply = llm.invoke(prompt).content.strip()
        for s in specs:
            if s.lower() in reply.lower():
                return s
    except Exception:
        pass
    return specs[0]


def render(patient_id: int):
    """Draws whichever step the wizard is currently on. chatbot_view.py
    calls this once per rerun, right after the chat history, whenever
    is_active() is True."""
    state = st.session_state.get(STATE_KEY, "IDLE")
    data = st.session_state.setdefault(DATA_KEY, {})

    with st.container(border=True):
        st.markdown("**📅 Booking assistant**")

        if state == "ASK_CONDITION":
            _step_condition(data)
        elif state == "PICK_DOCTOR":
            _step_doctor(data)
        elif state == "PICK_SLOT":
            _step_slot(data)
        elif state == "CONFIRM":
            _step_confirm(patient_id, data)
        elif state == "DONE":
            _step_done()


def _step_condition(data: dict):
    condition = st.text_area(
        "What's this visit for?",
        placeholder="e.g. persistent cough and mild fever",
        key="booking_condition_input",
    )
    if st.button("Continue", type="primary", key="booking_step1_continue"):
        data["condition"] = condition
        data["suggested_specialty"] = suggest_specialty(condition)
        st.session_state[STATE_KEY] = "PICK_DOCTOR"
        st.rerun()


def _step_doctor(data: dict):
    specs = list_specialties()
    spec_names = [s["name"] for s in specs]
    default_idx = (
        spec_names.index(data["suggested_specialty"])
        if data.get("suggested_specialty") in spec_names else 0
    )
    chosen_spec = st.selectbox("Specialty", spec_names, index=default_idx, key="booking_spec_select")
    specialty_id = next((s["id"] for s in specs if s["name"] == chosen_spec), None)

    doctors = list_doctors(specialty_id=specialty_id)
    if not doctors:
        st.warning("No doctors in that specialty yet.")
    else:
        doc_labels = [f"Dr. {d['name']} — ₹{d['fee']:.0f} · {d['experience_years']}y" for d in doctors]
        idx = st.selectbox(
            "Doctor", range(len(doctors)), format_func=lambda i: doc_labels[i],
            key="booking_doc_select",
        )
        if st.button("Continue", type="primary", key="booking_step2_continue"):
            data["doctor"] = doctors[idx]
            st.session_state[STATE_KEY] = "PICK_SLOT"
            st.rerun()

    if st.button("Back", key="booking_step2_back"):
        st.session_state[STATE_KEY] = "ASK_CONDITION"
        st.rerun()


def _step_slot(data: dict):
    doctor = data["doctor"]
    chosen_date = st.date_input(
        "Date", value=date.today() + timedelta(days=1), min_value=date.today(),
        key="booking_date_input",
    )
    available = get_available_slots(doctor["doctor_id"], chosen_date)
    if not available:
        st.warning("No open slots that day — try another date.")
    else:
        idx = st.selectbox(
            "Time", range(len(available)),
            format_func=lambda i: available[i].strftime("%I:%M %p"),
            key="booking_slot_select",
        )
        if st.button("Continue", type="primary", key="booking_step3_continue"):
            data["date"] = chosen_date
            data["slot"] = available[idx]
            st.session_state[STATE_KEY] = "CONFIRM"
            st.rerun()

    if st.button("Back", key="booking_step3_back"):
        st.session_state[STATE_KEY] = "PICK_DOCTOR"
        st.rerun()


def _step_confirm(patient_id: int, data: dict):
    doctor = data["doctor"]
    st.markdown(
        f"**Dr. {doctor['name']}** — {doctor['specialty']}  \n"
        f"{data['date'].strftime('%A, %d %B %Y')} at {data['slot'].strftime('%I:%M %p')}  \n"
        f"Reason: {data.get('condition') or '—'}"
    )
    c1, c2 = st.columns(2)
    if c1.button("Confirm booking", type="primary", key="booking_confirm"):
        ok, msg = commit(patient_id, data)
        st.session_state["booking_result_msg"] = msg
        st.session_state[STATE_KEY] = "DONE" if ok else "CONFIRM"
        if not ok:
            st.session_state[STATE_KEY] = "PICK_SLOT"  # slot taken — send them back to pick again
        st.rerun()
    if c2.button("Back", key="booking_step4_back"):
        st.session_state[STATE_KEY] = "PICK_SLOT"
        st.rerun()


def _step_done():
    st.success(st.session_state.get("booking_result_msg", "Booked."))
    if st.button("Close", key="booking_close"):
        st.session_state[STATE_KEY] = "IDLE"
        st.session_state[DATA_KEY] = {}
        st.rerun()


def commit(patient_id: int, data: dict) -> tuple[bool, str]:
    """
    The single write in the entire chat surface. Re-validates the slot is
    still free right before booking, since someone may have taken it
    while this patient sat on the confirm screen — then writes with
    source="chatbot" so it's clear in the data which bookings came from
    the assistant versus the web form.
    """
    doctor = data["doctor"]
    still_free = get_available_slots(doctor["doctor_id"], data["date"])
    if data["slot"] not in still_free:
        return False, "That slot was just taken by someone else — please pick another time."
    try:
        book_appointment(
            patient_id=patient_id, doctor_id=doctor["doctor_id"],
            scheduled_date=data["date"], start_time=data["slot"],
            reason=data.get("condition", ""), source="chatbot",
        )
        return True, (
            f"Booked with Dr. {doctor['name']} on "
            f"{data['date'].strftime('%d %b %Y')} at {data['slot'].strftime('%I:%M %p')}."
        )
    except AppointmentError as e:
        return False, str(e)

