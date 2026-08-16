"""
views/components.py
Small reusable render helpers shared by every dashboard view.
"""
import base64
import mimetypes
import os
import streamlit as st
import streamlit.components.v1 as components

# Bundled static illustrations (not user-uploaded, not tied to any DB
# row) live in assets/images/ — separate from assets/medicines/, which
# is user-uploaded medicine photos (see services/medicine_service.py).
_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "images")


def _asset_data_uri(filename: str):
    """Reads a bundled image from assets/images/ and returns it as a
    base64 data URI for embedding in raw HTML. Streamlit doesn't serve
    arbitrary file paths the way a normal web server does, so a plain
    <img src="assets/..."> tag inside injected HTML won't load — this is
    the same workaround services/medicine_service.py's
    image_src_for_html() uses for uploaded medicine photos. Returns None
    (never raises) if the file is missing, so a missing decorative asset
    degrades quietly instead of crashing the page."""
    path = os.path.join(_ASSET_DIR, filename)
    if not os.path.exists(path):
        return None
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# Loaded once at import time — it's a small, fixed set of illustrations,
# not per-request data, so there's no need to re-read/re-encode on every
# render.
_HEART_VITALS_URI = _asset_data_uri("heart_vitals.png")

_ECG_SVG = """
<div class="ecg-divider">
<svg viewBox="0 0 600 60" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M0 30 H150 L165 10 L180 50 L195 15 L210 45 L225 30 H600
           M600 30 H750 L765 10 L780 50 L795 15 L810 45 L825 30 H1200"
        fill="none" stroke="#E1614A" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
</div>
"""


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "assets", "styles.css")
    with open(css_path, "r") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def ecg_html() -> str:
    """Raw ECG divider markup — use this to embed the divider inside a
    single combined HTML string (e.g. building a whole card in one
    st.markdown call). Stripped of leading/trailing blank lines, because
    a blank line in the middle of an st.markdown() HTML string makes
    Streamlit's Markdown parser think the HTML block ended there — every
    thing after it then renders as literal text (and often as a stray
    "code block" if the following lines happen to be indented).
    Use ecg_divider() instead for a standalone one."""
    return _ECG_SVG.strip()


def ecg_divider():
    st.markdown(_ECG_SVG, unsafe_allow_html=True)


def empty_state(icon: str, title: str, message: str = None, col=None):
    """A styled placeholder for "nothing here yet" screens — dashed
    border, centered icon/title/message — used instead of a plain
    st.info() wherever an empty list/table would otherwise look broken
    rather than intentional. Purely presentational; callers still decide
    when there's nothing to show."""
    target = col if col is not None else st
    message_html = f'<p class="empty-state-msg">{message}</p>' if message else ""
    target.markdown(
        f"""<div class="empty-state">
                <div class="empty-state-icon">{icon}</div>
                <p class="empty-state-title">{title}</p>
                {message_html}
            </div>""",
        unsafe_allow_html=True,
    )


def report_preview(report: dict, height: int = 640):
    """Renders a patient report exactly as it will print/download — reuses
    services/report_pdf.py's own HTML template (the SAME template
    xhtml2pdf converts into the downloadable PDF), so a preview can never
    structurally drift from the real PDF. Rendered inside an isolated
    iframe (components.html) rather than st.markdown, since the template
    is a full standalone <html> document with its own <style> block that
    would otherwise leak into (and clash with) the app's own CSS.
    `report` is either a real saved report (services/report_service.py's
    get_report()) or an in-progress draft dict built from a form's
    current values — render_report_html() doesn't care which."""
    from services.report_pdf import render_report_html
    html = render_report_html(report)
    components.html(html, height=height, scrolling=True)


def button_tabs(items, key: str, default: str = None) -> str:
    """A horizontal row of buttons that behaves like st.tabs() — click one,
    its content shows — but is fully restyleable, since st.tabs() itself
    exposes almost nothing for custom CSS to target. Persists the active
    item in session_state across reruns, the same mechanic sidebar_nav()
    already uses for the sidebar (button per item, "primary" style = the
    active one), just arranged in columns instead of stacked.

    items: list of str for the common case (label IS the identity), or
    list of (value, label) tuples when the display label needs to change
    between reruns but the active tab shouldn't reset because of it —
    e.g. a cart item count in the label ("Cart (3)"): using the full
    label as the tracking key would silently jump back to the first tab
    every time the count changes, since last rerun's label would no
    longer match anything in the new items list.
    """
    pairs = [(i, i) if isinstance(i, str) else i for i in items]
    values = [v for v, _ in pairs]

    state_key = f"tabbtn_{key}"
    if state_key not in st.session_state or st.session_state[state_key] not in values:
        st.session_state[state_key] = default if default in values else values[0]

    cols = st.columns(len(pairs))
    for col, (value, label) in zip(cols, pairs):
        is_active = st.session_state[state_key] == value
        if col.button(
            label, key=f"{state_key}_{value}", use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state[state_key] = value
            st.rerun()

    return st.session_state[state_key]


def kpi_tile(label: str, value, col=None, caption: str = None, dark: bool = False, variant: str = None):
    """variant: None (light, default), "dark", or "accent" (clay-toned —
    handy for drawing the eye to a tile that needs attention, e.g. a low
    stock count). `dark=True` is kept for backward compatibility and is
    equivalent to variant="dark"; existing call sites need no changes."""
    target = col if col is not None else st
    caption_html = f'<div class="kpi-caption">{caption}</div>' if caption else ""
    resolved_variant = variant or ("dark" if dark else None)
    tile_class = "kpi-tile"
    if resolved_variant == "dark":
        tile_class += " kpi-tile-dark"
    elif resolved_variant == "accent":
        tile_class += " kpi-tile-accent"
    target.markdown(
        f"""<div class="{tile_class}">
                <div class="kpi-label">{label}</div>
                <div class="kpi-value">{value}</div>{caption_html}
            </div>""",
        unsafe_allow_html=True,
    )


def status_badge(status: str) -> str:
    status = (status or "pending").lower()
    return f'<span class="badge badge-{status}">{status.capitalize()}</span>'


def doctor_card(doc: dict, col=None):
    target = col if col is not None else st
    initials = "".join([p[0] for p in doc["name"].split()[:2]]).upper()
    bio_html = f'<p class="doc-bio">{doc["bio"]}</p>' if doc.get("bio") else ""
    target.markdown(
        f"""<div class="doc-card">
                <div class="doc-card-top">
                    <div class="doc-avatar">{initials}</div>
                    <div>
                        <p class="doc-name">Dr. {doc['name']}</p>
                        <p class="doc-meta">{doc['experience_years']} yrs experience &middot; ₹{doc['fee']:.0f} fee</p>
                        <span class="pill">{doc['specialty']}</span>
                    </div>
                </div>
                {bio_html}
            </div>""",
        unsafe_allow_html=True,
    )


def doctor_grid(doctors: list[dict], columns: int = 2):
    """Lays doctor_card tiles out in a responsive card grid instead of a single stacked list."""
    rows = [doctors[i:i + columns] for i in range(0, len(doctors), columns)]
    for row in rows:
        cols = st.columns(columns)
        for doc, col in zip(row, cols):
            doctor_card(doc, col)


def vitals_compass(latest: dict):
    """Renders the patient's four headline vitals arranged around the
    heart-and-stethoscope illustration — heart rate above, blood pressure
    below, SpO2 and ejection fraction to either side — instead of a plain
    row of cards. `latest` is health_service.get_latest_vitals()'s dict,
    or None/{} if the patient has no records yet; each chip shows a
    placeholder dash rather than the whole component disappearing, so the
    layout (and the illustration) is still there to greet a new patient
    with no history."""
    latest = latest or {}
    hr = latest.get("heart_rate")
    bp = latest.get("blood_pressure")
    spo2 = latest.get("pulse_oximetry")
    ef = latest.get("ejection_fraction")

    def chip(label: str, value, unit: str = "") -> str:
        display = f"{value}{unit}" if value not in (None, "") else "—"
        return f"""<div class="vc-chip">
                <div class="vc-chip-label">{label}</div>
                <div class="vc-chip-value">{display}</div>
            </div>"""

    if _HEART_VITALS_URI:
        heart_html = (
            f'<img class="vc-heart-img" src="{_HEART_VITALS_URI}" '
            'alt="Heart vitals illustration">'
        )
    else:
        # If the bundled asset is missing, keep a simple text placeholder
        # rather than showing the old emoji fallback.
        heart_html = '<div class="vc-heart-fallback">Heart vitals</div>'

    st.markdown(
        f"""<div class="vitals-compass">
            <div class="vc-cell vc-top">{chip("Heart rate", hr, " bpm")}</div>
            <div class="vc-cell vc-left">{chip("SpO&#8322;", spo2, "%")}</div>
            <div class="vc-cell vc-center">
                <div class="vc-heart-wrap">{heart_html}</div>
            </div>
            <div class="vc-cell vc-right">{chip("Ejection fraction", ef, "%")}</div>
            <div class="vc-cell vc-bottom">{chip("Blood pressure", bp)}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def vital_metric(label: str, value, unit: str = "", col=None):
    target = col if col is not None else st
    target.markdown(
        f"""<div class="sc-card" style="text-align:center;">
                <div class="vital-label">{label}</div>
                <div class="vital-num">{value if value is not None else '—'}{unit}</div>
            </div>""",
        unsafe_allow_html=True,
    )


def sidebar_nav(items: list[str], nav_key: str, on_logout=None) -> str:
    """
    Renders a vertical, hover-highlighted nav list in the sidebar (larger
    font than normal Streamlit buttons — see .stSidebar button rules in
    styles.css). Must be called from inside a `with st.sidebar:` block.
    Returns the currently-selected item. If "Logout" is clicked and
    on_logout is provided, it's called immediately instead of being
    returned as a section.
    """
    state_key = f"nav_{nav_key}"
    if state_key not in st.session_state or st.session_state[state_key] not in items:
        st.session_state[state_key] = items[0]

    for item in items:
        is_active = st.session_state[state_key] == item
        if st.button(
            item, key=f"{state_key}_{item}", use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            if item == "Logout" and on_logout:
                on_logout()
            else:
                st.session_state[state_key] = item
                st.rerun()

    return st.session_state[state_key]


# Layered, more saturated backgrounds per section — a linear gradient base
# plus radial accent "pops" in the palette's clay/sage/ink tones, so each
# screen feels distinct and a little more vivid instead of flat pastel.
_SECTION_BACKGROUNDS = {
    "My Health 🫀": (
        "radial-gradient(circle at 88% 6%, rgba(225,97,74,0.16) 0%, transparent 42%),"
        "radial-gradient(circle at 6% 94%, rgba(62,124,110,0.22) 0%, transparent 48%),"
        "linear-gradient(165deg,#F7F5EF 0%,#E3F0EA 50%,#CDE6DA 100%)"
    ),
    "Appointments": (
        "radial-gradient(circle at 90% 10%, rgba(62,124,110,0.14) 0%, transparent 42%),"
        "radial-gradient(circle at 8% 90%, rgba(225,97,74,0.20) 0%, transparent 48%),"
        "linear-gradient(165deg,#FDF6EC 0%,#F7EEDF 50%,#F0DCC7 100%)"
    ),
    "Doctors": (
        "radial-gradient(circle at 90% 8%, rgba(225,97,74,0.14) 0%, transparent 42%),"
        "radial-gradient(circle at 8% 92%, rgba(62,124,110,0.24) 0%, transparent 48%),"
        "linear-gradient(165deg,#F2F8F6 0%,#DFEFE8 50%,#C6E1D5 100%)"
    ),
    "Doctor Portal": (
        "radial-gradient(circle at 88% 8%, rgba(225,97,74,0.14) 0%, transparent 42%),"
        "radial-gradient(circle at 8% 92%, rgba(62,124,110,0.24) 0%, transparent 48%),"
        "linear-gradient(165deg,#F4F9F6 0%,#E1F0E8 50%,#C8E4D6 100%)"
    ),
    "Admin Console": (
        "radial-gradient(circle at 90% 10%, rgba(14,59,54,0.16) 0%, transparent 42%),"
        "radial-gradient(circle at 8% 90%, rgba(225,97,74,0.12) 0%, transparent 48%),"
        "linear-gradient(165deg,#F5F7F2 0%,#E4EBE2 50%,#CBDACB 100%)"
    ),
    "Smart Care AI": (
        "radial-gradient(circle at 90% 8%, rgba(62,124,110,0.18) 0%, transparent 42%),"
        "radial-gradient(circle at 8% 92%, rgba(225,97,74,0.14) 0%, transparent 48%),"
        "linear-gradient(165deg,#F7F5FB 0%,#E9EBF5 50%,#D6DEEE 100%)"
    ),
    "Medications": (
        "radial-gradient(circle at 88% 10%, rgba(62,124,110,0.16) 0%, transparent 42%),"
        "radial-gradient(circle at 10% 90%, rgba(225,97,74,0.16) 0%, transparent 48%),"
        "linear-gradient(165deg,#F5F9F4 0%,#E7F0E3 50%,#D3E5CF 100%)"
    ),
    "Pharmacy 💊": (
        "radial-gradient(circle at 88% 10%, rgba(62,124,110,0.16) 0%, transparent 42%),"
        "radial-gradient(circle at 10% 90%, rgba(225,97,74,0.16) 0%, transparent 48%),"
        "linear-gradient(165deg,#F5F9F4 0%,#E7F0E3 50%,#D3E5CF 100%)"
    ),
}
_DEFAULT_BACKGROUND = (
    "radial-gradient(circle at 15% 15%, rgba(62,124,110,0.14) 0%, transparent 42%),"
    "radial-gradient(circle at 88% 88%, rgba(225,97,74,0.12) 0%, transparent 48%),"
    "linear-gradient(165deg,#F7F5EF 0%,#EFEBE0 100%)"
)


def set_page_background(section: str):
    gradient = _SECTION_BACKGROUNDS.get(section, _DEFAULT_BACKGROUND)
    st.markdown(f"<style>.stApp{{ background:{gradient}; }}</style>", unsafe_allow_html=True)