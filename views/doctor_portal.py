"""
views/doctor_portal.py
Doctor dashboard: profile header + KPIs, weekly schedule grid, appointment
management, analytics, patient conditions/prescribing, and availability —
routed via the left-side nav.
"""
import os
from datetime import date, time as time_cls, datetime, timedelta
import pandas as pd
import streamlit as st

from services.auth_service import get_doctor_id_for_user
from services.appointment_service import (
    list_doctor_appointments, update_appointment_status,
)
from services.doctor_service import (
    get_doctor_profile, get_weekly_slots, add_slot, deactivate_slot, DAY_NAMES, list_specialties,
)
from services.health_service import add_health_record, get_patient_records
from services import medicine_service, prescription_service, report_service
from services.prescription_service import PrescriptionError
from services.report_service import ReportError
from services import report_pdf
from views import chatbot_view, doctors_view
from views.components import button_tabs, ecg_divider, report_preview, status_badge, kpi_tile, set_page_background

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_DOCTOR_IMAGE_PATH = os.path.join(_ASSET_ROOT, "doctor.png")


def render(user, section: str = "Doctor Portal"):
    set_page_background(section)
    doctor_id = get_doctor_id_for_user(user.id)
    if not doctor_id:
        st.error("Your doctor profile could not be found. Contact the admin.")
        return

    if section == "Doctor Portal":
        _profile_header(doctor_id, user)
        all_appts = [a for a in list_doctor_appointments(doctor_id) if a["status"] != "cancelled"]
        _kpi_row(all_appts)

        selected = button_tabs(["Schedule", "Appointments", "Analytics", "Patient conditions"], key="doctor_portal")
        if selected == "Schedule":
            _week_schedule(all_appts)
            with st.expander("Manage weekly availability"):
                _availability(doctor_id)
        elif selected == "Appointments":
            _appointments_list(list_doctor_appointments(doctor_id))
        elif selected == "Analytics":
            _analytics(all_appts)
        elif selected == "Patient conditions":
            _patient_conditions(doctor_id, all_appts)

    elif section == "Doctors":
        st.markdown(f"### Dr. {user.full_name.split()[-1]}'s dashboard")
        ecg_divider()
        doctors_view.render()


    elif section == "Smart Care AI":
        st.markdown(f"### Dr. {user.full_name.split()[-1]}'s dashboard")
        ecg_divider()
        chatbot_view.render(user)


def _profile_header(doctor_id: int, user):
    profile = get_doctor_profile(doctor_id)
    with st.container(border=True):
        st.image(_DOCTOR_IMAGE_PATH, width=120, use_container_width=False)
        specialty = profile["specialty"] if profile else "General Medicine"
        experience = profile["experience_years"] if profile else 0
        fee = profile["fee"] if profile else 0
        st.markdown(
            f"""<p class="doc-profile-name">Dr. {user.full_name}</p>
            <p class="doc-profile-meta">{specialty} &middot; {experience} yrs experience &middot; ₹{fee:.0f} per consultation</p>""",
            unsafe_allow_html=True,
        )
    ecg_divider()


def _kpi_row(appts: list[dict]):
    today = date.today()
    today_appts = [a for a in appts if a["date"] == today]
    upcoming = [a for a in appts if a["date"] > today and a["status"] in ("pending", "confirmed")]
    completed = [a for a in appts if a["status"] == "completed"]
    unique_patients = {a["patient_id"] for a in appts}

    c1, c2, c3, c4 = st.columns(4)
    kpi_tile("Today", len(today_appts), c1, caption="nothing booked" if not today_appts else None)
    kpi_tile("Upcoming", len(upcoming), c2, dark=True)
    kpi_tile("Completed", len(completed), c3, dark=True)
    kpi_tile("Unique patients", len(unique_patients), c4, dark=True)


def _week_schedule(appts: list[dict]):
    if "doc_week_ref" not in st.session_state:
        today = date.today()
        st.session_state.doc_week_ref = today - timedelta(days=today.weekday())
    week_start = st.session_state.doc_week_ref

    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    if c1.button("◀", key="week_prev"):
        st.session_state.doc_week_ref = week_start - timedelta(days=7)
        st.rerun()
    if c2.button("Today", key="week_today"):
        today = date.today()
        st.session_state.doc_week_ref = today - timedelta(days=today.weekday())
        st.rerun()
    if c3.button("▶", key="week_next"):
        st.session_state.doc_week_ref = week_start + timedelta(days=7)
        st.rerun()
    week_end = week_start + timedelta(days=6)
    c4.markdown(f"**{week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}**")

    days = [week_start + timedelta(days=i) for i in range(7)]
    today = date.today()

    by_day_hour = {}
    for a in appts:
        if week_start <= a["date"] <= week_end:
            by_day_hour.setdefault((a["date"], a["start_time"].hour), []).append(a)

    header_parts = ['<div class="week-cell week-header"></div>']
    for d in days:
        cls = "week-cell week-header is-today" if d == today else "week-cell week-header"
        header_parts.append(f'<div class="{cls}">{d.strftime("%a")} {d.strftime("%d %b")}</div>')

    row_parts = []
    for hour in range(8, 19):
        row_parts.append(f'<div class="week-cell week-hour-label">{time_cls(hour, 0).strftime("%I %p")}</div>')
        for d in days:
            entries = sorted(by_day_hour.get((d, hour), []), key=lambda a: a["start_time"])
            entry_html = "".join(
                f'<div class="week-entry">{a["start_time"].strftime("%H:%M")}'
                f'-{_plus_30(a["start_time"]).strftime("%H:%M")} {a["patient_name"]}</div>'
                for a in entries
            )
            row_parts.append(f'<div class="week-cell">{entry_html}</div>')

    grid_html = "".join(header_parts) + "".join(row_parts)
    st.markdown(f'<div class="week-grid">{grid_html}</div>', unsafe_allow_html=True)


def _plus_30(t: time_cls) -> time_cls:
    return (datetime.combine(date.today(), t) + timedelta(minutes=30)).time()


def _appointments_list(appts: list[dict]):
    if not appts:
        st.info("No appointments booked yet.")
        return

    for a in sorted(appts, key=lambda x: (x["date"], x["start_time"]), reverse=True):
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.4, 1.2])
            with c1:
                st.markdown(f"**{a['patient_name']}**")
                st.caption(f"{a['date'].strftime('%d %b %Y')} at {a['start_time'].strftime('%I:%M %p')}")
                if a["reason"]:
                    st.caption(f"Reason: {a['reason']}")
            with c2:
                st.markdown(status_badge(a["status"]), unsafe_allow_html=True)
            with c3:
                new_status = st.selectbox(
                    "Update", ["pending", "confirmed", "completed", "cancelled"],
                    index=["pending", "confirmed", "completed", "cancelled"].index(a["status"]),
                    key=f"status_{a['id']}", label_visibility="collapsed",
                )
                if new_status != a["status"] and st.button("Save", key=f"save_{a['id']}"):
                    update_appointment_status(a["id"], new_status)
                    st.rerun()


def _analytics(appts: list[dict]):
    if not appts:
        st.info("Analytics will appear once you have appointments.")
        return

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Appointments — last 14 days**")
        start = date.today() - timedelta(days=13)
        dates = [start + timedelta(days=i) for i in range(14)]
        counts = {d: 0 for d in dates}
        for a in appts:
            if a["date"] in counts:
                counts[a["date"]] += 1
        df = pd.DataFrame({"date": dates, "appointments": [counts[d] for d in dates]}).set_index("date")
        st.area_chart(df)

    with col2:
        st.markdown("**Status breakdown**")
        status_counts = {}
        for a in appts:
            status_counts[a["status"]] = status_counts.get(a["status"], 0) + 1
        df2 = pd.DataFrame(
            {"status": list(status_counts.keys()), "count": list(status_counts.values())}
        ).set_index("status")
        st.bar_chart(df2)


def _patient_conditions(doctor_id: int, appts: list[dict]):
    if not appts:
        st.info("Patient conditions will appear once you have patients booked.")
        return

    unique_patients = {a["patient_id"]: a["patient_name"] for a in appts}

    st.markdown("**Your patients**")
    for pid, pname in unique_patients.items():
        records = get_patient_records(pid)
        latest = records[0] if records else None
        with st.container(border=True):
            st.markdown(f"**{pname}**")
            if latest and latest["diagnosis"]:
                st.caption(f"Latest condition: {latest['diagnosis']} ({latest['recorded_at'].strftime('%d %b %Y')})")
            else:
                st.caption("No diagnosis on file yet.")

    st.markdown("---")
    patient_id = st.selectbox(
        "Select a patient", list(unique_patients.keys()),
        format_func=lambda pid: unique_patients[pid], key="pc_patient_select",
    )
    patient_name = unique_patients[patient_id]

    with st.expander("Patient's recent records", expanded=False):
        records = get_patient_records(patient_id)
        if not records:
            st.caption("No prior records.")
        for r in records[:5]:
            st.caption(
                f"{r['recorded_at'].strftime('%d %b %Y')} — "
                f"HR {r['heart_rate'] or '—'} bpm, BP {r['blood_pressure'] or '—'}, "
                f"Diagnosis: {r['diagnosis'] or '—'}"
            )

    selected = button_tabs(
        ["Prescribe medicine", "Clinical note & vitals", "🧾 Patient report", "Past prescriptions"],
        key=f"doctor_patient_tabs_{patient_id}",
    )
    if selected == "Prescribe medicine":
        _prescribe_medicines(doctor_id, patient_id, patient_name)
    elif selected == "Clinical note & vitals":
        _clinical_note(doctor_id, patient_id, patient_name)
    elif selected == "🧾 Patient report":
        _patient_report(doctor_id, patient_id, patient_name)
    elif selected == "Past prescriptions":
        _past_prescriptions(doctor_id, patient_id)


def _specialty_filtered_medicines(doctor_id: int, key_prefix: str) -> list[dict]:
    """Narrows the medicine dropdown to one specialty before picking a
    medicine — defaults to the doctor's OWN specialty (from their
    profile), since prescribing within their own field is the common
    case, but the dropdown still reaches every specialty (and "General")
    for cross-specialty prescribing. Mirrors the same specialty filter
    already on the admin catalog and patient pharmacy shop."""
    profile = get_doctor_profile(doctor_id)
    own_specialty = profile["specialty"] if profile else None

    specs = list_specialties()
    spec_options = ["All specialties", "General"] + [s["name"] for s in specs]
    id_by_name = {s["name"]: s["id"] for s in specs}

    default_index = spec_options.index(own_specialty) if own_specialty in spec_options else 0
    chosen_spec = st.selectbox(
        "Specialty", spec_options, index=default_index, key=f"{key_prefix}_spec_filter",
    )

    specialty_id = id_by_name.get(chosen_spec) if chosen_spec not in ("All specialties", "General") else None
    medicines = medicine_service.list_medicines(active_only=True, specialty_id=specialty_id)
    if chosen_spec == "General":
        medicines = [m for m in medicines if m["specialty_id"] is None]
    return medicines


def _prescribe_medicines(doctor_id: int, patient_id: int, patient_name: str):
    """Structured prescription: build one-or-more medicine lines from the
    admin's catalog, then save. Saving reduces each medicine's stock."""
    medicines = _specialty_filtered_medicines(doctor_id, f"rx_{patient_id}")
    if not medicines:
        st.info(
            "No medicines match this specialty yet. Try a different specialty above, "
            "or ask an admin to add medicines under the Medications section."
        )
        return

    med_by_id = {m["id"]: m for m in medicines}

    # Draft lines live in session state, keyed per patient so switching
    # patients doesn't carry lines across.
    draft_store = st.session_state.setdefault("rx_draft", {})
    draft = draft_store.setdefault(patient_id, [])

    st.markdown(f"**New prescription for {patient_name}**")

    # --- Add a medicine line ------------------------------------------------
    med_id = st.selectbox(
        "Medicine", list(med_by_id.keys()),
        format_func=lambda i: (
            f"{med_by_id[i]['name']} "
            f"({med_by_id[i]['stock_quantity']} in stock)"
            + ("  ⚠️ low" if med_by_id[i]["low_stock"] else "")
        ),
        key=f"rx_med_{patient_id}",
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        dosage = st.text_input("Dosage", placeholder="1 tablet", key=f"rx_dosage_{patient_id}")
    with c2:
        frequency = st.text_input("Frequency", placeholder="Twice daily", key=f"rx_freq_{patient_id}")
    with c3:
        duration = st.text_input("Duration", placeholder="7 days", key=f"rx_dur_{patient_id}")
    c4, c5 = st.columns([1, 3])
    with c4:
        max_stock = med_by_id[med_id]["stock_quantity"]
        quantity = st.number_input(
            "Quantity", 0, max(max_stock, 0), min(1, max_stock) if max_stock else 0,
            step=1, key=f"rx_qty_{patient_id}",
            help="Units to dispense — reduces stock when saved.",
        )
    with c5:
        instructions = st.text_input(
            "Instructions", placeholder="After food", key=f"rx_instr_{patient_id}"
        )

    if st.button("➕ Add to prescription", key=f"rx_add_{patient_id}"):
        if quantity <= 0:
            st.warning("Set a quantity greater than zero.")
        else:
            draft.append({
                "medicine_id": med_id,
                "medicine_name": med_by_id[med_id]["name"],
                "dosage": dosage, "frequency": frequency, "duration": duration,
                "quantity": int(quantity), "instructions": instructions,
            })
            st.rerun()

    # --- Current draft lines ------------------------------------------------
    if draft:
        st.markdown("**Medicines on this prescription**")
        for idx, line in enumerate(draft):
            cols = st.columns([5, 1])
            cols[0].markdown(
                f"- **{line['medicine_name']}** — {line['dosage'] or '—'}, "
                f"{line['frequency'] or '—'}, {line['duration'] or '—'}, "
                f"qty {line['quantity']}"
                + (f" · _{line['instructions']}_" if line["instructions"] else "")
            )
            if cols[1].button("Remove", key=f"rx_rm_{patient_id}_{idx}"):
                draft.pop(idx)
                st.rerun()
    else:
        st.caption("No medicines added yet.")

    # --- Diagnosis + advice + save -----------------------------------------
    diagnosis = st.text_input("Diagnosis", placeholder="e.g. Stable angina", key=f"rx_diag_{patient_id}")
    advice = st.text_area(
        "Advice note", placeholder="e.g. Reduce salt, walk 30 min daily, review in 2 weeks.",
        key=f"rx_advice_{patient_id}",
    )

    if st.button("💊 Save prescription", type="primary", key=f"rx_save_{patient_id}"):
        if not draft:
            st.error("Add at least one medicine before saving.")
            return
        try:
            prescription_service.create_prescription(
                patient_id=patient_id, doctor_id=doctor_id,
                items=draft, diagnosis=diagnosis, advice_note=advice,
            )
            draft_store[patient_id] = []  # clear the draft
            st.success(f"Prescription saved for {patient_name}. Stock updated.")
            st.rerun()
        except PrescriptionError as e:
            st.error(str(e))


def _clinical_note(doctor_id: int, patient_id: int, patient_name: str):
    """Free-text clinical note + optional vitals — written to health_records,
    which drives the patient's vitals trend charts."""
    with st.form(f"clinical_note_form_{patient_id}"):
        st.markdown("**Clinical note & vitals**")
        diagnosis = st.text_input("Diagnosis", placeholder="e.g. Stable angina")
        note = st.text_area(
            "Notes", placeholder="Observations, plan, non-catalog instructions…"
        )
        st.markdown("**Vitals captured this visit (optional)**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            hr = st.number_input("Heart rate (bpm)", 0, 250, 0)
        with c2:
            bp = st.text_input("Blood pressure", placeholder="120/80")
        with c3:
            spo2 = st.number_input("SpO₂ (%)", 0, 100, 0)
        with c4:
            ef = st.number_input("Ejection fraction (%)", 0, 100, 0)
        ecg_note = st.text_input("ECG note", placeholder="e.g. Normal sinus rhythm")

        submitted = st.form_submit_button("Save clinical note", type="primary")

    if submitted:
        if not diagnosis and not note and not (hr or bp or spo2 or ef or ecg_note):
            st.error("Add a diagnosis, a note, or at least one vital.")
            return
        add_health_record(
            patient_id=patient_id, doctor_id=doctor_id,
            heart_rate=hr or None, blood_pressure=bp or None,
            pulse_oximetry=spo2 or None, ejection_fraction=ef or None,
            ecg_note=ecg_note, diagnosis=diagnosis, notes=note,
        )
        st.success(f"Clinical note saved for {patient_name}.")
        st.rerun()


# ---------------------------------------------------------------------------
# Patient report: a single saved document combining the medicines table
# (fixed columns) with vitals/clinical notes, diagnosis, and advice note —
# exportable to PDF from the patient's dashboard. See services/report_service.py
# and services/report_pdf.py for the CRUD and rendering logic; this section
# is UI only.
# ---------------------------------------------------------------------------

_DOSAGE_OPTIONS = ["1 tablet", "2 tablets", "1 capsule", "2 capsules", "5 ml", "10 ml", "1 injection", "Other (specify)"]
_FREQUENCY_OPTIONS = ["Once daily", "Twice daily", "Three times daily", "Four times daily", "Every 6 hours", "Every 8 hours", "As needed (PRN)", "Other (specify)"]
_DURATION_OPTIONS = ["3 days", "5 days", "7 days", "10 days", "14 days", "1 month", "3 months", "Ongoing", "Other (specify)"]
_INSTRUCTIONS_OPTIONS = ["After food", "Before food", "With food", "Empty stomach", "At bedtime", "As directed by physician", "Other (specify)"]


def _dropdown_or_other(label: str, options: list[str], key: str) -> str:
    """A preset dropdown with an 'Other (specify)' escape hatch that reveals
    a free-text field — used for dosage/frequency/duration/instructions so
    reports stay consistent without losing the ability to enter something
    not on the list."""
    choice = st.selectbox(label, options, key=f"{key}_select")
    if choice == "Other (specify)":
        return st.text_input(f"{label} (custom)", key=f"{key}_custom")
    return choice


def _patient_report(doctor_id: int, patient_id: int, patient_name: str):
    st.markdown(f"**Patient report for {patient_name}**")
    st.caption(
        "Combines the medicines table with this visit's vitals, diagnosis and advice "
        "into one document the patient can download as a PDF."
    )
    existing = prescription_service.list_by_doctor(doctor_id, patient_id=patient_id)
    with st.expander("➕ Create a new report", expanded=not existing):
        _create_report_form(doctor_id, patient_id, patient_name)

    st.markdown("---")
    st.markdown("**Saved reports**")
    _report_list(doctor_id, patient_id, patient_name, existing)


def _create_report_form(doctor_id: int, patient_id: int, patient_name: str):
    medicines = _specialty_filtered_medicines(doctor_id, f"report_{patient_id}")
    if not medicines:
        st.info(
            "No medicines match this specialty yet. Try a different specialty above, "
            "or ask an admin to add medicines under the Medications section."
        )
        return
    med_by_id = {m["id"]: m for m in medicines}

    draft_store = st.session_state.setdefault("report_draft", {})
    draft = draft_store.setdefault(patient_id, [])

    col_input, col_preview = st.columns([1.05, 0.95], gap="large")

    with col_input:
        st.markdown("**Add a medicine line**")
        med_id = st.selectbox(
            "Medicine", list(med_by_id.keys()),
            format_func=lambda i: (
                f"{med_by_id[i]['name']} ({med_by_id[i]['stock_quantity']} in stock)"
                + ("  ⚠️ low" if med_by_id[i]["low_stock"] else "")
            ),
            key=f"report_med_{patient_id}",
        )
        c1, c2 = st.columns(2)
        with c1:
            dosage = _dropdown_or_other("Dosage", _DOSAGE_OPTIONS, f"report_dosage_{patient_id}")
        with c2:
            frequency = _dropdown_or_other("Frequency", _FREQUENCY_OPTIONS, f"report_freq_{patient_id}")
        c3, c4 = st.columns(2)
        with c3:
            duration = _dropdown_or_other("Duration", _DURATION_OPTIONS, f"report_dur_{patient_id}")
        with c4:
            max_stock = med_by_id[med_id]["stock_quantity"]
            quantity = st.number_input(
                "Quantity", 0, max(max_stock, 0), min(1, max_stock) if max_stock else 0,
                step=1, key=f"report_qty_{patient_id}",
                help="Units to dispense — reduces stock when saved.",
            )
        instructions = _dropdown_or_other("Instructions", _INSTRUCTIONS_OPTIONS, f"report_instr_{patient_id}")

        if st.button("➕ Add to report", key=f"report_add_{patient_id}"):
            if quantity <= 0:
                st.warning("Set a quantity greater than zero.")
            else:
                draft.append({
                    "medicine_id": med_id, "medicine_name": med_by_id[med_id]["name"],
                    "dosage": dosage, "frequency": frequency, "duration": duration,
                    "quantity": int(quantity), "instructions": instructions,
                })
                st.rerun()

        if draft:
            st.markdown("**Medicines on this report**")
            for idx, line in enumerate(draft):
                cols = st.columns([5, 1])
                cols[0].markdown(
                    f"- **{line['medicine_name']}** — {line['dosage'] or '—'}, "
                    f"{line['frequency'] or '—'}, {line['duration'] or '—'}, qty {line['quantity']}"
                    + (f" · _{line['instructions']}_" if line["instructions"] else "")
                )
                if cols[1].button("Remove", key=f"report_rm_{patient_id}_{idx}"):
                    draft.pop(idx)
                    st.rerun()
        else:
            st.caption("No medicines added yet.")

        st.markdown("**Diagnosis & advice**")
        diagnosis = st.text_input("Diagnosis", placeholder="e.g. Stable angina", key=f"report_diag_{patient_id}")
        advice = st.text_area(
            "Advice note", placeholder="e.g. Reduce salt, walk 30 min daily, review in 2 weeks.",
            key=f"report_advice_{patient_id}",
        )

        st.markdown("**Clinical notes & vitals (optional)**")
        c5, c6, c7, c8 = st.columns(4)
        with c5:
            hr = st.number_input("Heart rate (bpm)", 0, 250, 0, key=f"report_hr_{patient_id}")
        with c6:
            bp = st.text_input("Blood pressure", placeholder="120/80", key=f"report_bp_{patient_id}")
        with c7:
            spo2 = st.number_input("SpO₂ (%)", 0, 100, 0, key=f"report_spo2_{patient_id}")
        with c8:
            ef = st.number_input("Ejection fraction (%)", 0, 100, 0, key=f"report_ef_{patient_id}")
        ecg_note = st.text_input("ECG note", placeholder="e.g. Normal sinus rhythm", key=f"report_ecg_{patient_id}")
        clinical_notes = st.text_area(
            "Clinical notes", placeholder="Observations, plan, anything else for the record…",
            key=f"report_notes_{patient_id}",
        )

        if st.button("🧾 Save report", type="primary", key=f"report_save_{patient_id}"):
            if not draft:
                st.error("Add at least one medicine before saving the report.")
                return
            try:
                report_service.create_report(
                    doctor_id=doctor_id, patient_id=patient_id, items=draft,
                    diagnosis=diagnosis, advice_note=advice,
                    heart_rate=hr or None, blood_pressure=bp or None,
                    pulse_oximetry=spo2 or None, ejection_fraction=ef or None,
                    ecg_note=ecg_note, clinical_notes=clinical_notes,
                )
                draft_store[patient_id] = []
                st.success(f"Report saved for {patient_name}. Stock updated.")
                st.rerun()
            except ReportError as e:
                st.error(str(e))

    with col_preview:
        st.markdown("**👁️ Live preview**")
        st.caption("Updates as you fill in the form — this is exactly what the patient will see and can download.")
        preview = _draft_report_preview(
            doctor_id, patient_name, draft, diagnosis, advice,
            hr, bp, spo2, ef, ecg_note, clinical_notes,
        )
        report_preview(preview, height=640)


def _draft_report_preview(
    doctor_id: int, patient_name: str, items: list[dict], diagnosis: str, advice_note: str,
    heart_rate: int, blood_pressure: str, pulse_oximetry: int, ejection_fraction: int,
    ecg_note: str, clinical_notes: str,
) -> dict:
    """Builds the exact dict shape services/report_pdf.render_report_html()
    expects (the same shape services/report_service.get_report() returns
    for a REAL saved report) from the create-report form's CURRENT,
    unsaved values. Preview only — nothing here touches the database;
    _create_report_form's own Save button is still the only thing that
    actually writes a report."""
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


def _report_list(doctor_id: int, patient_id: int, patient_name: str, reports: list[dict]):
    if not reports:
        st.caption("No reports for this patient yet.")
        return

    for rx in reports:
        with st.container(border=True):
            st.markdown(f"**{rx['diagnosis'] or 'Report'}** &middot; {rx['created_at'].strftime('%d %b %Y, %I:%M %p')}")
            meds_summary = ", ".join(it["medicine_name"] for it in rx["items"])
            st.caption(meds_summary or "No medicines")

            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("⬇️ Download PDF", key=f"report_dl_{rx['id']}"):
                    st.session_state[f"report_download_{rx['id']}"] = True
                    st.rerun()
            with c2:
                edit_open = st.session_state.get(f"report_edit_open_{rx['id']}", False)
                if st.button("✏️ Edit" if not edit_open else "✏️ Close edit", key=f"report_edit_toggle_{rx['id']}"):
                    st.session_state[f"report_edit_open_{rx['id']}"] = not edit_open
                    st.rerun()
            with c3:
                confirm_key = f"report_del_confirm_{rx['id']}"
                if not st.session_state.get(confirm_key):
                    if st.button("🗑 Delete", key=f"report_del_{rx['id']}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    if st.button("Confirm delete", key=f"report_del_yes_{rx['id']}", type="primary"):
                        try:
                            report_service.delete_report(rx["id"])
                            st.session_state.pop(confirm_key, None)
                            st.success("Report deleted and stock restored.")
                            st.rerun()
                        except ReportError as e:
                            st.error(str(e))
                    if st.button("Cancel", key=f"report_del_no_{rx['id']}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()

            if st.session_state.get(f"report_download_{rx['id']}"):
                _render_download(rx["id"])

            if st.session_state.get(f"report_edit_open_{rx['id']}", False):
                _edit_report_form(rx["id"], patient_name)


def _render_download(prescription_id: int):
    full = report_service.get_report(prescription_id)
    if not full:
        st.error("This report could not be loaded.")
        return
    try:
        pdf_bytes = report_pdf.render_report_pdf(full)
    except RuntimeError as e:
        st.error(str(e))
        return
    st.download_button(
        "Save PDF", data=pdf_bytes,
        file_name=f"report_{full['patient_name'].replace(' ', '_')}_{prescription_id}.pdf",
        mime="application/pdf", key=f"report_dl_confirm_{prescription_id}",
    )


def _edit_report_form(prescription_id: int, patient_name: str):
    full = report_service.get_report(prescription_id)
    if not full:
        st.error("This report could not be loaded.")
        return

    st.caption("Medicines on a saved report can't be edited — delete and create a new report if the medicines need to change.")
    with st.form(f"report_edit_form_{prescription_id}"):
        diagnosis = st.text_input("Diagnosis", value=full["diagnosis"])
        advice = st.text_area("Advice note", value=full["advice_note"])

        vitals = full.get("vitals") or {}
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            hr = st.number_input("Heart rate (bpm)", 0, 250, vitals.get("heart_rate") or 0)
        with c2:
            bp = st.text_input("Blood pressure", value=vitals.get("blood_pressure") or "")
        with c3:
            spo2 = st.number_input("SpO₂ (%)", 0, 100, vitals.get("pulse_oximetry") or 0)
        with c4:
            ef = st.number_input("Ejection fraction (%)", 0, 100, vitals.get("ejection_fraction") or 0)
        ecg_note = st.text_input("ECG note", value=vitals.get("ecg_note") or "")
        clinical_notes = st.text_area("Clinical notes", value=vitals.get("notes") or "")

        if not full.get("vitals"):
            st.caption("This report has no linked vitals yet — filling these in will add them.")

        submitted = st.form_submit_button("Save changes", type="primary")

    if submitted:
        report_service.update_report(
            prescription_id, diagnosis=diagnosis, advice_note=advice,
            heart_rate=hr or None, blood_pressure=bp or None,
            pulse_oximetry=spo2 or None, ejection_fraction=ef or None,
            ecg_note=ecg_note, clinical_notes=clinical_notes,
        )
        st.session_state[f"report_edit_open_{prescription_id}"] = False
        st.success(f"Report updated for {patient_name}.")
        st.rerun()


def _past_prescriptions(doctor_id: int, patient_id: int):
    rxs = prescription_service.list_by_doctor(doctor_id, patient_id=patient_id)
    if not rxs:
        st.caption("No prescriptions for this patient yet.")
        return
    for rx in rxs:
        meds_html = ""
        for it in rx["items"]:
            details = " · ".join(
                x for x in (it["dosage"], it["frequency"], it["duration"]) if x
            )
            if it["quantity"]:
                details += f" · qty {it['quantity']}" if details else f"qty {it['quantity']}"
            if it["instructions"]:
                details += f" · {it['instructions']}" if details else it["instructions"]
            meds_html += (
                f'<div class="rx-med"><span class="rx-med-name">💊 {it["medicine_name"]}</span>'
                f'<span class="rx-med-detail">{details}</span></div>'
            )
        advice_html = (
            f'<div class="rx-advice">📋 {rx["advice_note"]}</div>'
            if rx["advice_note"] else ""
        )
        st.markdown(
            f"""<div class="rx-card">
            <div class="rx-head">
              <span class="rx-symbol">℞</span>
              <span class="rx-title">{rx['diagnosis'] or 'Prescription'}</span>
              <span class="rx-date">{rx['created_at'].strftime('%d %b %Y, %I:%M %p')}</span>
            </div>
            {meds_html}
            {advice_html}
            </div>""",
            unsafe_allow_html=True,
        )


def _availability(doctor_id: int):
    slots = get_weekly_slots(doctor_id)
    if slots:
        st.markdown("**Current weekly slots**")
        for s in slots:
            c1, c2 = st.columns([4, 1])
            c1.caption(
                f"{s['day']}: {s['start_time'].strftime('%I:%M %p')} – {s['end_time'].strftime('%I:%M %p')}"
            )
            if c2.button("Remove", key=f"rm_{s['id']}"):
                deactivate_slot(s["id"])
                st.rerun()
    else:
        st.info("No weekly slots set yet — add one below.")

    st.markdown("**Add a slot**")
    c1, c2, c3 = st.columns(3)
    with c1:
        day = st.selectbox("Day", range(7), format_func=lambda i: DAY_NAMES[i])
    with c2:
        start = st.time_input("Start", value=time_cls(9, 0))
    with c3:
        end = st.time_input("End", value=time_cls(13, 0))

    if st.button("Add slot", type="primary"):
        if end <= start:
            st.error("End time must be after start time.")
        else:
            add_slot(doctor_id, day, start, end)
            st.success("Slot added.")
            st.rerun()