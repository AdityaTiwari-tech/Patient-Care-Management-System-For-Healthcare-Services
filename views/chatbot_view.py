"""
views/chatbot_view.py
The Smart Care AI chat surface. Captures the message (typed, spoken, OR a
patient's uploaded health report or medicine photo), calls
ai.smartcare_agent.ask(), renders the reply (optionally read aloud), and
— when the agent's signals dict says so — opens one of the deterministic
wizards (ai/booking_flow.py, ai/medicine_flow.py, ai/report_flow.py).
This file never touches the database directly; history persistence goes
through services/chat_service.py.

    st.chat_input / mic / file  -> ask(user_dict, prompt, history, language) -> (answer, signals)

Language: a dropdown (see ai/languages.py) lets the person set a display
language, used as the Whisper transcription hint and the read-aloud voice
locale. The model itself (ai/smartcare_agent.py's _language_instruction)
always prefers matching whatever language the person's own message is
actually written or spoken in — Hindi, Tamil, Telugu, or any other Indian
language, in its native script — so the dropdown is a fallback/voice
setting, not a hard switch the person must keep in sync with what they type.

Speech: mic input and read-aloud output both work independently of each
other, for every role, in normal chat. A dedicated 🎙️ Mic toggle in the
header (separate from Voice mode) controls whether the microphone
recorder renders anywhere on the page at all — off means the browser is
never even asked for mic permission, and every input path silently falls
back to typing; on (the default) restores the existing mic behavior
below. Voice mode (the header's separate "Voice mode" toggle) turns
things into a real spoken exchange: activating it greets the person by
name ("Hey {first name}, how can I assist you today?" — see
_first_name()/render()'s voice_just_activated handling), spoken aloud and
added to chat history like any other assistant message; the mic then
becomes the PRIMARY input (typing still works as a fallback right below
it, and is the ONLY input if Mic is off); and every reply is read back
automatically. Same _mic_input()/_handle_turn() pipeline as normal chat
throughout — voice mode changes framing/ordering and adds the one-time
greeting, not the underlying mechanics.
  - Input: st.audio_input records mic audio in the browser; the bytes are
    sent to ai/speech.py (Groq Whisper) for transcription, with the
    dropdown's language as an accuracy hint, and the result is treated
    exactly like a typed prompt — same _handle_turn() path, same history,
    same signals handling. Requires Streamlit >= 1.38 for st.audio_input;
    the mic recorder is hidden (not an error) on older versions — see
    _HAS_AUDIO_INPUT below. If the mic isn't available, voice mode falls
    back to typed input and keeps reading replies aloud rather than
    breaking the conversation.
  - Output: read-aloud is done entirely in the browser via the Web Speech
    API (window.speechSynthesis), injected as a tiny HTML/JS snippet after
    a NEW assistant answer is rendered — never for replayed history, or
    every rerun would re-speak the whole conversation. Opt-in via a
    checkbox (or always-on under voice mode) so it never talks unless the
    person asked it to, one way or another. The dropdown's tts_lang sets
    the utterance's voice locale.

File upload (patients only): a file uploader lets a patient hand the
chatbot a PDF or photo. Two different things can come through it:
  - A health report (lab result, discharge summary, etc.) — PDFs, and
    images explicitly marked as a report, go through ai/report_reader.py
    (OCR/pypdf text extraction + a summarization prompt).
  - A medicine photo (box/strip/label, OR a bare pill with no printed
    text at all) — images marked as a medicine photo go through
    ai/medicine_reader.py instead, which reuses report_reader's own OCR
    path for anything with a legible label, and falls back to a vision
    LLM (ai/llm.py's get_vision_llm()) to describe a bare pill's shape/
    color/imprint when there's no text to read at all.
  Both wrap their result in a summarization prompt that goes through the
  exact same ask() pipeline as a typed question — display_text on
  _handle_turn() keeps the visible chat bubble to a short "Uploaded
  report: X" / "Uploaded medicine photo: X" line, and the verbatim
  extracted text is appended (as a fenced code block) after the model's
  ANSWER instead — see _handle_turn()'s append_raw_text — rather than
  asking the model to reproduce it itself, so it's exact and never
  paraphrased/truncated by the LLM, and never read aloud by voice mode.
  The file itself is never saved anywhere; only its extracted text (and
  the resulting summary) end up in chat history, same as any other
  message. Since which of the two an image is can't be
  auto-detected reliably, an image upload asks the patient to pick which
  one it is and confirm with a button before anything is sent to OCR/the
  LLM — a PDF is unambiguously a report, so it skips that extra click and
  processes immediately, same as before.

Immersive ("Jarvis") mode: opens by default whenever any role reaches
Smart Care AI — a dark, full-viewport HUD takeover (see _jarvis_css())
that hides the sidebar and Streamlit's own chrome so the chat fills the
browser tab, with a glowing cyan accent theme distinct from the rest of
the app's teal/clay palette, on purpose — this screen is meant to feel
like waking up an assistant, not like another portal tab. It's CSS-only:
Streamlit's rerun model can't reliably trigger the browser's real
Fullscreen API (that needs a direct synchronous click gesture, and a
rerun is several round-trips removed from the original click), so this
achieves the full-screen FEEL — no sidebar, no distractions — without
depending on OS/browser chrome actually disappearing. _jarvis_bar() draws
a small "← Exit immersive mode" pill so the sidebar (and normal
navigation) is always one click away, never truly trapped.
"""
import hashlib
import json
import re

import streamlit as st
import streamlit.components.v1 as components

from ai import booking_flow, medicine_flow, medicine_reader, report_flow, report_reader, speech
from ai.languages import LANGUAGES, DEFAULT_LANGUAGE, get_language
from ai.smartcare_agent import ask, assistant_status
from services import chat_service
from services.auth_service import user_to_dict, get_doctor_id_for_user
from views.components import ecg_divider

_HAS_AUDIO_INPUT = hasattr(st, "audio_input")

_SUGGESTIONS = {
    "patient": [
        "Do I have any upcoming appointments?",
        "What medicines am I prescribed?",
        "How are my vitals trending?",
        "Book an appointment",
    ],
    "doctor": [
        "What's my schedule today?",
        "Show my patient's health records",
        "What prescriptions have I written?",
        "Check medicine stock",
        "Create a report for a patient",
    ],
    "admin": [
        "Give me a clinic overview",
        "Show the medicine inventory",
        "List all pending appointments",
        "Which medicines are low on stock?",
        "Add a new medicine to the catalog",
    ],
}


def _first_name(full_name: str) -> str:
    """Best-effort first name for the voice greeting — skips a leading
    'Dr.' title, since this app's seeded doctor accounts already store it
    as part of full_name (e.g. "Dr. Arjun Mehta"), so the greeting says
    "Hey Arjun" rather than the ungainly "Hey Dr."."""
    parts = (full_name or "").strip().split()
    if parts and parts[0].rstrip(".").lower() == "dr":
        parts = parts[1:]
    return parts[0] if parts else "there"


def render(user):
    """`user` is the detached ORM User from st.session_state — convert it
    once to a plain dict so the agent never depends on ORM internals."""
    user_dict = user_to_dict(user)
    ckey = f"chat_history_{user_dict['id']}"

    immersive_key = f"chat_immersive_{user_dict['id']}"
    if immersive_key not in st.session_state:
        st.session_state[immersive_key] = True  # opens in immersive mode by default
    immersive = st.session_state[immersive_key]

    if immersive:
        st.markdown(_jarvis_css(), unsafe_allow_html=True)
    _jarvis_bar(user_dict, immersive)

    language, voice_mode, voice_just_activated, mic_enabled = _header(user_dict)

    # The orb is the assistant's visual "presence" in immersive mode — a
    # single persistent element (via st.empty()) that _handle_turn()
    # swaps between idle and active/glowing state around its ask() call,
    # rather than a new element per message. None in non-immersive mode,
    # since the glow only makes sense against the dark HUD background.
    orb_slot = st.empty()
    if immersive:
        orb_slot.markdown(_render_orb(active=False), unsafe_allow_html=True)
    else:
        orb_slot = None

    # First visit this session: pull persisted history from chat_messages.
    if ckey not in st.session_state:
        st.session_state[ckey] = chat_service.load_history(user_dict["id"])

    # Voice mode's opening line: fires exactly once per activation (see
    # _header()'s voice_just_activated), not on every rerun while voice
    # mode stays on. A real greeting, not a UI hint — it's appended to
    # chat history and persisted like any other assistant message, and
    # spoken aloud immediately, before the person has said anything.
    if voice_just_activated:
        greeting = f"Hey {_first_name(user_dict.get('full_name'))}, how can I assist you today?"
        st.session_state[ckey].append({"role": "assistant", "content": greeting})
        chat_service.save_message(user_dict["id"], "assistant", greeting)
        _speak(greeting, language)

    _suggestion_row(user_dict, ckey)

    # Replay the conversation so far. Never speaks here — read-aloud only
    # fires for a freshly generated answer inside _handle_turn() (or the
    # greeting above), never during this replay loop, or every rerun
    # would re-speak everything.
    for msg in st.session_state[ckey]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # The booking wizard renders after the history, whenever it's active —
    # same pattern for the admin's medicine wizard and the doctor's report
    # wizard: the chatbot only ever opens these, a human always clicks the
    # actual Save/Confirm button.
    if user_dict["role"] == "patient" and booking_flow.is_active():
        booking_flow.render(user_dict["id"])
    elif user_dict["role"] == "admin" and medicine_flow.is_active():
        medicine_flow.render()
    elif user_dict["role"] == "doctor" and report_flow.is_active():
        doctor_id = get_doctor_id_for_user(user_dict["id"])
        if doctor_id:
            report_flow.render(doctor_id)

    # Voice mode: greet, listen, answer — the mic is the primary input
    # (speaking is the point), with typing available as a fallback right
    # below it, not the other way around. Same _mic_input/_handle_turn
    # pipeline as normal chat either way; voice mode only changes which
    # control comes first and that replies always get read aloud. Both
    # mic call sites are gated on mic_enabled (the header's separate
    # 🎙️ Mic toggle) — when it's off, the recorder never renders at
    # all, in or out of voice mode; typing is always available regardless.
    prompt = None
    if voice_mode:
        if mic_enabled:
            prompt = _mic_input(user_dict, language, label="🎙️ Tap and speak your question", quiet=True)
        if not prompt:
            prompt = st.chat_input("Or type instead…")
    else:
        prompt = st.chat_input("Message Smart Care AI…")

    pending = st.session_state.pop("chat_suggested_prompt", None)
    if pending and not prompt:
        prompt = pending
    if not prompt and not voice_mode and mic_enabled:
        prompt = _mic_input(user_dict, language)

    if prompt:
        _handle_turn(user_dict, ckey, prompt, language, voice_mode=voice_mode, orb_slot=orb_slot)

    _file_upload(user_dict, ckey, language, voice_mode, orb_slot=orb_slot)


def _jarvis_css() -> str:
    """A dark, glowing HUD theme scoped to this page only — see the module
    docstring for why it's CSS-only rather than a real browser fullscreen.
    Injected as one <style> block via st.markdown(); since it's only
    called while immersive mode is on AND the person is on this screen,
    navigating away un-mounts this element and every rule in it along
    with it — same page-scoping trick views/auth_view.py already uses
    for its own login-only overrides. Cyan/electric-blue is a deliberate
    departure from the app's teal/clay palette everywhere else: this
    screen is meant to feel like waking up an assistant, not another
    portal tab."""
    return """<style>
    .stApp{
        background:
          radial-gradient(circle at 18% 12%, rgba(0,229,255,0.10) 0%, transparent 42%),
          radial-gradient(circle at 85% 88%, rgba(0,180,255,0.08) 0%, transparent 46%),
          linear-gradient(165deg,#05080C 0%,#070C12 55%,#04070A 100%) !important;
    }
    [data-testid="stSidebar"]{ display:none !important; }
    header[data-testid="stHeader"]{ background:transparent !important; }
    #MainMenu, footer{ visibility:hidden !important; }
    .block-container{ padding-top:1.2rem !important; max-width:1000px !important; }

    .stApp, .stApp p, .stApp li, .stApp span, .stApp label,
    .stMarkdown, .stCaption, [data-testid="stCaptionContainer"]{
        color:#CFEFFF !important;
    }
    /* Markdown tables (e.g. the assistant listing medicine stock, doctor
       directories, appointments) aren't covered by the rule above —
       Streamlit's default table CSS is styled for a LIGHT page background
       and was rendering near-invisible on this dark theme: readable
       header (it carries its own light background) over unreadable dim
       body text. Restyled explicitly rather than just inheriting color. */
    .stMarkdown table{
        width:100%;
        border-collapse:collapse;
        margin:0.7rem 0;
    }
    .stMarkdown table th{
        background:rgba(0,229,255,0.14) !important;
        color:#8FF6FF !important;
        border:none !important;
        border-bottom:2px solid rgba(0,229,255,0.35) !important;
        padding:8px 12px !important;
        text-align:left;
        font-weight:700;
    }
    .stMarkdown table td{
        background:rgba(10,20,30,0.6) !important;
        color:#EAFBFF !important;
        border:none !important;
        border-bottom:1px solid rgba(0,229,255,0.14) !important;
        padding:8px 12px !important;
    }
    .stMarkdown table tr:nth-child(even) td{
        background:rgba(0,229,255,0.06) !important;
    }
    h1, h2, h3, h4{
        font-family:'Fraunces',serif !important;
        color:#8FF6FF !important;
        text-shadow:0 0 18px rgba(0,229,255,0.35);
    }
    [data-testid="stWidgetLabel"] p{
        color:#7FD8EA !important;
        font-family:'JetBrains Mono',monospace !important;
        font-size:0.75rem !important;
        letter-spacing:0.05em;
        text-transform:uppercase;
    }

    .chat-greeting-title{ color:#8FF6FF !important; text-shadow:0 0 14px rgba(0,229,255,0.3); }
    .chat-greeting-sub{ color:#7FBCD1 !important; }

    .chat-status-pill{
        background:rgba(0,229,255,0.08) !important;
        border:1px solid rgba(0,229,255,0.35) !important;
        color:#8FF6FF !important;
        font-family:'JetBrains Mono',monospace !important;
        letter-spacing:0.04em;
    }
    .chat-status-pill.is-offline{
        background:rgba(255,92,92,0.1) !important;
        border-color:rgba(255,92,92,0.4) !important;
        color:#FF9B9B !important;
    }
    .chat-status-dot{
        background:#00E5FF !important;
        box-shadow:0 0 8px #00E5FF;
        animation:jarvisPulse 1.7s ease-in-out infinite !important;
    }
    .chat-status-pill.is-offline .chat-status-dot{
        background:#FF5C5C !important; box-shadow:0 0 8px #FF5C5C; animation:none !important;
    }
    @keyframes jarvisPulse{
        0%,100%{ box-shadow:0 0 0 0 rgba(0,229,255,0.55); }
        50%{ box-shadow:0 0 0 7px rgba(0,229,255,0); }
    }

    .ecg-divider svg path{ stroke:#00E5FF !important; opacity:0.6; }

    [data-testid="stChatMessage"]{
        background:rgba(10,20,30,0.72) !important;
        border:1px solid rgba(0,229,255,0.22) !important;
        border-radius:14px !important;
        box-shadow:0 0 26px -10px rgba(0,229,255,0.25);
    }
    [data-testid="stChatInput"]{
        background:rgba(8,16,24,0.85) !important;
        border:1px solid rgba(0,229,255,0.35) !important;
        border-radius:14px !important;
        box-shadow:0 0 22px -8px rgba(0,229,255,0.3);
    }
    [data-testid="stChatInput"] textarea{ color:#E8FBFF !important; }
    [data-testid="stAudioInput"]{
        background:rgba(8,16,24,0.75) !important;
        border:1px solid rgba(0,229,255,0.35) !important;
        border-radius:14px !important;
    }

    [data-baseweb="select"] > div,
    .stTextInput input, .stTextArea textarea, .stNumberInput input,
    [data-testid="stFileUploaderDropzone"]{
        background:rgba(8,16,24,0.7) !important;
        border-color:rgba(0,229,255,0.3) !important;
        color:#E8FBFF !important;
    }

    .stButton>button{
        background:rgba(10,20,30,0.6) !important;
        border:1px solid rgba(0,229,255,0.35) !important;
        color:#CFEFFF !important;
    }
    .stButton>button:hover{
        border-color:#00E5FF !important;
        box-shadow:0 0 16px rgba(0,229,255,0.45) !important;
        color:#FFFFFF !important;
    }
    .stButton>button[kind="primary"]{
        background:linear-gradient(135deg, rgba(0,229,255,0.28), rgba(0,140,255,0.16)) !important;
        border-color:#00E5FF !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"]{
        background:rgba(10,20,30,0.72) !important;
        border-color:rgba(0,229,255,0.25) !important;
    }

    /* ---- The glowing presence orb (see _render_orb()) ------------------ */
    .jarvis-orb-wrap{
        width:150px; height:150px;
        margin:0.4rem auto 1rem auto;
        display:flex; align-items:center; justify-content:center;
        position:relative;
    }
    .jarvis-orb-svg{ width:100%; height:100%; overflow:visible; }
    .jarvis-orb-ring-path{
        transform-origin:100px 100px;
        animation:jarvisOrbSpin 7s linear infinite;
        transition:stroke-width 0.3s ease;
    }
    .jarvis-orb-wrap::before{
        content:"";
        position:absolute; width:68%; height:68%; border-radius:50%;
        background:radial-gradient(circle, rgba(138,92,255,0.35) 0%, rgba(91,140,255,0.15) 55%, transparent 75%);
        filter:blur(9px);
        animation:jarvisOrbGlow 3.4s ease-in-out infinite;
    }
    @keyframes jarvisOrbSpin{ from{ transform:rotate(0deg); } to{ transform:rotate(360deg); } }
    @keyframes jarvisOrbGlow{
        0%,100%{ opacity:0.55; transform:scale(1); }
        50%{ opacity:0.9; transform:scale(1.08); }
    }
    .jarvis-orb-wrap.is-active .jarvis-orb-ring-path{
        animation-duration:1.3s;
        stroke-width:9.5;
    }
    .jarvis-orb-wrap.is-active::before{
        animation-duration:0.85s;
        background:radial-gradient(circle, rgba(138,92,255,0.6) 0%, rgba(91,140,255,0.3) 55%, transparent 75%);
    }
    </style>"""


def _jarvis_bar(user_dict: dict, immersive: bool):
    """A single small pill, top-left: exits (or re-enters) immersive mode.
    This is the escape hatch — with the sidebar hidden, this is the only
    way back to normal navigation, so it must always render regardless
    of anything else on the page."""
    key = f"chat_immersive_{user_dict['id']}"
    label = "← Exit immersive mode" if immersive else "🖥️ Enter immersive mode"
    if st.button(label, key="chat_jarvis_toggle"):
        st.session_state[key] = not immersive
        st.rerun()


def _render_orb(active: bool = False) -> str:
    """The assistant's visual presence in immersive mode — a glowing
    violet-to-blue energy ring (SVG, not a plain CSS shape, so the stroke
    can carry a real gradient plus a soft Gaussian-blur glow instead of a
    flat box-shadow approximation). idle: slow ambient rotation and a
    gentle breathing glow. active (passed True by _handle_turn(), right
    before its blocking ask() call, and back to False right after):
    faster spin, brighter pulse, thicker stroke — so it visibly reacts
    the instant a query is submitted, not just while a spinner's text
    changes. The gap in the ring (stroke-dasharray) is deliberate, not a
    rendering bug — it's what makes a spinning ring read as an energy
    trail rather than a static hoop."""
    state_class = "is-active" if active else ""
    return f"""<div class="jarvis-orb-wrap {state_class}">
        <svg viewBox="0 0 200 200" class="jarvis-orb-svg" xmlns="http://www.w3.org/2000/svg">
            <defs>
                <linearGradient id="orbGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stop-color="#8A5CFF"/>
                    <stop offset="50%" stop-color="#5B8CFF"/>
                    <stop offset="100%" stop-color="#E8E4FF"/>
                </linearGradient>
                <filter id="orbGlow" x="-60%" y="-60%" width="220%" height="220%">
                    <feGaussianBlur stdDeviation="4.5" result="blur"/>
                    <feMerge>
                        <feMergeNode in="blur"/>
                        <feMergeNode in="SourceGraphic"/>
                    </feMerge>
                </filter>
            </defs>
            <circle cx="100" cy="100" r="72" fill="none" stroke="url(#orbGrad)"
                    stroke-width="7" stroke-linecap="round" stroke-dasharray="410 40"
                    filter="url(#orbGlow)" class="jarvis-orb-ring-path"/>
        </svg>
    </div>"""


def _header(user_dict: dict) -> tuple[str, bool, bool, bool]:
    """Renders the header row (status, clear chat, language, mic, voice
    mode, read-aloud controls) and returns (language, voice_mode,
    voice_just_activated, mic_enabled) — the caller threads these into
    ask(), the mic transcriber, and TTS. voice_just_activated is True
    only on the exact rerun where the toggle flips off->on (detected by
    comparing against its value at the START of this call, before the
    widget updates it) — render() uses that single-shot signal to greet
    the person by name once per activation, not on every rerun while
    voice mode stays on. Voice mode: the assistant greets you, you speak
    your question (mic, primary), it answers and reads the answer back —
    typing still works as a fallback alongside the mic.

    Mic is a SEPARATE on/off switch from Voice mode: Voice mode controls
    the whole spoken-conversation experience (greeting, mic as primary
    input, auto-read-aloud); Mic controls only whether the microphone
    recorder renders at all, anywhere on this page — including the
    always-available "Or tap to speak" fallback under the normal text
    box outside Voice mode. Turning Mic off is for anyone who'd rather
    the browser never even offers to ask for microphone permission —
    typing still works everywhere regardless of this switch."""
    left, right = st.columns([3, 1])
    with left:
        st.markdown("### 🫀 Smart Care AI")
        status = assistant_status()
        offline = "offline" in status
        st.markdown(
            f"""<span class="chat-status-pill{' is-offline' if offline else ''}">
            <span class="chat-status-dot"></span>{status}</span>""",
            unsafe_allow_html=True,
        )
    with right:
        if st.button("🗑 Clear chat", key="chat_clear_btn"):
            chat_service.clear_history(user_dict["id"])
            st.session_state[f"chat_history_{user_dict['id']}"] = []
            st.rerun()

    lang_key = f"chat_lang_{user_dict['id']}"
    if lang_key not in st.session_state:
        st.session_state[lang_key] = DEFAULT_LANGUAGE
    lang_options = list(LANGUAGES.keys())

    mic_key = f"chat_mic_enabled_{user_dict['id']}"
    if mic_key not in st.session_state:
        st.session_state[mic_key] = True  # on by default — matches existing behavior

    voice_key = f"chat_voice_mode_{user_dict['id']}"
    if voice_key not in st.session_state:
        st.session_state[voice_key] = False
    was_voice_on = st.session_state[voice_key]

    col_lang, col_mic, col_voice, col_tts = st.columns([2, 1.4, 2, 2])
    with col_lang:
        st.session_state[lang_key] = st.selectbox(
            "🌐 Language", lang_options,
            index=lang_options.index(st.session_state[lang_key]),
            key=f"chat_lang_select_{user_dict['id']}",
            help=(
                "Sets the mic transcription and read-aloud voice, and the "
                "fallback language — the assistant always replies in "
                "whatever language you actually type or speak, in any "
                "Indian language, regardless of this setting."
            ),
        )
    with col_mic:
        st.session_state[mic_key] = st.toggle(
            "🎙️ Mic", value=st.session_state[mic_key],
            key=f"chat_mic_toggle_{user_dict['id']}",
            help=(
                "Turn off to hide the microphone button everywhere on this "
                "page — the browser will never be asked for mic permission. "
                "Typing still works either way."
            ),
        )
    with col_voice:
        st.session_state[voice_key] = st.toggle(
            "Voice mode", value=st.session_state[voice_key],
            key=f"chat_voice_toggle_{user_dict['id']}",
            help=(
                "The assistant greets you by name, you speak your question "
                "into the mic, and it answers out loud — a real "
                "conversation. Typing still works alongside it. Needs Mic "
                "turned on to actually listen; otherwise it still greets "
                "and reads replies aloud, just via typed input."
            ),
        )
    voice_just_activated = st.session_state[voice_key] and not was_voice_on

    with col_tts:
        if st.session_state[voice_key]:
            st.caption("🔊 Reading every reply aloud (voice mode)")
        else:
            st.checkbox(
                "🔊 Read replies aloud", key=f"chat_tts_{user_dict['id']}",
                help="Uses your browser's built-in voice — no audio is sent anywhere for this.",
            )

    mic_enabled = st.session_state[mic_key]
    if not mic_enabled and st.session_state[voice_key]:
        st.caption("🎙️ Mic is turned off — type your question below; replies will still be read aloud.")
    elif mic_enabled and st.session_state[voice_key] and not (_HAS_AUDIO_INPUT and speech.is_configured()):
        st.caption(
            "🎙️ Voice mode is on, but the mic isn't available right now "
            "(needs a recent Streamlit and a configured voice service) — "
            "type below instead; replies will still be read aloud."
        )

    ecg_divider()
    return st.session_state[lang_key], st.session_state[voice_key], voice_just_activated, mic_enabled


def _suggestion_row(user_dict: dict, ckey: str):
    """A few one-click starter questions, only while the chat is empty."""
    if st.session_state[ckey]:
        return
    st.markdown(
        """<div style="text-align:center;padding:1.2rem 0 0.4rem 0;">
        <div style="font-size:2.6rem;">🩺</div>
        <div class="chat-greeting-title">How can I help you today?</div>
        <div class="chat-greeting-sub">Ask about anything in your portal — or try one of these:</div>
        </div>""",
        unsafe_allow_html=True,
    )
    suggestions = _SUGGESTIONS.get(user_dict["role"], _SUGGESTIONS["patient"])
    cols = st.columns(len(suggestions))
    for i, (col, text) in enumerate(zip(cols, suggestions)):
        if col.button(text, key=f"chat_sugg_{i}", use_container_width=True):
            st.session_state["chat_suggested_prompt"] = text
            st.rerun()


def _mic_input(user_dict: dict, language: str, label: str = "🎤 Or tap to speak your question", quiet: bool = False) -> str | None:
    """Renders the mic recorder (if this Streamlit version supports it)
    and returns newly-transcribed text exactly once per NEW recording —
    st.audio_input keeps returning the same bytes on every rerun until
    the user records again, so without the hash check below the last
    thing you said would get re-sent as a new message on every rerun.
    `language` is the header dropdown's current selection — passed to
    Whisper as an accuracy hint, not a hard restriction; the person can
    still speak a different language and it'll usually still transcribe.
    `label` lets callers frame this as the primary control (voice mode)
    vs. a secondary fallback (normal chat). `quiet` skips the "needs a
    newer Streamlit" caption — used when voice mode already showed its
    own unavailability note in _header(), so it isn't said twice."""
    if not _HAS_AUDIO_INPUT:
        if not quiet:
            st.caption("🎤 Voice input needs Streamlit ≥ 1.38 — update to enable it.")
        return None

    if not speech.is_configured():
        return None  # same "quietly unavailable" behavior as the offline LLM

    mic = st.audio_input(label, key="chat_mic")
    if mic is None:
        return None

    audio_bytes = mic.getvalue()
    audio_hash = hashlib.md5(audio_bytes).hexdigest()
    hash_key = f"chat_mic_last_hash_{user_dict['id']}"
    if st.session_state.get(hash_key) == audio_hash:
        return None  # already transcribed this exact recording

    st.session_state[hash_key] = audio_hash
    whisper_lang = get_language(language)["whisper_lang"]
    with st.spinner("Transcribing…"):
        try:
            text = speech.transcribe(audio_bytes, filename="mic.wav", language=whisper_lang)
        except RuntimeError as e:
            st.error(str(e))
            return None

    return text or None


def _file_upload(user_dict: dict, ckey: str, language: str, voice_mode: bool = False, orb_slot=None):
    """Lets a patient upload a health report OR a medicine photo and get
    an AI summary/identification of it, through the exact same agent turn
    as a typed question. PDFs are unambiguously a report and process
    immediately, same as before (see ai/report_reader.py). Images are
    ambiguous — a photo could be a lab report page or a pill/box — so the
    patient picks which one it is and hits a confirm button before
    anything is sent to OCR/the vision model; this mirrors the rest of
    the app's "a human confirms before the AI-driven step" pattern
    (booking_flow, medicine_flow, report_flow) rather than guessing.
    Doctors/admins don't get this control; they read reports through the
    portal itself, and a patient's own upload shouldn't be actionable by
    another role's tools anyway."""
    if user_dict["role"] != "patient":
        return

    uploaded = st.file_uploader(
        "📄 Or upload a health report or medicine photo for a summary (PDF, PNG, JPG)",
        type=["pdf", "png", "jpg", "jpeg"], key="chat_report_upload",
    )
    if uploaded is None:
        return

    file_bytes = uploaded.getvalue()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    hash_key = f"chat_report_last_hash_{user_dict['id']}"

    is_image = uploaded.name.lower().endswith((".png", ".jpg", ".jpeg"))
    upload_kind = "Health report"

    if is_image:
        # Ambiguous file type — ask which one this is, and require an
        # explicit click before processing, so a stale/default radio
        # value can never silently trigger the wrong extraction path.
        upload_kind = st.radio(
            "What is this a photo of?",
            ["Health report", "Medicine (pill, strip, box, or label)"],
            horizontal=True, key=f"chat_upload_kind_{file_hash}",
        )
        if st.session_state.get(hash_key) == file_hash:
            return  # this exact file was already processed
        if not st.button("Summarize this upload", key=f"chat_upload_confirm_{file_hash}"):
            return
    else:
        if st.session_state.get(hash_key) == file_hash:
            return  # already processed this exact PDF

    st.session_state[hash_key] = file_hash

    if is_image and upload_kind.startswith("Medicine"):
        with st.spinner("Identifying the medicine…"):
            try:
                extracted = medicine_reader.identify(file_bytes, uploaded.name)
            except medicine_reader.MedicineReadError as e:
                st.error(str(e))
                return
        display_text = f"💊 Uploaded medicine photo: **{uploaded.name}**"
        agent_prompt = medicine_reader.build_summary_prompt(uploaded.name, extracted)
        spinner_text = "Identifying the medicine…"
        raw_extracted = extracted
    else:
        with st.spinner("Reading your report…"):
            try:
                text = report_reader.extract_text(file_bytes, uploaded.name)
            except report_reader.ReportReadError as e:
                st.error(str(e))
                return
        display_text = f"📄 Uploaded report: **{uploaded.name}**"
        agent_prompt = report_reader.build_summary_prompt(uploaded.name, text)
        spinner_text = "Reading your report…"
        raw_extracted = text

    _handle_turn(
        user_dict, ckey, agent_prompt, language, display_text=display_text,
        voice_mode=voice_mode, orb_slot=orb_slot, spinner_text=spinner_text,
        append_raw_text=raw_extracted,
    )


def _speech_text(markdown_text: str) -> str:
    """Strips Markdown syntax before handing text to the browser's TTS —
    without this, the voice reads out literal asterisks, hashes, and
    bullet dashes ("asterisk asterisk heart rate asterisk asterisk")
    instead of just the words. Only affects what's SPOKEN; the on-screen
    st.markdown() rendering of the answer is untouched."""
    text = markdown_text

    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)      # code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)                     # inline code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)         # [label](url) -> label
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)   # headers
    text = re.sub(r"^\s*[\*\-\+]\s+", "", text, flags=re.MULTILINE)   # bullet markers
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)      # numbered list markers
    text = re.sub(r"^\s*-{3,}\s*$", "", text, flags=re.MULTILINE)     # horizontal rules
    text = re.sub(r"\*\*\*(.*?)\*\*\*", r"\1", text)             # ***bold italic***
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)                 # **bold**
    text = re.sub(r"(?<!\w)\*(?!\s)(.*?)(?<!\s)\*(?!\w)", r"\1", text)  # *italic*
    text = re.sub(r"__(.*?)__", r"\1", text)                     # __bold__
    text = re.sub(r"(?<!\w)_(?!\s)(.*?)(?<!\s)_(?!\w)", r"\1", text)   # _italic_
    text = re.sub(r"[*_`#]", "", text)                           # any leftover stray symbols
    text = re.sub(r"\n{2,}", ". ", text)                         # paragraph breaks -> pause
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


def _speak(text: str, language: str):
    """Reads `text` aloud using the browser's Web Speech API. Runs
    entirely client-side — nothing is uploaded anywhere for this.
    `language` sets u.lang to the matching BCP-47 locale (see
    ai/languages.py's tts_lang) so the browser picks a matching voice
    where the OS/browser has one installed, instead of defaulting to
    whatever locale the browser itself is set to."""
    tts_lang = get_language(language)["tts_lang"]
    payload = json.dumps(_speech_text(text))
    lang_payload = json.dumps(tts_lang)
    components.html(
        f"""<script>
        try {{
            const u = new SpeechSynthesisUtterance({payload});
            u.lang = {lang_payload};
            u.rate = 1.0;
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(u);
        }} catch (e) {{ /* speechSynthesis unsupported — fail silently */ }}
        </script>""",
        height=0,
    )


def _handle_turn(
    user_dict: dict, ckey: str, prompt: str, language: str, display_text: str = None,
    voice_mode: bool = False, orb_slot=None, spinner_text: str = None,
    append_raw_text: str = None,
):
    """One full agent turn: show it, run the agent, show + persist the
    answer, optionally read it aloud, and act on any signals a tool sent
    back to the UI. `prompt` is always what's sent to ask(); `display_text`
    — used by _file_upload() — is what's actually shown in the chat
    bubble and saved to history when the two need to differ, e.g. showing
    "Uploaded report: X" instead of the full extracted report text.
    `spinner_text` likewise overrides the default "Thinking…" spinner
    label for upload-driven turns (report reading vs. medicine
    identification say different things while working).
    `append_raw_text` — used by _file_upload() — is the verbatim
    OCR/vision extraction, appended as a fenced code block AFTER the
    model's answer, in what's DISPLAYED and SAVED to history only. It is
    deliberately appended here in code, not asked of the model in the
    prompt: having the LLM reproduce OCR output itself risks it
    paraphrasing, summarizing, or truncating what should be an exact,
    verbatim record of what was actually extracted from the file. For
    the same reason it's excluded from what _speak() reads aloud — a
    voice reading raw OCR noise/garbled text aloud would be poor UX and
    isn't the point of the spoken answer.
    `voice_mode` always speaks the reply, regardless of the (hidden, in
    that case) read-aloud checkbox — see _header(). `orb_slot`, when given
    (immersive mode only — see render()), is the st.empty() placeholder
    holding the presence orb: switched to its glowing "active" state
    right before the blocking ask() call and back to idle right after, so
    the orb visibly reacts the instant a query is submitted."""
    shown = display_text if display_text is not None else prompt
    st.session_state[ckey].append({"role": "user", "content": shown})
    with st.chat_message("user"):
        st.markdown(shown)

    # History sent to the model excludes the message we just appended
    # (ask() adds it itself) and is capped so the prompt stays small.
    history = st.session_state[ckey][:-1][-chat_service.HISTORY_CONTEXT_LIMIT:]

    if orb_slot is not None:
        orb_slot.markdown(_render_orb(active=True), unsafe_allow_html=True)

    default_spinner = spinner_text or ("Reading your report…" if display_text is not None else "Thinking…")
    with st.chat_message("assistant"):
        with st.spinner(default_spinner):
            answer, signals = ask(user_dict, prompt, history, language=language)
        full_answer = answer
        if append_raw_text:
            full_answer = (
                f"{answer}\n\n---\n**📄 Extracted text**\n```\n{append_raw_text}\n```"
            )
        st.markdown(full_answer)
        if voice_mode or st.session_state.get(f"chat_tts_{user_dict['id']}"):
            _speak(answer, language)  # speak the answer only, never the raw text block

    if orb_slot is not None:
        orb_slot.markdown(_render_orb(active=False), unsafe_allow_html=True)

    st.session_state[ckey].append({"role": "assistant", "content": full_answer})
    chat_service.save_message(user_dict["id"], "user", shown)
    chat_service.save_message(user_dict["id"], "assistant", full_answer)

    # signals is how a tool talks back to the UI: start_booking opens the
    # patient's booking wizard, open_medicine_form opens the admin's
    # medicine wizard pre-filled from the propose_*_medicine tools, and
    # open_report_form opens the doctor's report wizard pre-filled from
    # the propose_*_report tools. None of these write anything themselves
    # — each wizard still needs a human click to actually save.
    if signals.get("start_booking") and user_dict["role"] == "patient":
        booking_flow.start()
        st.rerun()

    medicine_form = signals.get("open_medicine_form")
    if medicine_form and user_dict["role"] == "admin":
        mode = medicine_form.get("mode")
        data = medicine_form.get("data", {})
        if mode == "add":
            medicine_flow.open_add(data)
        elif mode == "edit":
            medicine_flow.open_edit(data)
        elif mode == "delete":
            medicine_flow.open_delete(data)
        st.rerun()

    report_form = signals.get("open_report_form")
    if report_form and user_dict["role"] == "doctor":
        mode = report_form.get("mode")
        data = report_form.get("data", {})
        if mode == "create":
            report_flow.open_create(data)
        elif mode == "edit":
            report_flow.open_edit(data)
        elif mode == "delete":
            report_flow.open_delete(data)
        st.rerun()