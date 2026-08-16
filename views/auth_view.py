"""
views/auth_view.py
Two-panel login / register screen — a dark hero panel with feature
highlights next to a card-style sign-in/create-account form. Sets
st.session_state.user on success.
"""

import os
from datetime import date

import streamlit as st

from core.config import settings
from services.auth_service import register_user, login_user, AuthError
from services.doctor_service import list_specialties
from views.components import ecg_divider, ecg_html

_FEATURES = [
    ("📅", "Book in seconds", "Find a specialist and grab an open slot without a phone call."),
    ("🫀", "Track your vitals", "Heart rate, BP, SpO₂ and ECG notes, all in one place."),
    ("💬", "Ask Smart Care AI", "A role-aware assistant that only ever sees what you're allowed to."),
]

_STATS = [("10+", "Specialists"), ("24/7", "Portal access"), ("100%", "Role-based data")]

# Single-codepoint emoji only — multi-codepoint ZWJ sequences (e.g. the
# "people holding hands" emoji) render inconsistently across Windows/
# browser font combos and can show up as a broken/generic fallback glyph
# instead of the intended icon. Stick to simple, widely-supported emoji
# here rather than anything decorative that risks looking broken.
_ROLE_ICONS = {"Patient": "🧑 Patient", "Doctor": "🩺 Doctor"}

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_LOGO_PATH = os.path.join(_ASSET_ROOT, "logo.png")


def render():
    st.markdown(
        """<style>
        .stApp{
            background:
              radial-gradient(circle at 18% 20%, rgba(62,124,110,0.20) 0%, transparent 45%),
              radial-gradient(circle at 85% 80%, rgba(225,97,74,0.18) 0%, transparent 50%),
              linear-gradient(165deg,#F7F5EF 0%,#EFEBE0 50%,#E7E0CE 100%);
        }
        /* Login page only: darker/higher-contrast text so nothing blends
           into the card or hero panel — the app-wide Inter/Fraunces type
           system (assets/styles.css) still applies here as everywhere
           else; this block only tightens color, not typeface. */
        .auth-eyebrow, .auth-title, .auth-sub{ color:#0A2E2A !important; }
        .auth-hero .tagline{ color:#000000 !important; }
        .auth-feature-text span{ color:#000000 !important; }
        .auth-stat-label{ color:#000000 !important; }
        .stTextInput input, .stTextArea textarea{ color:#0A0F0E !important; font-weight:700 !important; }
        [data-testid="stWidgetLabel"] p{ color:#0A2E2A !important; }
        /* The auth card is a real st.container(border=True) — style that
           specific wrapper here so it doesn't affect bordered containers
           on other pages. A deeper resting shadow than the app default
           (var(--shadow-lg) rather than the generic --shadow-md) makes
           this one card feel deliberately elevated, since it's the only
           thing on the page competing with the hero panel for attention. */
        [data-testid="stVerticalBlockBorderWrapper"]{
            background:#FFFFFF !important;
            border-radius:18px !important;
            box-shadow:var(--shadow-lg);
        }
        /* The hero column (left) is naturally shorter than the form
           column (right) — the Doctor signup path in particular adds
           enough fields that the form runs well past the hero panel's
           height, which left a large empty gap under the hero card on
           any viewport tall enough to show it. Pinning the hero column
           instead of letting it float at a fixed position keeps it in
           view as the form scrolls, so there's never dead space beneath
           it regardless of which tab/role is showing. Targets the FIRST
           column specifically (the hero), not the form column next to
           it — data-testid="column" is shared by both, so scoping via
           nth-of-type is required here.
        */
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-of-type(1){
            position: sticky;
            top: 1.5rem;
            align-self: flex-start;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    hero_col, card_col = st.columns([1, 1.15], gap="large")

    with hero_col:
        _hero_panel()

    with card_col:
        with st.container(border=True):
            st.markdown(
                f'<div style="text-align: center; margin-bottom: 1.5rem;"><p class="auth-eyebrow">{settings.APP_NAME}</p></div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div style="text-align: center;"><p class="auth-title">Welcome back</p><p class="auth-sub">Sign in to reach your care team, records and appointments.</p></div>',
                unsafe_allow_html=True,
            )
            ecg_divider()

            tab_login, tab_register = st.tabs(["🔐 Sign in", "📝 Create account"])

            with tab_login:
                _login_form()

            with tab_register:
                _register_form()


def _hero_panel():
    features_html = "".join(
        f"""<div class="auth-feature">
                <div class="auth-feature-icon">{icon}</div>
                <div class="auth-feature-text"><strong>{title}</strong><span>{desc}</span></div>
            </div>"""
        for icon, title, desc in _FEATURES
    )
    stats_html = "".join(
        f'<div><div class="auth-stat-num">{num}</div><div class="auth-stat-label">{label}</div></div>'
        for num, label in _STATS
    )

    # Built and injected as ONE markdown call so the whole panel is a single
    # real HTML fragment — opening/closing the div across separate
    # st.markdown() calls would leave it empty, since each call is its own
    # isolated DOM fragment in Streamlit.
    st.markdown(
        f"""<div class="auth-hero">
                <h1>🫀 {settings.APP_NAME}</h1>
                <p class="tagline">Cardiac-first care, connected for patients, doctors and admins.</p>
                {ecg_html()}
                {features_html}
                <div class="auth-stats">{stats_html}</div>
            </div>""",
        unsafe_allow_html=True,
    )


def _login_form():
    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("📧 Email", placeholder="you@example.com")
        password = st.text_input("🔒 Password", type="password", placeholder="••••••••")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

    st.caption("Forgot your password? Ask your hospital admin to reset it.")

    if submitted:
        if not email or not password:
            st.error("Enter both your email and password.")
            return
        try:
            user = login_user(email, password)
            st.session_state.user = user
            st.success(f"Welcome back, {user.full_name.split()[0]}.")
            st.rerun()
        except AuthError as e:
            st.error(str(e))


def _register_form():
    role = st.selectbox("I am a...", ["Patient", "Doctor"], format_func=lambda r: _ROLE_ICONS[r], key="reg_role")

    with st.form("register_form", clear_on_submit=False):
        full_name = st.text_input("👤 Full name")
        email = st.text_input("📧 Email", key="reg_email")
        col1, col2 = st.columns(2)
        with col1:
            password = st.text_input(
                "🔒 Password", type="password", key="reg_pw",
                help="At least 8 characters, including one number and one special character.",
            )
        with col2:
            confirm = st.text_input("🔒 Confirm password", type="password", key="reg_pw2")
        col3, col4 = st.columns(2)
        with col3:
            gender = st.selectbox("⚧ Gender", ["Prefer not to say", "Female", "Male", "Other"])
        with col4:
            dob = st.date_input(
                "🎂 Date of birth", value=date(1995, 1, 1),
                min_value=date(1920, 1, 1), max_value=date.today(),
                help="This defaults to a placeholder date — make sure to set your actual date of birth.",
            )
        phone = st.text_input("📱 Phone number")

        specialty_id, experience_years, fee, bio = None, None, None, None
        if role == "Doctor":
            st.markdown("**🩺 Professional details**")
            specs = list_specialties()
            spec_names = [s["name"] for s in specs] or ["General Medicine"]
            chosen = st.selectbox("Specialty", spec_names)
            specialty_id = next((s["id"] for s in specs if s["name"] == chosen), None)
            colx, coly = st.columns(2)
            with colx:
                experience_years = st.number_input("Years of experience", 0, 60, 1)
            with coly:
                fee = st.number_input("Consultation fee (₹)", 0, 20000, 500)
            bio = st.text_area("Short bio", placeholder="e.g. Interventional cardiologist focused on...")

        submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)

    if submitted:
        if not full_name or not email or not password:
            st.error("Full name, email and password are required.")
            return
        if password != confirm:
            st.error("Passwords do not match.")
            return
        try:
            user = register_user(
                full_name=full_name, email=email, password=password,
                role=role.lower(), gender=gender, dob=dob, phone=phone,
                specialty_id=specialty_id, experience_years=experience_years,
                consultation_fee=fee, bio=bio,
            )
            st.session_state.user = user
            st.success("Account created. Redirecting to your dashboard...")
            st.rerun()
        except AuthError as e:
            st.error(str(e))