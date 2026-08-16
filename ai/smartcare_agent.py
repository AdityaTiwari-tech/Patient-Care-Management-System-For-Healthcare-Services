"""
ai/smartcare_agent.py
THE BRAIN of the chatbot. This is where the LLM meets our code — and
where it stops. The model never sees the database and never writes SQL;
it is handed a small menu of ordinary Python functions ("tools") bound
to the logged-in user's own identity, picks one, and we run the real
function. The database is the source of truth; the model is a router
and a narrator.

    views/chatbot_view.py  ->  ask(user, message, history)  ->  (answer, signals)

Five rules this file exists to enforce (see also ai/booking_flow.py):
  1. The LLM never writes to the database — see ai/booking_flow.py.
  2. Identity is bound in a closure, never passed as a tool parameter.
     The moment a tool takes a patient_id argument, isolation is gone.
  3. Arithmetic and date parsing happen in Python; the model narrates.
  4. The docstring on every @tool IS the prompt — LangChain converts it
     straight into the JSON schema the model reads to decide whether to
     call it. Write it for the model, not for a human reader.
  5. Prefer one range tool (appointments_between, find_available_doctors
     with days=N) over N single-day calls — it's cheaper and it's what
     prevents recursion overruns on "how's my week look" style questions.
"""
import logging
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

from ai.llm import get_llm, is_configured
from ai.languages import get_language, DEFAULT_LANGUAGE

log = logging.getLogger("smartcare.agent")

ROLE_PATIENT = "patient"
ROLE_DOCTOR = "doctor"
ROLE_ADMIN = "admin"

_RECURSION_LIMIT = 30
_MAX_RETRIES = 4
_RATE_LIMITED = ("rate limit", "429", "rate_limit_exceeded")
_TOOL_USE_FAILED = ("tool_use_failed", "failed to call a function", "tool call validation failed")

# Appended to every role's system prompt: how to WRITE the answer once the
# tools have returned. Detail comes from structure and completeness, never
# from inventing facts beyond what the tools provided.
_RESPONSE_STYLE = (
    " RESPONSE STYLE: Write like a knowledgeable person chatting with the "
    "patient/doctor/admin, not a report generator — warm, natural, in "
    "your own words, contractions welcome. Match length and format to "
    "the question: a quick fact or a yes/no deserves a short, direct "
    "reply in plain sentences, not headers or bullets for their own "
    "sake. Save Markdown structure (headings, bullet lists, tables) for "
    "when it actually helps — several items sharing the same fields "
    "(doctors, medicines, appointments), or an explanation with genuinely "
    "distinct parts. Never open with a stock phrase like 'Here is the "
    "information you requested' — just answer, the way you'd start "
    "talking, not the way a report starts. However many items a tool "
    "returned, report all of them — never quietly summarise a list down "
    "to 'a few' or 'and more'; completeness is about the DATA, not the "
    "formatting wrapped around it, so a plain sentence listing every item "
    "is fine when a table would feel like overkill. Include every "
    "relevant field the tool gave you (names, dates, times, statuses, "
    "doses, prices, stock counts) — just say it plainly rather than "
    "dressing it up. Where a natural next step fits (checking "
    "availability, opening the booking flow), offer it in passing as "
    "part of the reply, not bolted on as a separate closing line every "
    "time. For general-knowledge health questions, answer as thoroughly "
    "as the question actually calls for — sectioned only if it's "
    "genuinely long or multi-part, a couple of sentences if it isn't — "
    "and remind the patient to confirm specifics with their doctor."
)

SYSTEM_PROMPTS = {
    ROLE_PATIENT: (
        "You are SmartCare Assistant, a friendly helper inside a hospital "
        "PATIENT portal. Use your tools for anything about THIS clinic — "
        "doctors, specialties, availability, the patient's own appointments, "
        "prescriptions, medical history or health records. Never invent "
        "clinic facts; if a tool returns nothing useful, say so plainly. "
        "For general health, diet, or medicine questions with no "
        "clinic-specific angle, answer from your own knowledge — you don't "
        "need a tool for those, and search_health_info does not cover them. "
        "Don't do arithmetic on clinic numbers yourself; report exactly "
        "what a tool returned. You are NOT a doctor: never diagnose or "
        "prescribe; when asked about their prescriptions, report what is "
        "on file without adding medical advice. If the patient wants to "
        "book, schedule or reschedule a visit, call start_booking_flow — "
        "never try to complete a booking yourself in conversation. If the "
        "patient uploads a health report for you to summarize, describe "
        "what it says in plain language — its type, its key values, and "
        "anything it flags as outside its own stated reference range — "
        "without diagnosing a condition or recommending a treatment "
        "change; close by suggesting they discuss the full report with "
        "their doctor. Never discuss another patient's information; you "
        "have no tool that could."
    ),
    ROLE_DOCTOR: (
        "You are SmartCare Assistant, a clinical support helper inside a "
        "hospital DOCTOR portal. Use your tools for the doctor's own "
        "schedule, workload, profile, prescriptions they wrote, the "
        "pharmacy's medicine stock, and their OWN patients' health "
        "records — you only have tools for their own data; another "
        "doctor's patients are unreachable. Prefer the range tool "
        "(appointments_between) over calling a single-day tool repeatedly "
        "for anything spanning more than one day. Don't do arithmetic "
        "yourself; report what a tool returned. You do not make final "
        "diagnostic or prescribing decisions — you only support the "
        "doctor's own judgement. Never reveal another doctor's patients, "
        "hospital financials, or admin/HR matters; you have no tool that "
        "could. If the doctor wants to create, update, or delete a "
        "patient report, call the matching propose_*_report tool — never "
        "write or delete a report yourself in conversation; the doctor "
        "must review and save it themselves."
    ),
    ROLE_ADMIN: (
        "You are SmartCare Assistant, an operations helper inside a "
        "hospital ADMIN portal. Your tools cover everything this portal "
        "shows: hospital-wide KPIs and charts, the doctor and patient "
        "directories, the full appointment list, each patient's health "
        "records, and the pharmacy's medicine inventory with prices and "
        "stock. Never invent data — always answer from a tool, and if a "
        "tool returns nothing, say so plainly. Don't do arithmetic "
        "yourself; report what a tool returned. You support operations "
        "only: never give medical advice, interpret a patient's clinical "
        "results, or suggest treatment — refer clinical questions to the "
        "patient's doctor. If the admin wants to add, restock, reprice, "
        "or remove a medicine from the pharmacy catalog, call the "
        "matching propose_*_medicine tool — never edit the catalog "
        "yourself in conversation; the admin must review and confirm the "
        "change first."
    ),
}


def _language_instruction(language_label: str) -> str:
    """
    Appended to every role's system prompt, after _RESPONSE_STYLE. The
    UI's language dropdown (see views/chatbot_view.py) only sets a
    fallback and picks the Whisper/TTS locale — the model itself is
    always told to mirror whatever language the person actually used in
    THIS message, so someone can type in Hindi one turn and English the
    next and get matching replies each time, without switching the
    dropdown. Kept broad ("any Indian language") rather than enumerating
    ai/languages.py's curated list, since that list only drives the
    dropdown + voice locales — the model can read/write far more
    languages than that short list covers.
    """
    fallback = get_language(language_label)["llm_name"]
    return (
        " LANGUAGE: Always reply in the same language the person actually "
        "wrote or spoke in for THIS message — English, Hindi, Tamil, "
        "Telugu, Bengali, Marathi, Gujarati, Kannada, Malayalam, Punjabi, "
        "Urdu, or any other Indian language, in that language's normal "
        "native script (e.g. Devanagari for Hindi, Tamil script for "
        "Tamil) — never transliterate into Roman letters unless the "
        "person themselves wrote in Roman letters. This applies to the "
        "whole reply, including facts pulled from a tool — translate the "
        "content itself, not just a one-line acknowledgement. Keep "
        "medical/clinical terms accurate when translating them; if a "
        "term has no natural equivalent, keep the English term and "
        "explain it in the target language. If a message is too short or "
        f"ambiguous to tell the language, reply in {fallback}."
    )


def _system_prompt(user: dict, language: str = DEFAULT_LANGUAGE) -> str:
    base = SYSTEM_PROMPTS.get(user.get("role"), SYSTEM_PROMPTS[ROLE_PATIENT])
    return base + _RESPONSE_STYLE + _language_instruction(language)


# --------------------------------------------------------------------------
# Small shared helpers used by several tools below. Rule B from the
# blueprint: the model extracts free text, Python decides if it's real.
# --------------------------------------------------------------------------

def _parse_date(text: str) -> Optional[date_cls]:
    """Accepts today/tomorrow/yesterday, a weekday name, or a handful of
    common date formats. Returns None for anything else — the calling
    tool then replies with a correction instead of querying with garbage."""
    if not text:
        return None
    t = text.strip().lower()
    today = date_cls.today()
    if t == "today":
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t == "yesterday":
        return today - timedelta(days=1)

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if t in weekdays:
        target = weekdays.index(t)
        delta = (target - today.weekday()) % 7
        delta = delta or 7  # saying "monday" ON a monday means next monday
        return today + timedelta(days=delta)

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _match_specialty_id(specialty_text: str) -> Optional[int]:
    if not specialty_text:
        return None
    from services.doctor_service import list_specialties
    text = specialty_text.strip().lower()
    specs = list_specialties()
    for s in specs:
        if s["name"].lower() == text:
            return s["id"]
    for s in specs:
        if text in s["name"].lower() or s["name"].lower() in text:
            return s["id"]
    return None


def _match_specialty_name(specialty_text: str) -> Optional[str]:
    """Same matching as _match_specialty_id, but returns the specialty's
    display name — used to pre-fill the medicine/report wizards' specialty
    dropdowns, which are keyed by name, not id."""
    spec_id = _match_specialty_id(specialty_text)
    if spec_id is None:
        return None
    from services.doctor_service import list_specialties
    for s in list_specialties():
        if s["id"] == spec_id:
            return s["name"]
    return None


def _match_doctor(name_text: str) -> Optional[dict]:
    from services.doctor_service import list_doctors
    doctors = list_doctors(search=name_text)
    return doctors[0] if doctors else None


def _format_doctor_appts(appts: list) -> str:
    return "\n".join(
        f"- {a['date'].isoformat()} at {a['start_time'].strftime('%H:%M')} "
        f"with {a['patient_name']} ({a['status']})"
        for a in sorted(appts, key=lambda x: (x["date"], x["start_time"]))
    )


def _format_prescription(rx: dict) -> str:
    """One prescription -> compact text the model can narrate accurately."""
    header = rx["created_at"].strftime("%Y-%m-%d")
    if rx.get("doctor_name"):
        header += f" by Dr. {rx['doctor_name']}"
    if rx.get("patient_name"):
        header += f" for {rx['patient_name']}"
    if rx.get("diagnosis"):
        header += f" — {rx['diagnosis']}"
    lines = [header]
    for it in rx["items"]:
        detail = ", ".join(x for x in (it["dosage"], it["frequency"], it["duration"]) if x)
        line = f"  - {it['medicine_name']}"
        if detail:
            line += f": {detail}"
        if it["quantity"]:
            line += f", qty {it['quantity']}"
        if it["instructions"]:
            line += f" ({it['instructions']})"
        lines.append(line)
    if rx.get("advice_note"):
        lines.append(f"  Advice: {rx['advice_note']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Patient tools (12) — bound to their own user.id, never a parameter.
# --------------------------------------------------------------------------

def _patient_tools(patient_id: int, signals: dict) -> list:
    from langchain_core.tools import tool
    from services import doctor_service, appointment_service, health_service
    from services import prescription_service
    from ai.knowledge_base import retrieve_context

    @tool
    def list_specialties() -> str:
        """List every medical specialty this clinic offers, with a short description of what each treats."""
        specs = doctor_service.list_specialties()
        if not specs:
            return "No specialties are set up yet."
        return "\n".join(f"- {s['name']}: {s['description'] or ''}" for s in specs)

    @tool
    def find_doctors(specialty: str = "") -> str:
        """Find doctors at this clinic, optionally filtered by specialty name (e.g. "Cardiology"). Leave blank to list every doctor."""
        specialty_id = _match_specialty_id(specialty)
        doctors = doctor_service.list_doctors(specialty_id=specialty_id)
        if not doctors:
            return "No doctors match that specialty."
        return "\n".join(
            f"- {doctor_service.display_name(d)}, {d['experience_years']} yrs exp, fee {d['fee']:.0f}"
            for d in doctors
        )

    @tool
    def find_available_doctors(date: str = "today", specialty: str = "", days: int = 1) -> str:
        """
        Every doctor's FREE slot count, optionally filtered by specialty,
        starting from `date` (today/tomorrow/a weekday name/YYYY-MM-DD)
        across `days` days.

        'which doctors are free tomorrow?' -> date="tomorrow", days=1.
        For a span ('this week') set days (e.g. days=7) and this covers
        the whole range in ONE call — never call it once per day.
        """
        start = _parse_date(date) or date_cls.today()
        specialty_id = _match_specialty_id(specialty)
        doctors = doctor_service.list_doctors(specialty_id=specialty_id)
        if not doctors:
            return "No doctors match that specialty."
        span = max(days, 1)
        lines = []
        for d in doctors:
            total_open = sum(
                len(appointment_service.get_available_slots(d["doctor_id"], start + timedelta(days=i)))
                for i in range(span)
            )
            lines.append(
                f"- {doctor_service.display_name(d)}: {total_open} open slot(s) "
                f"across {span} day(s) from {start.isoformat()}"
            )
        return "\n".join(lines)

    @tool
    def check_availability(doctor_name: str, date: str = "today") -> str:
        """Check one named doctor's open time slots on a specific date (today/tomorrow/a weekday name/YYYY-MM-DD)."""
        day = _parse_date(date)
        if day is None:
            return f"I couldn't understand the date '{date}'. Try 'today', 'tomorrow', a weekday name, or YYYY-MM-DD."
        doctor = _match_doctor(doctor_name)
        if not doctor:
            return f"I couldn't find a doctor matching '{doctor_name}'."
        slots = appointment_service.get_available_slots(doctor["doctor_id"], day)
        if not slots:
            return f"{doctor_service.display_name(doctor)} has no open slots on {day.isoformat()}."
        times = ", ".join(t.strftime("%H:%M") for t in slots)
        return f"{doctor_service.display_name(doctor)} is free on {day.isoformat()} at: {times}"

    @tool
    def start_booking_flow() -> str:
        """Open the guided booking assistant. Call this whenever the patient wants to book, schedule, arrange or make an appointment — never try to complete a booking yourself in conversation."""
        signals["start_booking"] = True
        return "Opening the booking assistant now."

    @tool
    def my_appointments() -> str:
        """List the patient's own upcoming appointments."""
        appts = appointment_service.list_for_patient(patient_id, upcoming_only=True)
        if not appts:
            return "You have no upcoming appointments."
        return "\n".join(
            f"- {a['date'].isoformat()} at {a['start_time'].strftime('%H:%M')} with "
            f"Dr. {a['doctor_name']} ({a['specialty']}) — {a['status']}"
            for a in appts
        )

    @tool
    def my_health_summary() -> str:
        """The patient's most recent recorded vitals: heart rate, blood pressure, SpO2, ejection fraction, troponin, cardiac output."""
        latest = health_service.latest_vitals(patient_id)
        if not latest:
            return "No health records on file yet."
        return (
            f"As of {latest['recorded_at'].strftime('%Y-%m-%d')}: "
            f"HR {latest['heart_rate'] or '—'} bpm, BP {latest['blood_pressure'] or '—'}, "
            f"SpO2 {latest['pulse_oximetry'] or '—'}%, EF {latest['ejection_fraction'] or '—'}%, "
            f"troponin {latest['troponin'] if latest['troponin'] is not None else '—'}, "
            f"cardiac output {latest['cardiac_output'] if latest['cardiac_output'] is not None else '—'}."
        )

    @tool
    def my_health_trend() -> str:
        """How the patient's vitals have changed between their earliest and latest recorded visit, including whether each change is improving or worsening."""
        trend = health_service.get_health_trend(patient_id)
        if not trend:
            return "Not enough health records yet to show a trend (need at least 2)."
        lines = [
            f"Vitals trend across {trend['record_count']} records, "
            f"{trend['start_date'].strftime('%Y-%m-%d')} -> {trend['end_date'].strftime('%Y-%m-%d')}:"
        ]
        for d in trend["deltas"]:
            arrow = "down" if d["change"] < 0 else "up" if d["change"] > 0 else "flat"
            lines.append(
                f"- {d['label']}: {d['first']} -> {d['last']} {d['unit']}, "
                f"{arrow} {abs(d['change'])} ({d['direction']})"
            )
        return "\n".join(lines)

    @tool
    def my_past_appointments() -> str:
        """The patient's own PAST appointments (completed or cancelled included). Use my_appointments for upcoming ones."""
        appts = appointment_service.list_for_patient(patient_id, upcoming_only=False)
        past = [a for a in appts if a["date"] < date_cls.today()]
        if not past:
            return "You have no past appointments."
        return "\n".join(
            f"- {a['date'].isoformat()} at {a['start_time'].strftime('%H:%M')} with "
            f"Dr. {a['doctor_name']} ({a['specialty']}) — {a['status']}"
            for a in past[:15]
        )

    @tool
    def my_prescriptions() -> str:
        """The patient's own prescriptions: each medicine's name, dosage, frequency, duration, quantity, instructions, plus the doctor's advice note."""
        rxs = prescription_service.list_for_patient(patient_id)
        if not rxs:
            return "You have no prescriptions on file."
        return "\n\n".join(_format_prescription(rx) for rx in rxs[:5])

    @tool
    def my_medical_history() -> str:
        """The patient's own past medical records: dates, diagnoses, doctor notes and ECG notes. Use my_health_summary for just the latest vitals."""
        records = health_service.history(patient_id)
        if not records:
            return "No medical records on file yet."
        lines = []
        for r in records[:10]:
            parts = [r["recorded_at"].strftime("%Y-%m-%d")]
            if r["diagnosis"]:
                parts.append(f"diagnosis: {r['diagnosis']}")
            if r["notes"]:
                parts.append(f"notes: {r['notes'][:300]}")
            if r["ecg_note"]:
                parts.append(f"ECG: {r['ecg_note']}")
            parts.append(f"recorded by {r['doctor_name']}")
            lines.append("- " + "; ".join(parts))
        return "\n".join(lines)

    @tool
    def search_health_info(query: str) -> str:
        """
        Search this clinic's small curated reference library of cardiac
        topics (e.g. what a normal ejection fraction is, what troponin
        measures, what an ECG note means). Do NOT use this for food, diet,
        fruits, medicines, or general wellness questions — answer those
        from your own knowledge instead; this tool has no data on them.
        """
        context = retrieve_context(query)
        return context or "Nothing in the clinic's reference notes matches that — answer from general knowledge instead."

    return [
        list_specialties, find_doctors, find_available_doctors, check_availability,
        start_booking_flow, my_appointments, my_past_appointments, my_health_summary,
        my_health_trend, my_prescriptions, my_medical_history, search_health_info,
    ]


# --------------------------------------------------------------------------
# Doctor tools (14) — bound to their own doctor_id, never a parameter.
# patient_health_records resolves names ONLY among this doctor's own
# patients, so another doctor's patient is unreachable by construction.
# The propose_*_report tools never write themselves — see ai/report_flow.py,
# which is the ONLY code path that actually saves/edits/deletes a report.
# --------------------------------------------------------------------------

def _doctor_tools(doctor_id: int, signals: dict) -> list:
    from langchain_core.tools import tool
    from services import appointment_service, doctor_service, health_service
    from services import prescription_service, medicine_service

    def _own_patients() -> dict:
        """{patient_id: name} for patients who have appointments with THIS
        doctor — the only population any doctor tool may reach into."""
        appts = appointment_service.list_doctor_appointments(doctor_id)
        return {a["patient_id"]: a["patient_name"] for a in appts}

    @tool
    def appointments_today() -> str:
        """The doctor's own appointments scheduled for today."""
        appts = appointment_service.list_doctor_appointments(doctor_id, on_date=date_cls.today())
        return _format_doctor_appts(appts) if appts else "No appointments today."

    @tool
    def appointments_on(date: str) -> str:
        """The doctor's own appointments on one specific date (today/tomorrow/a weekday name/YYYY-MM-DD)."""
        day = _parse_date(date)
        if day is None:
            return f"I couldn't understand the date '{date}'."
        appts = appointment_service.list_doctor_appointments(doctor_id, on_date=day)
        return _format_doctor_appts(appts) if appts else f"No appointments on {day.isoformat()}."

    @tool
    def appointments_between(start_date: str, end_date: str) -> str:
        """
        The doctor's own appointments across a date range in ONE call — use
        this for anything spanning more than a single day ('this week',
        'next 3 days'). Never call appointments_on() once per day for a range.
        """
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        if start is None or end is None:
            return "I couldn't understand one of those dates."
        appts = appointment_service.appointments_between(doctor_id, start, end)
        return _format_doctor_appts(appts) if appts else f"No appointments between {start.isoformat()} and {end.isoformat()}."

    @tool
    def my_workload() -> str:
        """Summary counts of the doctor's own appointments: today, upcoming, completed, and total unique patients."""
        appts = appointment_service.list_doctor_appointments(doctor_id)
        today = date_cls.today()
        today_n = sum(1 for a in appts if a["date"] == today)
        upcoming_n = sum(1 for a in appts if a["date"] > today and a["status"] in ("pending", "confirmed"))
        completed_n = sum(1 for a in appts if a["status"] == "completed")
        unique_n = len({a["patient_id"] for a in appts})
        return f"Today: {today_n}, Upcoming: {upcoming_n}, Completed: {completed_n}, Unique patients: {unique_n}."

    @tool
    def my_schedule() -> str:
        """The doctor's own configured weekly availability (which days/times they take appointments)."""
        slots = doctor_service.get_weekly_slots(doctor_id)
        if not slots:
            return "No weekly availability configured yet."
        return "\n".join(
            f"- {s['day']}: {s['start_time'].strftime('%H:%M')}-{s['end_time'].strftime('%H:%M')}"
            for s in slots
        )

    @tool
    def patient_conditions() -> str:
        """The doctor's own patients and each one's most recent diagnosis on file."""
        appts = appointment_service.list_doctor_appointments(doctor_id)
        unique = {a["patient_id"]: a["patient_name"] for a in appts}
        if not unique:
            return "No patients booked yet."
        lines = []
        for pid, name in unique.items():
            records = health_service.history(pid)
            latest = records[0] if records else None
            if latest and latest["diagnosis"]:
                lines.append(f"- {name}: {latest['diagnosis']} (as of {latest['recorded_at'].strftime('%Y-%m-%d')})")
            else:
                lines.append(f"- {name}: no diagnosis on file")
        return "\n".join(lines)

    @tool
    def appointment_volume(days: int = 14) -> str:
        """Count of the doctor's own appointments per day over the last N days (default 14)."""
        start = date_cls.today() - timedelta(days=days - 1)
        appts = appointment_service.appointments_between(doctor_id, start, date_cls.today())
        counts: dict = {}
        for a in appts:
            counts[a["date"]] = counts.get(a["date"], 0) + 1
        if not counts:
            return f"No appointments in the last {days} days."
        return "\n".join(f"- {d.isoformat()}: {n}" for d, n in sorted(counts.items()))

    @tool
    def patient_health_records(patient_name: str) -> str:
        """
        Full health-record history for ONE of this doctor's OWN patients,
        found by name: vitals, diagnoses, notes, ECG notes per visit.
        Only works for patients who have an appointment with this doctor.
        """
        own = _own_patients()
        match = next(
            (pid for pid, name in own.items()
             if patient_name.strip().lower() in name.lower()),
            None,
        )
        if match is None:
            return (
                f"No patient matching '{patient_name}' among your own patients. "
                "You can only view records of patients booked with you."
            )
        records = health_service.history(match)
        if not records:
            return f"{own[match]} has no health records on file."
        lines = [f"Health records for {own[match]}:"]
        for r in records[:8]:
            parts = [r["recorded_at"].strftime("%Y-%m-%d")]
            if r["heart_rate"]:
                parts.append(f"HR {r['heart_rate']}")
            if r["blood_pressure"]:
                parts.append(f"BP {r['blood_pressure']}")
            if r["ejection_fraction"]:
                parts.append(f"EF {r['ejection_fraction']}%")
            if r["troponin"] is not None:
                parts.append(f"troponin {r['troponin']}")
            if r["diagnosis"]:
                parts.append(f"diagnosis: {r['diagnosis']}")
            if r["notes"]:
                parts.append(f"notes: {r['notes'][:300]}")
            if r["ecg_note"]:
                parts.append(f"ECG: {r['ecg_note']}")
            lines.append("- " + "; ".join(parts))
        return "\n".join(lines)

    @tool
    def prescriptions_written(patient_name: str = "") -> str:
        """Prescriptions this doctor has written — optionally filtered to one of their own patients by name. Shows medicines, dosage, frequency, duration, quantity, instructions and advice."""
        pid = None
        if patient_name.strip():
            own = _own_patients()
            pid = next(
                (p for p, name in own.items()
                 if patient_name.strip().lower() in name.lower()),
                None,
            )
            if pid is None:
                return f"No patient matching '{patient_name}' among your own patients."
        rxs = prescription_service.list_by_doctor(doctor_id, patient_id=pid)
        if not rxs:
            return "No prescriptions found."
        return "\n\n".join(_format_prescription(rx) for rx in rxs[:5])

    @tool
    def medicine_stock(search: str = "") -> str:
        """The pharmacy catalog: medicine names, price per unit and current stock. Optionally filter by (partial) medicine name. Use before prescribing to check availability."""
        meds = medicine_service.list_medicines(active_only=True, search=search)
        if not meds:
            return "No medicines match that in the catalog."
        return "\n".join(
            f"- {m['name']} ({m['description'] or '—'}): ₹{m['price']:.2f}/unit, "
            f"{m['stock_quantity']} in stock" + (" [LOW]" if m["low_stock"] else "")
            for m in meds[:25]
        )

    @tool
    def my_profile() -> str:
        """This doctor's own profile: specialty, experience, consultation fee and bio."""
        p = doctor_service.get_doctor_profile(doctor_id)
        if not p:
            return "Profile not found."
        return (
            f"Dr. {p['name']} — {p['specialty']}, {p['experience_years']} yrs experience, "
            f"fee ₹{p['fee']:.0f}. Bio: {p['bio'] or '—'}"
        )

    @tool
    def propose_create_report(
        patient_name: str, diagnosis: str = "", advice_note: str = "",
        medicine_name: str = "", dosage: str = "", frequency: str = "",
        duration: str = "", quantity: int = 0, instructions: str = "",
    ) -> str:
        """
        Prepare a NEW patient report — diagnosis, medicines, advice note,
        and optionally vitals — for one of this doctor's OWN patients,
        found by name. This is the exact same report the doctor fills in
        manually on the Patient report tab, and it downloads as the same
        PDF. It never saves anything itself: it opens that same form,
        pre-filled with these fields, for the doctor to review — add more
        medicines, fix anything wrong, fill in vitals — and click Save on
        themselves. Only works for patients who have an appointment with
        this doctor. If medicine_name matches something in the pharmacy
        catalog it's added as the first line; leave it blank if the
        doctor didn't mention a medicine yet, they can add one from the
        form. Call this whenever the doctor wants to create, write, or
        save a report for a patient.
        """
        own = _own_patients()
        match = next(
            (pid for pid, name in own.items()
             if patient_name.strip().lower() in name.lower()),
            None,
        )
        if match is None:
            return f"No patient matching '{patient_name}' among your own patients."

        draft_item = None
        if medicine_name.strip():
            meds = medicine_service.list_medicines(active_only=True, search=medicine_name)
            if meds:
                m = meds[0]
                draft_item = {
                    "medicine_id": m["id"], "medicine_name": m["name"],
                    "dosage": dosage, "frequency": frequency, "duration": duration,
                    "quantity": max(int(quantity), 0), "instructions": instructions,
                }

        signals["open_report_form"] = {
            "mode": "create",
            "data": {
                "patient_id": match, "patient_name": own[match],
                "diagnosis": diagnosis, "advice_note": advice_note,
                "draft_item": draft_item,
            },
        }
        return f"Prepared a report for {own[match]} — review it in the panel below."

    @tool
    def propose_update_report(
        patient_name: str, diagnosis: Optional[str] = None, advice_note: Optional[str] = None,
    ) -> str:
        """
        Prepare an edit to this doctor's MOST RECENT saved report for one
        of their own patients, found by name — diagnosis and/or advice
        note only (medicine lines on a saved report can't be changed this
        way; vitals stay editable from the form itself). This never saves
        anything itself: it opens the same review form the doctor uses
        manually, pre-filled with the current report, for them to check
        and click Save on. Only pass the fields that should change.
        """
        own = _own_patients()
        match = next(
            (pid for pid, name in own.items()
             if patient_name.strip().lower() in name.lower()),
            None,
        )
        if match is None:
            return f"No patient matching '{patient_name}' among your own patients."
        rxs = prescription_service.list_by_doctor(doctor_id, patient_id=match)
        if not rxs:
            return f"{own[match]} has no saved reports yet."
        rx = rxs[0]  # list_by_doctor is newest-first
        data = {"prescription_id": rx["id"], "patient_name": own[match]}
        if diagnosis is not None:
            data["diagnosis"] = diagnosis
        if advice_note is not None:
            data["advice_note"] = advice_note
        signals["open_report_form"] = {"mode": "edit", "data": data}
        return (
            f"Prepared changes to {own[match]}'s most recent report "
            f"({rx['created_at'].strftime('%d %b %Y')}) — review it in the panel below."
        )

    @tool
    def propose_delete_report(patient_name: str) -> str:
        """
        Prepare deleting this doctor's MOST RECENT saved report for one
        of their own patients, found by name. This never deletes anything
        itself — it opens a confirmation panel the doctor must click.
        Deleting restores any pharmacy stock the report deducted.
        """
        own = _own_patients()
        match = next(
            (pid for pid, name in own.items()
             if patient_name.strip().lower() in name.lower()),
            None,
        )
        if match is None:
            return f"No patient matching '{patient_name}' among your own patients."
        rxs = prescription_service.list_by_doctor(doctor_id, patient_id=match)
        if not rxs:
            return f"{own[match]} has no saved reports yet."
        rx = rxs[0]
        signals["open_report_form"] = {
            "mode": "delete",
            "data": {"prescription_id": rx["id"], "patient_name": own[match]},
        }
        return (
            f"Prepared deletion of {own[match]}'s most recent report "
            f"({rx['created_at'].strftime('%d %b %Y')}) — confirm in the panel below."
        )

    return [
        appointments_today, appointments_on, appointments_between, my_workload,
        my_schedule, patient_conditions, appointment_volume,
        patient_health_records, prescriptions_written, medicine_stock, my_profile,
        propose_create_report, propose_update_report, propose_delete_report,
    ]


# --------------------------------------------------------------------------
# Admin tools (14) — mirrors what the admin portal itself displays: KPIs,
# directories, the full appointment list, patient records (the portal shows
# these inline under each patient), and the medicine inventory. The
# propose_*_medicine tools never write themselves — see ai/medicine_flow.py,
# which is the ONLY code path that actually saves a catalog change.
# --------------------------------------------------------------------------

def _admin_tools(signals: dict) -> list:
    from langchain_core.tools import tool
    from services import analytics_service, doctor_service, health_service
    from services import appointment_service, medicine_service

    @tool
    def clinic_overview() -> str:
        """Hospital-wide totals: patients, doctors, appointments, and appointment status counts. Aggregates only — no individual patient data."""
        k = analytics_service.get_kpis()
        return (
            f"Patients: {k['total_patients']}, Doctors: {k['total_doctors']}, "
            f"Appointments: {k['total_appointments']} "
            f"(pending {k['pending']}, confirmed {k['confirmed']}, "
            f"completed {k['completed']}, cancelled {k['cancelled']})."
        )

    @tool
    def user_counts() -> str:
        """Count of registered users by role: patients, doctors, admins."""
        c = analytics_service.user_counts()
        return f"Patients: {c['patients']}, Doctors: {c['doctors']}, Admins: {c['admins']}."

    @tool
    def list_all_doctors() -> str:
        """Every doctor registered at this clinic, with specialty, experience and fee. Public directory information, not patient data."""
        doctors = doctor_service.list_doctors()
        if not doctors:
            return "No doctors registered yet."
        return "\n".join(
            f"- {doctor_service.display_name(d)}, {d['experience_years']} yrs exp, fee {d['fee']:.0f}"
            for d in doctors
        )

    @tool
    def doctors_available_today() -> str:
        """How many doctors currently have at least one open appointment slot today."""
        n = analytics_service.doctors_available_today()
        return f"{n} doctor(s) have open slots today."

    @tool
    def appointment_breakdown() -> str:
        """Hospital-wide appointment counts by status (pending/confirmed/completed/cancelled)."""
        b = analytics_service.appointment_status_breakdown()
        return ", ".join(f"{label}: {value}" for label, value in zip(b["labels"], b["values"]))

    @tool
    def appointment_volume(days: int = 14) -> str:
        """Hospital-wide appointment count per day over the last N days (default 14)."""
        data = analytics_service.appointments_last_n_days(days)
        return "\n".join(f"- {d.isoformat()}: {c}" for d, c in zip(data["dates"], data["counts"]))

    @tool
    def list_specialties() -> str:
        """List every medical specialty this clinic offers, with a short description."""
        specs = doctor_service.list_specialties()
        if not specs:
            return "No specialties are set up yet."
        return "\n".join(f"- {s['name']}: {s['description'] or ''}" for s in specs)

    @tool
    def list_patients(search: str = "") -> str:
        """The hospital's patient directory: names, emails, gender, phone. Optionally filter by (partial) name."""
        patients = health_service.get_all_patients()
        if search.strip():
            patients = [p for p in patients if search.strip().lower() in p["name"].lower()]
        if not patients:
            return "No patients match."
        return "\n".join(
            f"- {p['name']} · {p['email']} · {p['gender'] or '—'} · {p['phone'] or '—'}"
            for p in patients[:30]
        )

    @tool
    def patient_health_records(patient_name: str) -> str:
        """One patient's health-record history by name — dates, vitals, diagnoses (the same records the admin portal shows under each patient)."""
        patients = health_service.get_all_patients()
        match = next(
            (p for p in patients
             if patient_name.strip().lower() in p["name"].lower()),
            None,
        )
        if not match:
            return f"No patient matching '{patient_name}'."
        records = health_service.history(match["id"])
        if not records:
            return f"{match['name']} has no health records on file."
        lines = [f"Health records for {match['name']}:"]
        for r in records[:8]:
            parts = [r["recorded_at"].strftime("%Y-%m-%d")]
            if r["heart_rate"]:
                parts.append(f"HR {r['heart_rate']}")
            if r["blood_pressure"]:
                parts.append(f"BP {r['blood_pressure']}")
            if r["diagnosis"]:
                parts.append(f"diagnosis: {r['diagnosis']}")
            lines.append("- " + "; ".join(parts))
        return "\n".join(lines)

    @tool
    def all_appointments(status: str = "") -> str:
        """Hospital-wide appointment list: patient, doctor, date, time, status. Optionally filter by status (pending/confirmed/completed/cancelled)."""
        valid = {"pending", "confirmed", "completed", "cancelled"}
        s = status.strip().lower()
        appts = appointment_service.list_all_appointments(s if s in valid else None)
        if not appts:
            return "No appointments match."
        return "\n".join(
            f"- {a['date'].isoformat()} {a['start_time'].strftime('%H:%M')}: "
            f"{a['patient_name']} with Dr. {a['doctor_name']} ({a['status']})"
            for a in appts[:25]
        )

    @tool
    def medicines_inventory(search: str = "") -> str:
        """The pharmacy inventory: medicine names, price per unit, units in stock, and which are low on stock. Optionally filter by (partial) name."""
        meds = medicine_service.list_medicines(active_only=True, search=search)
        if not meds:
            return "No medicines match that in the catalog."
        total_value = sum(m["price"] * m["stock_quantity"] for m in meds)
        lines = [
            f"- {m['name']} ({m['description'] or '—'}): ₹{m['price']:.2f}/unit, "
            f"{m['stock_quantity']} in stock" + (" [LOW]" if m["low_stock"] else "")
            for m in meds[:25]
        ]
        lines.append(f"Total inventory value: ₹{total_value:,.0f}")
        return "\n".join(lines)

    @tool
    def propose_add_medicine(
        name: str, description: str = "", price: float = 0.0,
        stock_quantity: int = 0, specialty: str = "",
    ) -> str:
        """
        Prepare a NEW medicine to add to the pharmacy catalog, from the
        admin's free-text request (e.g. "add Paracetamol 500mg tablet,
        price 5, stock 200"). This never writes to the catalog itself —
        it opens the same review form the admin uses manually, pre-filled
        with these fields, for the admin to check and click Save on
        themselves. Call this whenever the admin wants to add, create, or
        stock a new medicine.
        """
        if not name.strip():
            return "I need at least a medicine name to prepare this."
        signals["open_medicine_form"] = {
            "mode": "add",
            "data": {
                "name": name.strip(),
                "description": description.strip(),
                "price": price,
                "stock_quantity": max(int(stock_quantity), 0),
                "specialty": _match_specialty_name(specialty) if specialty else None,
            },
        }
        return f"Prepared a form to add '{name.strip()}' to the catalog — review it in the panel below."

    @tool
    def propose_update_medicine(
        medicine_name: str, price: Optional[float] = None,
        stock_quantity: Optional[int] = None, specialty: str = "",
    ) -> str:
        """
        Prepare a price, stock, and/or specialty change to an EXISTING
        medicine, matched by name. This never writes anything itself — it
        opens the same review form the admin uses manually, pre-filled
        with the proposed changes, for the admin to check and click Save
        on themselves. Only pass the fields that should change; leave the
        rest out. Note: stock_quantity here SETS the stock to this exact
        number — if the admin means "add N units" rather than "set stock
        to N", compute the new total yourself from medicines_inventory
        first.
        """
        meds = medicine_service.list_medicines(active_only=True, search=medicine_name)
        if not meds:
            return f"No medicine matching '{medicine_name}' in the catalog."
        med = meds[0]
        data = {"medicine_id": med["id"]}
        if price is not None:
            data["price"] = price
        if stock_quantity is not None:
            data["stock_quantity"] = max(int(stock_quantity), 0)
        if specialty.strip():
            matched = _match_specialty_name(specialty)
            if matched:
                data["specialty"] = matched
        signals["open_medicine_form"] = {"mode": "edit", "data": data}
        return f"Prepared changes for '{med['name']}' — review them in the panel below."

    @tool
    def propose_remove_medicine(medicine_name: str) -> str:
        """
        Prepare removing a medicine from the catalog, matched by name.
        This never deletes anything itself — it opens a confirmation
        panel the admin must click to actually remove it. The medicine
        stays visible on any past prescription; it's only hidden from the
        active catalog and pharmacy shop.
        """
        meds = medicine_service.list_medicines(active_only=True, search=medicine_name)
        if not meds:
            return f"No medicine matching '{medicine_name}' in the catalog."
        med = meds[0]
        signals["open_medicine_form"] = {"mode": "delete", "data": {"medicine_id": med["id"]}}
        return f"Prepared removal of '{med['name']}' — confirm in the panel below."

    return [
        clinic_overview, user_counts, list_all_doctors, doctors_available_today,
        appointment_breakdown, appointment_volume, list_specialties,
        list_patients, patient_health_records, all_appointments, medicines_inventory,
        propose_add_medicine, propose_update_medicine, propose_remove_medicine,
    ]


# --------------------------------------------------------------------------
# Assembling and running the agent — one engine, three permission sets.
# --------------------------------------------------------------------------

def build_tools(user: dict, signals: dict) -> list:
    """Return the toolset for this user's role, bound to their own id via
    closures — never as a tool parameter the model could fill in itself.
    An empty list means the chatbot has nothing it's allowed to do for
    this account (e.g. a doctor login with no linked doctor profile yet)."""
    role = user.get("role")
    if role == ROLE_DOCTOR:
        from services.doctor_service import get_doctor_by_user
        doc = get_doctor_by_user(user["id"])
        if not doc:
            return []
        return _doctor_tools(doc["doctor_id"], signals)
    if role == ROLE_ADMIN:
        return _admin_tools(signals)
    return _patient_tools(user["id"], signals)


def build_agent(user: dict, signals: dict, language: str = DEFAULT_LANGUAGE):
    from langchain.agents import create_agent

    tools = build_tools(user, signals)
    if not tools:
        return None
    return create_agent(model=get_llm(), tools=tools, system_prompt=_system_prompt(user, language))


def _make_callback(user: dict):
    """Fires on_tool_start / on_tool_end / on_tool_error around every tool
    call the agent makes — this is the entire audit trail. If a patient
    says "the bot showed me the wrong slot", these log lines are the only
    record of what the model actually asked for and what the DB actually
    returned."""
    from langchain_core.callbacks import BaseCallbackHandler

    class ToolTracer(BaseCallbackHandler):
        def on_tool_start(self, serialized, input_str, **kwargs):
            log.info(
                "tool_start user=%s role=%s tool=%s args=%s",
                user.get("id"), user.get("role"),
                (serialized or {}).get("name", "?"), str(input_str)[:200],
            )

        def on_tool_end(self, output, **kwargs):
            log.info("tool_end user=%s output=%s", user.get("id"), str(output)[:200])

        def on_tool_error(self, error, **kwargs):
            log.error("tool_error user=%s error=%s", user.get("id"), error)

    return ToolTracer()


def _to_lc_history(history: list) -> list:
    from langchain_core.messages import HumanMessage, AIMessage

    messages = []
    for m in history or []:
        if m.get("role") == "assistant":
            messages.append(AIMessage(content=m.get("content", "")))
        else:
            messages.append(HumanMessage(content=m.get("content", "")))
    return messages


def _is(exc: Exception, patterns) -> bool:
    text = str(exc).lower()
    return any(p in text for p in patterns)


def _invoke(agent, messages: list, user: dict) -> dict:
    """Wraps agent.invoke(...) with the retry and recursion-salvage logic.
    tool_use_failed is a sampling artefact on some models (~50% on some
    prompts) — a fresh attempt usually works, so we retry it. A 429 rate
    limit is never retried, since retrying just deepens the backoff."""
    try:
        from langgraph.errors import GraphRecursionError
    except Exception:
        class GraphRecursionError(Exception):
            pass

    config = {"callbacks": [_make_callback(user)], "recursion_limit": _RECURSION_LIMIT}

    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return agent.invoke({"messages": messages}, config=config)
        except GraphRecursionError:
            log.warning("recursion limit hit for user=%s — salvaging from partial state", user.get("id"))
            return _salvage(messages, user)
        except Exception as exc:
            last_exc = exc
            if _is(exc, _RATE_LIMITED):
                raise
            if _is(exc, _TOOL_USE_FAILED) and attempt < _MAX_RETRIES - 1:
                log.info("tool_use_failed for user=%s, retrying (%d/%d)", user.get("id"), attempt + 1, _MAX_RETRIES)
                continue
            raise
    raise last_exc


def _salvage(messages: list, user: dict) -> dict:
    """The model looped (e.g. called a single-day tool once per day instead
    of a range tool) and hit the recursion backstop. Rather than fail
    outright, ask it to answer from whatever tool results are already in
    `messages` — with NO tools bound this time, so it cannot loop again."""
    from langchain_core.messages import SystemMessage

    salvage_prompt = SystemMessage(content=(
        "You've reached the end of your tool budget for this turn. Answer "
        "the user's question as best you can using only the tool results "
        "already above — do not ask for any more tools."
    ))
    llm = get_llm()
    reply = llm.invoke([salvage_prompt] + messages)
    return {"messages": messages + [reply]}


def ask(user: dict, message: str, history: list = None, language: str = DEFAULT_LANGUAGE):
    """
    Returns (answer, signals). Never raises — a user should never see a
    traceback. `signals` is how a tool talks back to the UI: start_booking
    (from start_booking_flow), open_medicine_form (from the
    propose_*_medicine tools) and open_report_form (from the
    propose_*_report tools). `language` is one of ai/languages.py's
    dropdown keys (views/chatbot_view.py) — it sets the fallback/voice
    locale, but the system prompt (_language_instruction) always prefers
    matching whatever language the person's own message is actually in.
    """
    from langchain_core.messages import HumanMessage

    signals: dict = {}

    if not is_configured():
        return (
            "The AI assistant isn't connected yet — add a `GROQ_API_KEY` to "
            "your `.env` file to enable it. In the meantime, use the other "
            "tabs in your dashboard for anything urgent.",
            signals,
        )

    try:
        agent = build_agent(user, signals, language)
    except Exception:
        log.exception("failed to build agent for user=%s", user.get("id"))
        return "Sorry, I couldn't start the assistant. Please try again shortly.", signals

    if agent is None:
        return "No assistant tools are available for your account.", signals

    messages = _to_lc_history(history) + [HumanMessage(content=message)]
    try:
        result = _invoke(agent, messages, user)
    except Exception as exc:
        log.exception("agent invocation failed for user=%s", user.get("id"))
        if _is(exc, _RATE_LIMITED):
            return "I've hit the AI provider's rate limit. Please wait a moment and try again.", signals
        return "Sorry, I ran into a problem reaching the assistant.", signals

    out = result.get("messages", []) if isinstance(result, dict) else []
    answer = getattr(out[-1], "content", "") if out else ""
    return (answer or "Sorry, I couldn't process that."), signals


def assistant_status() -> str:
    return "connected" if is_configured() else "offline (no GROQ_API_KEY set)"
