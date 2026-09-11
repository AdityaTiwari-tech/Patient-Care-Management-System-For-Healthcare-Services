"""
ai/languages.py
The single list of languages the chatbot can respond in — shared by
views/chatbot_view.py (the dropdown, and the TTS/Whisper locale hints)
and ai/smartcare_agent.py (the instruction telling the LLM which
language to answer in). Defined once here so the two can never drift
apart — e.g. the dropdown offering a language the agent doesn't know
how to instruct itself into.

Coverage: all 22 languages of the Constitution's Eighth Schedule, plus
English. The LLM itself was never limited to this list — see
smartcare_agent._language_instruction(), which already tells the model
to mirror whatever language the person actually typed, independent of
this dropdown — so this file mainly controls the dropdown UI and the
voice pipeline (TTS output voice, Whisper input hint), not the model's
own language ceiling.

Each entry:
  - key (the dict key)   -> shown in the dropdown, includes native script
                             for recognizability (e.g. "Hindi (हिन्दी)")
  - llm_name              -> plain English name passed to the LLM's
                             system prompt instruction (e.g. "Hindi")
  - tts_lang               -> BCP-47 locale for the browser's
                             speechSynthesis (views/chatbot_view.py's
                             _speak()), so read-aloud uses a matching
                             voice where the OS/browser has one installed.
                             Coverage varies a lot by OS/browser for the
                             less common languages below — this is a
                             best-effort tag, not a guarantee a voice
                             exists locally.
  - whisper_lang            -> ISO-639-1/639-3 code passed as a hint to
                             Groq's Whisper transcription (ai/speech.py).
                             Whisper large-v3 does NOT cover every
                             language below (it supports ~99 languages
                             total) — where a language isn't in Whisper's
                             list, this is left as "" and ai/speech.py's
                             existing `if language:` check simply skips
                             the hint, falling back to Whisper's own
                             auto-detection instead of failing.
"""

LANGUAGES = {
    "English": {"llm_name": "English", "tts_lang": "en-IN", "whisper_lang": "en"},

    # --- Original 11 -------------------------------------------------
    "Hindi (हिन्दी)": {"llm_name": "Hindi", "tts_lang": "hi-IN", "whisper_lang": "hi"},
    "Tamil (தமிழ்)": {"llm_name": "Tamil", "tts_lang": "ta-IN", "whisper_lang": "ta"},
    "Telugu (తెలుగు)": {"llm_name": "Telugu", "tts_lang": "te-IN", "whisper_lang": "te"},
    "Bengali (বাংলা)": {"llm_name": "Bengali", "tts_lang": "bn-IN", "whisper_lang": "bn"},
    "Marathi (मराठी)": {"llm_name": "Marathi", "tts_lang": "mr-IN", "whisper_lang": "mr"},
    "Gujarati (ગુજરાતી)": {"llm_name": "Gujarati", "tts_lang": "gu-IN", "whisper_lang": "gu"},
    "Kannada (ಕನ್ನಡ)": {"llm_name": "Kannada", "tts_lang": "kn-IN", "whisper_lang": "kn"},
    "Malayalam (മലയാളം)": {"llm_name": "Malayalam", "tts_lang": "ml-IN", "whisper_lang": "ml"},
    "Punjabi (ਪੰਜਾਬੀ)": {"llm_name": "Punjabi", "tts_lang": "pa-IN", "whisper_lang": "pa"},
    "Urdu (اردو)": {"llm_name": "Urdu", "tts_lang": "ur-IN", "whisper_lang": "ur"},

    # --- Newly added: remaining Eighth Schedule languages -------------
    # Whisper-supported (whisper_lang set):
    "Nepali (नेपाली)": {"llm_name": "Nepali", "tts_lang": "ne-NP", "whisper_lang": "ne"},
    "Assamese (অসমীয়া)": {"llm_name": "Assamese", "tts_lang": "as-IN", "whisper_lang": "as"},
    "Sanskrit (संस्कृतम्)": {"llm_name": "Sanskrit", "tts_lang": "sa-IN", "whisper_lang": "sa"},
    "Sindhi (سنڌي)": {"llm_name": "Sindhi", "tts_lang": "sd-IN", "whisper_lang": "sd"},

    # Not in Whisper's language list — voice input auto-detects instead
    # of using a hint; text chat and TTS read-aloud are unaffected.
    "Odia (ଓଡ଼ିଆ)": {"llm_name": "Odia", "tts_lang": "or-IN", "whisper_lang": ""},
    "Konkani (कोंकणी)": {"llm_name": "Konkani", "tts_lang": "kok-IN", "whisper_lang": ""},
    "Maithili (मैथिली)": {"llm_name": "Maithili", "tts_lang": "mai-IN", "whisper_lang": ""},
    "Dogri (डोगरी)": {"llm_name": "Dogri", "tts_lang": "doi-IN", "whisper_lang": ""},
    "Kashmiri (کٲشُر)": {"llm_name": "Kashmiri", "tts_lang": "ks-IN", "whisper_lang": ""},
    "Manipuri (মৈতৈলোন্)": {"llm_name": "Manipuri (Meitei)", "tts_lang": "mni-IN", "whisper_lang": ""},
    "Bodo (बड़ो)": {"llm_name": "Bodo", "tts_lang": "brx-IN", "whisper_lang": ""},
    "Santali (ᱥᱟᱱᱛᱟᱲᐤ)": {"llm_name": "Santali", "tts_lang": "sat-IN", "whisper_lang": ""},
}

DEFAULT_LANGUAGE = "English"

# Languages whose Whisper hint is unavailable (whisper_lang == "") — used
# by views/chatbot_view.py to show a small "voice input will auto-detect
# for this language" caption instead of implying the hint is active.
WHISPER_UNSUPPORTED = [label for label, cfg in LANGUAGES.items() if not cfg["whisper_lang"]]


def get_language(label: str) -> dict:
    return LANGUAGES.get(label, LANGUAGES[DEFAULT_LANGUAGE])