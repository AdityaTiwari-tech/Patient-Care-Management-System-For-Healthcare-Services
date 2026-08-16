"""
views/patient_dashboard.py
Patient home: vitals snapshot, health record history (with charts),
appointments, doctor directory and pharmacy — routed via the left-side nav.
"""
import pandas as pd
import streamlit as st

from services.health_service import get_patient_records, get_latest_vitals
from services.appointment_service import list_patient_appointments
from services import prescription_service, report_service, report_pdf
from views import appointments_view, doctors_view, chatbot_view, charts, pharmacy_view
from views.components import ecg_divider, report_preview, vitals_compass, set_page_background


def render(user, section: str = "My Health 🫀"):
    set_page_background(section)
    st.markdown(f"### Welcome back, {user.full_name.split()[0]}")
    ecg_divider()

    if section == "My Health 🫀":
        _vitals_snapshot(user.id)
        _prescriptions(user.id)
        _health_records(user.id)
    elif section == "Appointments":
        appointments_view.render(user.id)
    elif section == "Doctors":
        doctors_view.render()
    elif section == "Pharmacy 💊":
        pharmacy_view.render(user.id)
    elif section == "Smart Care AI":
        chatbot_view.render(user)


def _vitals_snapshot(patient_id: int):
    latest = get_latest_vitals(patient_id)
    vitals_compass(latest)


def _health_records(patient_id: int):
    records = get_patient_records(patient_id)
    if not records:
        st.info("No health records on file yet. Your doctor will add these after a visit.")
        return

    df = pd.DataFrame(records).sort_values("recorded_at")

    # Split "120/80" into separate systolic/diastolic columns so they can
    # be plotted as two lines on one chart.
    bp_split = df["blood_pressure"].fillna("").str.split("/", expand=True)
    df["bp_systolic"] = pd.to_numeric(bp_split.get(0), errors="coerce")
    if bp_split.shape[1] > 1:
        df["bp_diastolic"] = pd.to_numeric(bp_split[1], errors="coerce")
    else:
        df["bp_diastolic"] = pd.Series([float("nan")] * len(df), index=df.index)

    st.markdown("**Vitals trends**")
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        _trend_chart(df, ["heart_rate"], "Heart rate (bpm)")
        _trend_chart(df, ["pulse_oximetry"], "SpO₂ (%)")
        _trend_chart(df, ["ejection_fraction"], "Ejection fraction (%)")

    with chart_col2:
        _trend_chart(df, ["bp_systolic", "bp_diastolic"], "Blood pressure (systolic / diastolic)")
        _trend_chart(df, ["troponin"], "Troponin")
        _trend_chart(df, ["cardiac_output"], "Cardiac output")

    st.markdown("**Full history**")
    for r in records:
        with st.container(border=True):
            st.markdown(
                f"**{r['recorded_at'].strftime('%d %b %Y, %I:%M %p')}** &middot; "
                f"recorded by {r['doctor_name']}"
            )
            cols = st.columns(4)
            cols[0].caption(f"HR: {r['heart_rate'] or '—'} bpm")
            cols[1].caption(f"BP: {r['blood_pressure'] or '—'}")
            cols[2].caption(f"SpO₂: {r['pulse_oximetry'] or '—'}%")
            cols[3].caption(f"Troponin: {r['troponin'] if r['troponin'] is not None else '—'}")
            if r["diagnosis"]:
                st.markdown(f"*Diagnosis:* {r['diagnosis']}")
            if r["notes"]:
                st.markdown(f"*Notes / prescription:* {r['notes']}")
            if r["ecg_note"]:
                st.caption(f"ECG: {r['ecg_note']}")


def _prescriptions(patient_id: int):
    """Prescriptions the patient's doctors have issued, newest first,
    rendered as pharmacy-style Rx slips (see .rx-card in styles.css)."""
    rxs = prescription_service.list_for_patient(patient_id)
    if not rxs:
        return
    st.markdown("**Your prescriptions**")
    _report_download_picker(rxs)
    for rx in rxs[:10]:
        title = f"Dr. {rx['doctor_name']}" if rx["doctor_name"] else "Prescription"
        if rx["diagnosis"]:
            title += f" · {rx['diagnosis']}"
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
              <span class="rx-title">{title}</span>
              <span class="rx-date">{rx['created_at'].strftime('%d %b %Y')}</span>
            </div>
            {meds_html}
            {advice_html}
            </div>""",
            unsafe_allow_html=True,
        )
    ecg_divider()


def _report_download_picker(rxs: list[dict]):
    """One dropdown to pick a report BY DATE, plus a single download
    button — handier than a button per card once a patient has more than
    a handful of prescriptions on file. rxs is already newest-first, so
    that's the dropdown's natural order too."""
    options = {
        rx["id"]: (
            f"{rx['created_at'].strftime('%d %b %Y')} — "
            f"{rx['diagnosis'] or 'Prescription'} (Dr. {rx['doctor_name']})"
        )
        for rx in rxs
    }
    selected_id = st.selectbox(
        "Select a report by date", list(options.keys()),
        format_func=lambda i: options[i], key="patient_report_date_select",
    )
    _download_report_button(selected_id)
    st.markdown("---")


def _download_report_button(prescription_id: int):
    """Patients can only READ and export a report — no edit/delete
    controls appear here at all; those live exclusively in
    views/doctor_portal.py's Patient report tab. Preview and Download are
    independent toggles: Preview renders the SAME HTML template Download
    turns into a PDF (see views/components.report_preview), just inline
    and without needing xhtml2pdf, so a patient can check a report before
    committing to a download."""
    preview_key = f"patient_report_preview_{prescription_id}"
    dl_key = f"patient_report_dl_{prescription_id}"

    c1, c2 = st.columns(2)
    if c1.button("👁️ Preview", key=f"patient_report_preview_btn_{prescription_id}", use_container_width=True):
        st.session_state[preview_key] = not st.session_state.get(preview_key, False)
        st.rerun()
    if c2.button("⬇️ Download PDF", key=f"patient_report_btn_{prescription_id}", use_container_width=True):
        st.session_state[dl_key] = True
        st.rerun()

    if st.session_state.get(preview_key):
        full = report_service.get_report(prescription_id)
        if not full:
            st.error("This report could not be loaded.")
        else:
            report_preview(full, height=600)

    if st.session_state.get(dl_key):
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
            file_name=f"report_{prescription_id}.pdf", mime="application/pdf",
            key=f"patient_report_confirm_{prescription_id}",
        )


def _trend_chart(df: pd.DataFrame, columns: list[str], title: str):
    """Renders a small line chart for one or more vitals columns, skipping
    quietly if there's no data yet for that metric."""
    present = [c for c in columns if c in df.columns]
    chart_df = df.dropna(subset=present, how="all")[["recorded_at"] + present] if present else pd.DataFrame()
    if chart_df.empty:
        return
    st.caption(title)
    st.line_chart(chart_df.set_index("recorded_at")[present], height=180)