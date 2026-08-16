"""
views/appointments_view.py
Patient-facing appointments: a "next appointment" banner, a Book flow
(specialty -> doctor with fee/experience -> date -> clickable slot grid),
a Calendar tab (Day / Month views of the patient's own bookings), and a
flat "My appointments" list.
"""
import calendar
from datetime import date, timedelta, time as time_cls
import streamlit as st

from services.doctor_service import list_specialties, list_doctors
from services.appointment_service import (
    book_appointment, list_patient_appointments, get_booked_slots, AppointmentError,
)
from views.components import button_tabs, empty_state, status_badge

SLOT_TIMES = [time_cls(h, m) for h in range(9, 18) for m in (0, 30)]
DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def render(patient_id: int):
    _next_appointment_banner(patient_id)

    selected = button_tabs(["Book", "Calendar", "My appointments"], key="patient_appointments")

    if selected == "Book":
        _booking_form(patient_id)
    elif selected == "Calendar":
        _calendar_view(patient_id)
    elif selected == "My appointments":
        _my_appointments(patient_id)


def _next_appointment_banner(patient_id: int):
    appts = list_patient_appointments(patient_id)
    upcoming = sorted(
        (a for a in appts if a["status"] in ("pending", "confirmed") and a["date"] >= date.today()),
        key=lambda a: (a["date"], a["start_time"]),
    )
    if not upcoming:
        return

    nxt = upcoming[0]
    delta = (nxt["date"] - date.today()).days
    badge = "Today" if delta == 0 else "Tomorrow" if delta == 1 else nxt["date"].strftime("%d %b")

    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(
                f"""<div class="next-appt-eyebrow">Next appointment</div>
                <p class="next-appt-title">Dr. {nxt['doctor_name']} &middot; {nxt['specialty']}</p>
                <p class="next-appt-meta">{nxt['date'].strftime('%A, %d %B')} at {nxt['start_time'].strftime('%I:%M %p')}{' &middot; ' + nxt['reason'] if nxt['reason'] else ''}</p>""",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(f'<span class="next-appt-badge">{badge}</span>', unsafe_allow_html=True)


def _booking_form(patient_id: int):
    specs = list_specialties()
    spec_options = ["All specialties"] + [s["name"] for s in specs]
    col1, col2 = st.columns(2)
    with col1:
        chosen_spec = st.selectbox("Specialty", spec_options)
    specialty_id = next((s["id"] for s in specs if s["name"] == chosen_spec), None)

    doctors = list_doctors(specialty_id=specialty_id)
    if not doctors:
        st.info("No doctors match that specialty yet.")
        return

    doc_labels = [f"Dr. {d['name']} — ₹{d['fee']:.0f} · {d['experience_years']}y" for d in doctors]
    with col2:
        idx = st.selectbox("Doctor", range(len(doctors)), format_func=lambda i: doc_labels[i])
    doctor = doctors[idx]

    chosen_date = st.date_input(
        "Date", value=date.today() + timedelta(days=1), min_value=date.today(),
    )

    booked = set(get_booked_slots(doctor["doctor_id"], chosen_date))
    available = [t for t in SLOT_TIMES if t not in booked]

    st.markdown(f"**Available slots** &middot; {len(available)} of {len(SLOT_TIMES)} open")
    slot_key = f"selected_slot_{doctor['doctor_id']}_{chosen_date}"
    if slot_key not in st.session_state:
        st.session_state[slot_key] = None

    if not available:
        st.warning("No open slots that day — try another date.")
    else:
        st.markdown('<div class="slot-btn-wrap">', unsafe_allow_html=True)
        cols = st.columns(4)
        for i, t in enumerate(available):
            is_selected = st.session_state[slot_key] == t
            if cols[i % 4].button(
                t.strftime("%I:%M %p"), key=f"{slot_key}_{t}",
                type="primary" if is_selected else "secondary", use_container_width=True,
            ):
                st.session_state[slot_key] = t
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    chosen_time = st.session_state[slot_key]
    reason = st.text_area("Reason for visit", placeholder="e.g. Follow-up on chest pain, routine ECG...")

    if st.button("Confirm booking", type="primary", disabled=chosen_time is None):
        try:
            book_appointment(
                patient_id=patient_id, doctor_id=doctor["doctor_id"],
                scheduled_date=chosen_date, start_time=chosen_time,
                reason=reason, source="patient",
            )
            st.session_state[slot_key] = None
            st.success(f"Booked with Dr. {doctor['name']} on {chosen_date.strftime('%d %b %Y')}.")
            st.rerun()
        except AppointmentError as e:
            st.error(str(e))


def _calendar_view(patient_id: int):
    appts = list_patient_appointments(patient_id)

    view = st.radio("View", ["Month", "Day"], horizontal=True, label_visibility="collapsed")

    if view == "Month":
        _month_view(appts)
    else:
        _day_view(appts)


def _month_view(appts: list[dict]):
    if "cal_month_ref" not in st.session_state:
        st.session_state.cal_month_ref = date.today().replace(day=1)
    ref = st.session_state.cal_month_ref

    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    if c1.button("◀", key="cal_prev_month"):
        prev_month_last_day = ref - timedelta(days=1)
        st.session_state.cal_month_ref = prev_month_last_day.replace(day=1)
        st.rerun()
    if c2.button("Today", key="cal_today_month"):
        st.session_state.cal_month_ref = date.today().replace(day=1)
        st.rerun()
    if c3.button("▶", key="cal_next_month"):
        next_month_first = (ref.replace(day=28) + timedelta(days=4)).replace(day=1)
        st.session_state.cal_month_ref = next_month_first
        st.rerun()
    c4.markdown(f"**{ref.strftime('%B %Y')}**")

    by_day = {}
    for a in appts:
        if a["date"].year == ref.year and a["date"].month == ref.month:
            by_day.setdefault(a["date"].day, []).append(a)

    weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(ref.year, ref.month)  # Sunday-first
    today = date.today()

    dow_html = "".join(f'<div class="cal-dow">{d}</div>' for d in DOW_LABELS)
    cell_html_parts = []
    for week in weeks:
        for day_num in week:
            if day_num == 0:
                cell_html_parts.append('<div class="cal-cell is-empty"></div>')
                continue
            is_today = (ref.year, ref.month, day_num) == (today.year, today.month, today.day)
            day_appts = sorted(by_day.get(day_num, []), key=lambda a: a["start_time"])
            entries = "".join(
                f'<div class="cal-entry">{a["start_time"].strftime("%H:%M")} Dr. {a["doctor_name"]}</div>'
                for a in day_appts[:3]
            )
            if len(day_appts) > 3:
                entries += f'<div class="cal-entry">+{len(day_appts) - 3} more</div>'
            cell_class = "cal-cell is-today" if is_today else "cal-cell"
            cell_html_parts.append(
                f'<div class="{cell_class}"><div class="cal-daynum">{day_num}</div>{entries}</div>'
            )
    grid_html = "".join(cell_html_parts)

    st.markdown(f'<div class="cal-month">{dow_html}{grid_html}</div>', unsafe_allow_html=True)


def _day_view(appts: list[dict]):
    if "cal_day_ref" not in st.session_state:
        st.session_state.cal_day_ref = date.today()
    ref = st.session_state.cal_day_ref

    c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
    if c1.button("◀", key="cal_prev_day"):
        st.session_state.cal_day_ref = ref - timedelta(days=1)
        st.rerun()
    if c2.button("Today", key="cal_today_day"):
        st.session_state.cal_day_ref = date.today()
        st.rerun()
    if c3.button("▶", key="cal_next_day"):
        st.session_state.cal_day_ref = ref + timedelta(days=1)
        st.rerun()
    c4.markdown(f"**{ref.strftime('%A, %d %B %Y')}**")

    day_appts = {a["start_time"].hour: a for a in appts if a["date"] == ref}

    row_parts = []
    for hour in range(8, 19):
        label = time_cls(hour, 0).strftime("%I %p")
        appt = day_appts.get(hour)
        if appt:
            entry = (
                f'<div class="cal-day-entry"><strong>{appt["start_time"].strftime("%I:%M %p")} '
                f'Dr. {appt["doctor_name"]}</strong> &middot; {appt["specialty"]}'
                f'{" &middot; " + appt["reason"] if appt["reason"] else ""}</div>'
            )
        else:
            entry = '<div style="flex:1;"></div>'
        row_parts.append(f'<div class="cal-day-row"><div class="cal-day-hour">{label}</div>{entry}</div>')

    st.markdown("".join(row_parts), unsafe_allow_html=True)


def _my_appointments(patient_id: int):
    appts = list_patient_appointments(patient_id)
    if not appts:
        empty_state("📅", "No appointments yet", "Book one from the Book tab to see it here.")
        return

    for a in appts:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**Dr. {a['doctor_name']}** &middot; {a['specialty']}")
                st.caption(f"{a['date'].strftime('%d %b %Y')} at {a['start_time'].strftime('%I:%M %p')}")
                if a["reason"]:
                    st.caption(f"Reason: {a['reason']}")
            with c2:
                st.markdown(status_badge(a["status"]), unsafe_allow_html=True)