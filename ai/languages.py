"""
ai/languages.py
The single list of languages the chatbot can respond in — shared by
views/chatbot_view.py (the dropdown, and the TTS/Whisper locale hints)
and ai/smartcare_agent.py (the instruction telling the LLM which
language to answer in). Defined once here so the two can never drift
apart — e.g. the dropdown offering a language the agent doesn't know
how to instruct itself into.

Each entry:
  - key (the dict key)   -> shown in the dropdown, includes native script
                             for recognizability (e.g. "Hindi (हिन्दी)")
  - llm_name              -> plain English name passed to the LLM's
                             system prompt instruction (e.g. "Hindi")
  - tts_lang               -> BCP-47 locale for the browser's
                             speechSynthesis (views/chatbot_view.py's
                             _speak()), so read-aloud uses a matching
                             voice where the OS/browser has one installed
  - whisper_lang            -> ISO-639-1 code passed as a hint to Groq's
                             Whisper transcription (ai/speech.py), which
                             improves accuracy for mic input in that
                             language over relying on auto-detection alone
"""

LANGUAGES = {
    "English": {"llm_name": "English", "tts_lang": "en-IN", "whisper_lang": "en"},
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
}

DEFAULT_LANGUAGE = "English"


def get_language(label: str) -> dict:
    return LANGUAGES.get(label, LANGUAGES[DEFAULT_LANGUAGE])