"""
views/admin_portal.py
Admin console — a single page with hospital-wide KPI tiles and a horizontal
tab bar:

    Analytics · Create doctor · Availability · Specialties ·
    Pharmacy · Prescriptions · Records · All appointments

Beyond the core screens it adds several admin-only features:
  • Analytics    — consultation-fee revenue panel + busiest-doctors leaderboard
  • Create doctor— a "Manage doctor accounts" panel to activate/deactivate logins
  • Specialties  — add a new specialty to the directory
  • Pharmacy     — a low-stock alert banner above the catalog
  • All appts    — CSV export of the (filtered) appointment list

Every screen reads/writes only through the services layer — no raw SQL, no
arithmetic done here that a service can do instead.
"""
from datetime import time, date
import pandas as pd
import streamlit as st

from services import analytics_service
from services.analytics_service import (
    get_kpis, appointments_last_n_days, appointments_per_specialty,
    appointment_status_breakdown,
)
from services.appointment_service import (
    list_all_appointments, update_appointment_status,
)
from services.doctor_service import (
    list_doctors, list_specialties, add_specialty, list_doctor_accounts,
    get_weekly_slots, add_slot, deactivate_slot, DAY_NAMES,
)
from services.auth_service import (
    create_doctor_account, set_account_active, AuthError,
)
from services.health_service import (
    get_all_patients, add_health_record, get_patient_records,
)
from services import prescription_service, medicine_service
from views import chatbot_view, medications_view, charts
from views.components import (
    button_tabs, ecg_divider, empty_state, kpi_tile, status_badge, set_page_background,
)

STATUSES = ["pending", "confirmed", "completed", "cancelled"]


def render(user, section: str = "Admin Console"):
    set_page_background("Admin Console")

    if section == "Smart Care AI":
        st.markdown("### Smart Care AI")
        ecg_divider()
        chatbot_view.render(user)
        return

    st.markdown("### Admin console")
    ecg_divider()
    _kpi_header()

    selected = button_tabs(
        ["Analytics", "Create doctor", "Availability", "Specialties",
         "Pharmacy", "Prescriptions", "Records", "All appointments"],
        key="admin_console",
    )
    if selected == "Analytics":
        _analytics()
    elif selected == "Create doctor":
        _create_doctor()
    elif selected == "Availability":
        _availability()
    elif selected == "Specialties":
        _specialties()
    elif selected == "Pharmacy":
        _pharmacy()
    elif selected == "Prescriptions":
        _prescriptions()
    elif selected == "Records":
        _records()
    elif selected == "All appointments":
        _all_appointments()


# ---------------------------------------------------------------- KPI header
def _kpi_header():
    k = get_kpis()
    c1, c2, c3, c4 = st.columns(4)
    kpi_tile("Doctors", k["total_doctors"], c1)
    kpi_tile("Patients", k["total_patients"], c2, dark=True)
    kpi_tile("Appointments", k["total_appointments"], c3, dark=True)
    kpi_tile(
        "Pending requests", k["pending"], c4,
        caption="awaiting action" if k["pending"] else "all clear",
    )


# ------------------------------------------------------------------ Analytics
def _analytics():
    col1, col2 = st.columns(2)

    with col1:
        data = appointments_last_n_days(14)
        labels = [d.strftime("%d %b") for d in data["dates"]]
        charts.bar_chart(labels, data["counts"], "Appointments per day", color="#E1614A")

    with col2:
        breakdown = appointment_status_breakdown()
        total = sum(breakdown["values"])
        charts.doughnut_chart(
            breakdown["labels"], breakdown["values"], "By status",
            center_text=str(total),
        )

    spec = appointments_per_specialty()
    charts.bar_chart(spec["labels"], spec["values"], "Appointments by specialty")

    st.markdown("---")

    # --- New: consultation-fee revenue -------------------------------------
    st.markdown("**Consultation revenue**")
    rev = analytics_service.revenue_summary()
    r1, r2, r3 = st.columns(3)
    kpi_tile("Collected", f"₹{rev['collected']:,.0f}", r1,
             caption="from completed visits")
    kpi_tile("In pipeline", f"₹{rev['pipeline']:,.0f}", r2, dark=True,
             caption="pending + confirmed")
    kpi_tile("Completed visits", rev["completed_count"], r3, dark=True)

    st.markdown("**Busiest doctors**")
    top = analytics_service.top_doctors(limit=6)
    charts.bar_chart(top["labels"], top["values"], "Appointments per doctor",
                     color="#3E7C6E")


# --------------------------------------------------------------- Create doctor
def _create_doctor():
    st.caption("Create a login for a new doctor. Only admins can do this.")

    specialties = list_specialties()
    spec_names = [s["name"] for s in specialties]

    with st.form("admin_create_doctor"):
        c1, c2 = st.columns(2)
        with c1:
            full_name = st.text_input("Full name", placeholder="Dr. Meera Nair")
        with c2:
            password = st.text_input("Temporary password", type="password")

        c3, c4 = st.columns(2)
        with c3:
            email = st.text_input("Email", placeholder="doctor@smartcare.in")
        with c4:
            experience = st.number_input("Experience (years)", 0, 60, 5)

        c5, c6 = st.columns(2)
        with c5:
            if spec_names:
                spec_choice = st.selectbox("Specialty", spec_names)
            else:
                spec_choice = None
                st.info("Add a specialty first (Specialties tab).")
        with c6:
            fee = st.number_input("Consultation fee (₹)", 0, 20000, 100)

        bio = st.text_area("Short bio", placeholder="Interventional cardiologist…")
        submitted = st.form_submit_button("Create doctor account", type="primary")

    st.caption("Password needs at least 8 characters, one number, and one special character.")

    if submitted:
        if not full_name.strip() or not email.strip():
            st.error("Full name and email are required.")
        elif spec_choice is None:
            st.error("Create at least one specialty before adding a doctor.")
        else:
            specialty_id = next(
                (s["id"] for s in specialties if s["name"] == spec_choice), None
            )
            try:
                create_doctor_account(
                    full_name=full_name, email=email, password=password,
                    specialty_id=specialty_id, experience_years=int(experience),
                    consultation_fee=float(fee), bio=bio,
                )
                st.success(
                    f"Created a doctor login for Dr. {full_name.strip()}. "
                    "Share the temporary password so they can sign in."
                )
                st.rerun()
            except AuthError as e:
                st.error(str(e))

    st.markdown("---")
    _manage_accounts()


def _manage_accounts():
    """New feature: activate / deactivate existing doctor logins."""
    with st.expander("Manage doctor accounts"):
        accounts = list_doctor_accounts()
        if not accounts:
            st.caption("No doctor accounts yet.")
            return
        for a in accounts:
            c1, c2, c3 = st.columns([3, 1.2, 1.2])
            with c1:
                st.markdown(f"**Dr. {a['name']}** &middot; {a['specialty']}")
                st.caption(a["email"])
            with c2:
                if a["is_active"]:
                    st.markdown(":green[● Active]")
                else:
                    st.markdown(":red[● Disabled]")
            with c3:
                if a["is_active"]:
                    if st.button("Deactivate", key=f"deact_{a['user_id']}"):
                        set_account_active(a["user_id"], False)
                        st.rerun()
                else:
                    if st.button("Reactivate", key=f"react_{a['user_id']}", type="primary"):
                        set_account_active(a["user_id"], True)
                        st.rerun()


# ---------------------------------------------------------------- Availability
def _availability():
    doctors = list_doctors()
    if not doctors:
        st.info("No doctors registered yet.")
        return

    labels = [f"Dr. {d['name']} ({d['specialty']})" for d in doctors]
    idx = st.selectbox("Doctor", range(len(doctors)), format_func=lambda i: labels[i])
    doctor = doctors[idx]
    doctor_id = doctor["doctor_id"]

    slots = get_weekly_slots(doctor_id)
    st.markdown("**Current weekly slots**")
    if slots:
        df = pd.DataFrame([
            {
                "Day": s["day"],
                "Start": s["start_time"].strftime("%H:%M"),
                "End": s["end_time"].strftime("%H:%M"),
            }
            for s in slots
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No weekly slots set for this doctor yet.")

    with st.expander("Add or remove slots"):
        c1, c2, c3 = st.columns(3)
        with c1:
            day = st.selectbox("Day", range(7), format_func=lambda i: DAY_NAMES[i],
                               key=f"av_day_{doctor_id}")
        with c2:
            start = st.time_input("Start", value=time(9, 0), key=f"av_start_{doctor_id}")
        with c3:
            end = st.time_input("End", value=time(9, 30), key=f"av_end_{doctor_id}")
        if st.button("Add slot", type="primary", key=f"av_add_{doctor_id}"):
            if end <= start:
                st.error("End time must be after start time.")
            else:
                add_slot(doctor_id, day, start, end)
                st.success("Slot added.")
                st.rerun()

        if slots:
            st.markdown("**Remove a slot**")
            for s in slots:
                c1, c2 = st.columns([4, 1])
                c1.caption(
                    f"{s['day']}: {s['start_time'].strftime('%I:%M %p')} – "
                    f"{s['end_time'].strftime('%I:%M %p')}"
                )
                if c2.button("Remove", key=f"av_rm_{s['id']}"):
                    deactivate_slot(s["id"])
                    st.rerun()


# ----------------------------------------------------------------- Specialties
def _specialties():
    specialties = list_specialties()
    if specialties:
        for s in specialties:
            icon = s.get("icon") or "•"
            desc = s.get("description") or ""
            st.markdown(f"{icon} **{s['name']}** — {desc}")
    else:
        st.info("No specialties defined yet — add the first one below.")

    st.markdown("---")
    with st.expander("➕ Add a specialty", expanded=not specialties):
        c1, c2 = st.columns([1, 3])
        with c1:
            icon = st.text_input("Icon (emoji)", placeholder="🫀", key="spec_icon")
        with c2:
            name = st.text_input("Name", placeholder="Cardiology", key="spec_name")
        description = st.text_input("Description", placeholder="Heart and blood vessels",
                                    key="spec_desc")
        if st.button("Add specialty", type="primary", key="spec_add"):
            if not name.strip():
                st.error("Specialty name is required.")
            else:
                try:
                    add_specialty(name=name, description=description, icon=icon)
                    st.success(f"Added {name.strip()}.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ------------------------------------------------------------------- Pharmacy
def _pharmacy():
    # New feature: surface low-stock medicines up front before the catalog.
    meds = medicine_service.list_medicines(active_only=True)
    low = [m for m in meds if m["low_stock"]]
    if low:
        names = ", ".join(f"{m['name']} ({m['stock_quantity']})" for m in low)
        st.warning(f"⚠️ {len(low)} medicine(s) low on stock: {names}")
    medications_view.render()


# --------------------------------------------------------------- Prescriptions
def _prescriptions():
    st.caption("Every prescription written in the system (newest first). Read-only.")
    rxs = prescription_service.list_all()
    if not rxs:
        st.info("No prescriptions have been written yet.")
        return

    search = st.text_input("Search by patient or doctor", placeholder="Name",
                           key="rx_search")
    if search:
        q = search.lower()
        rxs = [r for r in rxs
               if q in r["patient_name"].lower() or q in r["doctor_name"].lower()]
        if not rxs:
            st.caption("No prescriptions match that search.")
            return

    for rx in rxs:
        with st.container(border=True):
            st.markdown(
                f"**{rx['diagnosis'] or 'Prescription'}** — {rx['patient_name']} "
                f"&middot; by Dr. {rx['doctor_name']}"
            )
            st.caption(rx["created_at"].strftime("%d %b %Y, %I:%M %p"))
            for it in rx["items"]:
                details = " · ".join(
                    x for x in (it["dosage"], it["frequency"], it["duration"]) if x
                )
                if it["quantity"]:
                    details += (f" · qty {it['quantity']}" if details
                                else f"qty {it['quantity']}")
                if it["instructions"]:
                    details += f" · {it['instructions']}" if details else it["instructions"]
                st.markdown(f"- 💊 **{it['medicine_name']}** {('— ' + details) if details else ''}")
            if rx["advice_note"]:
                st.caption(f"📋 {rx['advice_note']}")


# --------------------------------------------------------------------- Records
def _records():
    st.caption("Add a vitals record for a patient (feeds the heart dashboard & analytics).")
    patients = get_all_patients()
    if not patients:
        empty_state("🧑‍🤝‍🧑", "No patients registered yet", "New patient signups will show up here.")
        return

    labels = [f"{p['name']} — {p['email']}" for p in patients]
    p_idx = st.selectbox("Patient", range(len(patients)),
                         format_func=lambda i: labels[i], key="rec_patient")
    patient = patients[p_idx]

    with st.form("admin_add_vitals"):
        c1, c2, c3 = st.columns(3)
        with c1:
            bp = st.text_input("Blood pressure", placeholder="120/80")
        with c2:
            hr = st.number_input("Heart rate", 0, 300, 0)
        with c3:
            spo2 = st.number_input("SpO2 %", 0, 100, 0)

        c4, c5, c6 = st.columns(3)
        with c4:
            ef = st.number_input("Ejection fraction %", 0, 100, 0)
        with c5:
            co = st.number_input("Cardiac output", 0.0, 20.0, 0.0, step=0.1)
        with c6:
            troponin = st.number_input("Troponin", 0.0, 100.0, 0.0, step=0.001, format="%.3f")

        diagnosis = st.text_input("Diagnosis", placeholder="e.g. Hypertension with tachycardia")
        ecg_note = st.text_input("ECG note", placeholder="e.g. Sinus tachycardia")
        submitted = st.form_submit_button("Save vitals record", type="primary")

    if submitted:
        if not (bp or hr or spo2 or ef or co or troponin or diagnosis or ecg_note):
            st.error("Enter at least one vital or a diagnosis.")
        else:
            add_health_record(
                patient_id=patient["id"], doctor_id=None,
                heart_rate=hr or None, blood_pressure=bp or None,
                troponin=troponin or None, ejection_fraction=ef or None,
                cardiac_output=co or None, pulse_oximetry=spo2 or None,
                ecg_note=ecg_note, diagnosis=diagnosis,
            )
            st.success(f"Saved a vitals record for {patient['name']}.")
            st.rerun()

    with st.expander("Recent records for this patient"):
        records = get_patient_records(patient["id"])
        if not records:
            st.caption("No health records on file.")
        for r in records[:8]:
            st.caption(
                f"{r['recorded_at'].strftime('%d %b %Y')} — "
                f"HR {r['heart_rate'] or '—'}, BP {r['blood_pressure'] or '—'}, "
                f"SpO2 {r['pulse_oximetry'] or '—'}, "
                f"Diagnosis: {r['diagnosis'] or '—'}"
            )


# ---------------------------------------------------------------- All appts
def _all_appointments():
    status_filter = st.selectbox("Filter by status", ["All"] + STATUSES)
    appts = list_all_appointments(None if status_filter == "All" else status_filter)

    if not appts:
        st.info("No appointments match this filter.")
        return

    # New feature: export the (filtered) list as CSV.
    export_df = pd.DataFrame([
        {
            "Patient": a["patient_name"],
            "Doctor": a["doctor_name"],
            "Date": a["date"].strftime("%Y-%m-%d"),
            "Time": a["start_time"].strftime("%H:%M"),
            "Status": a["status"],
        }
        for a in appts
    ])
    st.download_button(
        "⬇️ Export CSV", export_df.to_csv(index=False).encode("utf-8"),
        file_name=f"appointments_{status_filter.lower()}_{date.today().isoformat()}.csv",
        mime="text/csv",
    )

    for a in appts:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.3, 1.2])
            with c1:
                st.markdown(f"**{a['patient_name']}** with **Dr. {a['doctor_name']}**")
                st.caption(
                    f"{a['date'].strftime('%d %b %Y')} at "
                    f"{a['start_time'].strftime('%I:%M %p')}"
                )
            with c2:
                st.markdown(status_badge(a["status"]), unsafe_allow_html=True)
            with c3:
                new_status = st.selectbox(
                    "Update", STATUSES, index=STATUSES.index(a["status"]),
                    key=f"admin_status_{a['id']}", label_visibility="collapsed",
                )
                if new_status != a["status"] and st.button("Save", key=f"admin_save_{a['id']}"):
                    update_appointment_status(a["id"], new_status)
                    st.rerun()