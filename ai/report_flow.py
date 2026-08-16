"""
ai/report_flow.py
Deterministic, human-confirmed patient-report CRUD for the doctor
chatbot — the doctor-side counterpart to ai/booking_flow.py and
ai/medicine_flow.py. The LLM has no tool that writes a report; it can
only notice intent and best-guess the first medicine line + diagnosis
from free text via a "propose_*" tool in ai/smartcare_agent.py, which
opens this wizard pre-filled. A real doctor then reviews it — adding
more medicine lines, fixing anything the LLM guessed wrong, filling in
vitals — and clicks Save/Delete themselves. Every write still goes
through services/report_service.py, exactly like the doctor portal's
own "🧾 Patient report" tab (views/doctor_portal.py) does; this file
only re-renders that same reviewing UI inline inside the chat.

    IDLE -> CREATE_REVIEW | EDIT_REVIEW | DELETE_REVIEW -> DONE
"""
import streamlit as st
from datetime import datetime

from services import medicine_service, report_service
from services.doctor_service import get_doctor_profile
from services.report_service import ReportError
from views.components import report_preview

STATE_KEY = "report_wizard_state"
DATA_KEY = "report_wizard_data"
RESULT_KEY = "report_wizard_result_msg"

_DOSAGE_OPTIONS = ["1 tablet", "2 tablets", "1 capsule", "2 capsules", "5 ml", "10 ml", "1 injection", "Other (specify)"]
_FREQUENCY_OPTIONS = ["Once daily", "Twice daily", "Three times daily", "Four times daily", "Every 6 hours", "Every 8 hours", "As needed (PRN)", "Other (specify)"]
_DURATION_OPTIONS = ["3 days", "5 days", "7 days", "10 days", "14 days", "1 month", "3 months", "Ongoing", "Other (specify)"]
_INSTRUCTIONS_OPTIONS = ["After food", "Before food", "With food", "Empty stomach", "At bedtime", "As directed by physician", "Other (specify)"]


def open_create(data: dict):
    """data: {patient_id, patient_name, diagnosis, advice_note, draft_item:
    {medicine_id, medicine_name, dosage, frequency, duration, quantity,
    instructions} or None} — set by propose_create_report."""
    st.session_state[STATE_KEY] = "CREATE_REVIEW"
    st.session_state[DATA_KEY] = data
    items = [data["draft_item"]] if data.get("draft_item") else []
    st.session_state["report_wizard_items"] = items


def open_edit(data: dict):
    """data: {prescription_id, patient_name, diagnosis, advice_note}."""
    st.session_state[STATE_KEY] = "EDIT_REVIEW"
    st.session_state[DATA_KEY] = data


def open_delete(data: dict):
    """data: {prescription_id, patient_name}."""
    st.session_state[STATE_KEY] = "DELETE_REVIEW"
    st.session_state[DATA_KEY] = data


def is_active() -> bool:
    return st.session_state.get(STATE_KEY, "IDLE") not in ("IDLE", "DONE")


def render(doctor_id: int):
    state = st.session_state.get(STATE_KEY, "IDLE")
    data = st.session_state.setdefault(DATA_KEY, {})

    with st.container(border=True):
        st.markdown("**🧾 Report assistant**")
        if state == "CREATE_REVIEW":
            _review_create(doctor_id, data)
        elif state == "EDIT_REVIEW":
            _review_edit(data)
        elif state == "DELETE_REVIEW":
            _review_delete(data)
        elif state == "DONE":
            _done()


def _dropdown_or_other(label: str, options: list[str], key: str) -> str:
    choice = st.selectbox(label, options, key=f"{key}_select")
    if choice == "Other (specify)":
        return st.text_input(f"{label} (custom)", key=f"{key}_custom")
    return choice


def _review_create(doctor_id: int, data: dict):
    st.caption(
        f"The assistant prepared this report for **{data.get('patient_name', 'the patient')}** "
        "— review, add more medicines if needed, then save."
    )
    medicines = medicine_service.list_medicines(active_only=True)
    if not medicines:
        st.info("No medicines are in the pharmacy catalog yet.")
        return
    med_by_id = {m["id"]: m for m in medicines}

    items = st.session_state.setdefault("report_wizard_items", [])

    col_input, col_preview = st.columns([1.05, 0.95], gap="large")

    with col_input:
        st.markdown("**Add a medicine line**")
        med_id = st.selectbox(
            "Medicine", list(med_by_id.keys()),
            format_func=lambda i: f"{med_by_id[i]['name']} ({med_by_id[i]['stock_quantity']} in stock)",
            key="rw_med",
        )
        c1, c2 = st.columns(2)
        with c1:
            dosage = _dropdown_or_other("Dosage", _DOSAGE_OPTIONS, "rw_dosage")
        with c2:
            frequency = _dropdown_or_other("Frequency", _FREQUENCY_OPTIONS, "rw_freq")
        c3, c4 = st.columns(2)
        with c3:
            duration = _dropdown_or_other("Duration", _DURATION_OPTIONS, "rw_dur")
        with c4:
            max_stock = med_by_id[med_id]["stock_quantity"]
            quantity = st.number_input(
                "Quantity", 0, max(max_stock, 0), min(1, max_stock) if max_stock else 0,
                step=1, key="rw_qty",
            )
        instructions = _dropdown_or_other("Instructions", _INSTRUCTIONS_OPTIONS, "rw_instr")

        if st.button("➕ Add to report", key="rw_add_line"):
            if quantity <= 0:
                st.warning("Set a quantity greater than zero.")
            else:
                items.append({
                    "medicine_id": med_id, "medicine_name": med_by_id[med_id]["name"],
                    "dosage": dosage, "frequency": frequency, "duration": duration,
                    "quantity": int(quantity), "instructions": instructions,
                })
                st.rerun()

        if items:
            st.markdown("**Medicines on this report**")
            for idx, line in enumerate(items):
                cols = st.columns([5, 1])
                cols[0].markdown(
                    f"- **{line['medicine_name']}** — {line['dosage'] or '—'}, "
                    f"{line['frequency'] or '—'}, {line['duration'] or '—'}, qty {line['quantity']}"
                )
                if cols[1].button("Remove", key=f"rw_rm_{idx}"):
                    items.pop(idx)
                    st.rerun()
        else:
            st.caption("No medicines added yet.")

        diagnosis = st.text_input("Diagnosis", value=data.get("diagnosis", ""), key="rw_diag")
        advice = st.text_area("Advice note", value=data.get("advice_note", ""), key="rw_advice")

        with st.expander("Vitals & clinical notes (optional)"):
            c5, c6, c7, c8 = st.columns(4)
            with c5:
                hr = st.number_input("Heart rate (bpm)", 0, 250, 0, key="rw_hr")
            with c6:
                bp = st.text_input("Blood pressure", placeholder="120/80", key="rw_bp")
            with c7:
                spo2 = st.number_input("SpO₂ (%)", 0, 100, 0, key="rw_spo2")
            with c8:
                ef = st.number_input("Ejection fraction (%)", 0, 100, 0, key="rw_ef")
            ecg_note = st.text_input("ECG note", key="rw_ecg")
            clinical_notes = st.text_area("Clinical notes", key="rw_notes")

        c9, c10 = st.columns(2)
        if c9.button("🧾 Save report", type="primary", key="rw_save"):
            if not items:
                st.error("Add at least one medicine before saving.")
            else:
                try:
                    report_service.create_report(
                        doctor_id=doctor_id, patient_id=data["patient_id"], items=items,
                        diagnosis=diagnosis, advice_note=advice,
                        heart_rate=hr or None, blood_pressure=bp or None,
                        pulse_oximetry=spo2 or None, ejection_fraction=ef or None,
                        ecg_note=ecg_note, clinical_notes=clinical_notes,
                    )
                    st.session_state[RESULT_KEY] = f"Report saved for {data.get('patient_name', 'the patient')}."
                    st.session_state[STATE_KEY] = "DONE"
                    st.session_state.pop("report_wizard_items", None)
                    st.rerun()
                except ReportError as e:
                    st.error(str(e))
        if c10.button("Cancel", key="rw_cancel"):
            _reset()
            st.rerun()

    with col_preview:
        st.markdown("**👁️ Live preview**")
        st.caption("Updates as you edit — exactly what the patient will see and can download.")
        preview = _draft_report_preview(
            doctor_id, data.get("patient_name", "Patient"), items, diagnosis, advice,
            hr, bp, spo2, ef, ecg_note, clinical_notes,
        )
        report_preview(preview, height=620)


def _draft_report_preview(
    doctor_id: int, patient_name: str, items: list, diagnosis: str, advice_note: str,
    heart_rate: int, blood_pressure: str, pulse_oximetry: int, ejection_fraction: int,
    ecg_note: str, clinical_notes: str,
) -> dict:
    """Same dict shape services/report_pdf.render_report_html() expects
    (identical to services/report_service.get_report()'s shape for a REAL
    saved report), built from the wizard's CURRENT, unsaved values —
    preview only, nothing here touches the database. Mirrors
    views/doctor_portal.py's _draft_report_preview() exactly, since both
    forms are meant to produce identical reports."""
    doctor = get_doctor_profile(doctor_id) or {}
    has_vitals = any([
        heart_rate, blood_pressure, pulse_oximetry, ejection_fraction,
        (ecg_note or "").strip(), (clinical_notes or "").strip(),
    ])
    vitals = None
    if has_vitals:
        vitals = {
            "heart_rate": heart_rate or None, "blood_pressure": blood_pressure or None,
            "pulse_oximetry": pulse_oximetry or None, "ejection_fraction": ejection_fraction or None,
            "ecg_note": ecg_note or "", "notes": clinical_notes or "",
        }
    return {
        "created_at": datetime.now(),
        "diagnosis": diagnosis or "",
        "advice_note": advice_note or "",
        "patient_name": patient_name,
        "doctor_name": doctor.get("name", ""),
        "doctor_specialty": doctor.get("specialty", "General Medicine"),
        "items": items,
        "vitals": vitals,
    }


def _review_edit(data: dict):
    prescription_id = data.get("prescription_id")
    full = report_service.get_report(prescription_id) if prescription_id else None
    if not full:
        st.error("Couldn't find that report anymore.")
        if st.button("Close", key="rw_edit_close_missing"):
            _reset()
            st.rerun()
        return

    st.caption(
        f"The assistant matched this to a report for **{full['patient_name']}** "
        f"from {full['created_at'].strftime('%d %b %Y')} — review before saving."
    )
    st.caption("Medicine lines can't be edited here — delete and ask the assistant to create a new report if they need to change.")

    diagnosis = st.text_input("Diagnosis", value=data.get("diagnosis") or full["diagnosis"], key="rw_edit_diag")
    advice = st.text_area("Advice note", value=data.get("advice_note") or full["advice_note"], key="rw_edit_advice")

    vitals = full.get("vitals") or {}
    with st.expander("Vitals & clinical notes", expanded=bool(vitals)):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            hr = st.number_input("Heart rate (bpm)", 0, 250, vitals.get("heart_rate") or 0, key="rw_edit_hr")
        with c2:
            bp = st.text_input("Blood pressure", value=vitals.get("blood_pressure") or "", key="rw_edit_bp")
        with c3:
            spo2 = st.number_input("SpO₂ (%)", 0, 100, vitals.get("pulse_oximetry") or 0, key="rw_edit_spo2")
        with c4:
            ef = st.number_input("Ejection fraction (%)", 0, 100, vitals.get("ejection_fraction") or 0, key="rw_edit_ef")
        ecg_note = st.text_input("ECG note", value=vitals.get("ecg_note") or "", key="rw_edit_ecg")
        clinical_notes = st.text_area("Clinical notes", value=vitals.get("notes") or "", key="rw_edit_notes")

    c5, c6 = st.columns(2)
    if c5.button("💾 Save changes", type="primary", key="rw_edit_save"):
        report_service.update_report(
            prescription_id, diagnosis=diagnosis, advice_note=advice,
            heart_rate=hr or None, blood_pressure=bp or None,
            pulse_oximetry=spo2 or None, ejection_fraction=ef or None,
            ecg_note=ecg_note, clinical_notes=clinical_notes,
        )
        st.session_state[RESULT_KEY] = f"Report updated for {full['patient_name']}."
        st.session_state[STATE_KEY] = "DONE"
        st.rerun()
    if c6.button("Cancel", key="rw_edit_cancel"):
        _reset()
        st.rerun()


def _review_delete(data: dict):
    prescription_id = data.get("prescription_id")
    full = report_service.get_report(prescription_id) if prescription_id else None
    if not full:
        st.error("Couldn't find that report anymore — it may already be deleted.")
        if st.button("Close", key="rw_del_close_missing"):
            _reset()
            st.rerun()
        return

    st.warning(
        f"Delete the report for **{full['patient_name']}** from "
        f"{full['created_at'].strftime('%d %b %Y')} ({full['diagnosis'] or 'no diagnosis on file'})? "
        "This restores any stock it deducted and cannot be undone."
    )
    c1, c2 = st.columns(2)
    if c1.button("🗑 Confirm delete", type="primary", key="rw_del_confirm"):
        try:
            report_service.delete_report(prescription_id)
            st.session_state[RESULT_KEY] = f"Report deleted for {full['patient_name']}. Stock restored."
            st.session_state[STATE_KEY] = "DONE"
            st.rerun()
        except ReportError as e:
            st.error(str(e))
    if c2.button("Cancel", key="rw_del_cancel"):
        _reset()
        st.rerun()


def _done():
    st.success(st.session_state.get(RESULT_KEY, "Done."))
    if st.button("Close", key="rw_close"):
        _reset()
        st.rerun()


def _reset():
    st.session_state[STATE_KEY] = "IDLE"
    st.session_state[DATA_KEY] = {}
    st.session_state.pop("report_wizard_items", None)