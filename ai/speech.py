"""
ai/speech.py
Speech-to-text for the chatbot's mic input, using Groq's hosted Whisper
model — reuses the same GROQ_API_KEY already configured for the chat LLM
in ai/llm.py, so voice input needs no second API key or provider.

Text-to-speech (the bot reading its replies aloud) is deliberately NOT
here — it's handled entirely client-side in views/chatbot_view.py via the
browser's built-in Web Speech API. That keeps replies instant (no audio
round-trip to a server) and free, at the cost of voice quality being
whatever the user's browser/OS provides — an acceptable trade for a
hospital portal chatbot, not a narration product.
"""
from core.config import settings


def is_configured() -> bool:
    return bool(settings.GROQ_API_KEY)


def transcribe(audio_bytes: bytes, filename: str = "audio.wav", language: str = "") -> str:
    """
    Sends recorded mic audio to Groq's Whisper endpoint and returns the
    transcribed text (stripped). `language` is an optional ISO-639-1 hint
    (e.g. "hi", "ta" — see ai/languages.py's whisper_lang field, set from
    views/chatbot_view.py's language dropdown); Whisper can auto-detect
    without it, but a hint noticeably improves accuracy for non-English
    Indian languages, so callers should pass it whenever the person has a
    language selected. Raises RuntimeError — never lets a raw SDK/network
    exception escape — so callers can show a friendly error instead of
    crashing the page, matching how ai/llm.py's callers handle the
    "offline" case.
    """
    if not is_configured():
        raise RuntimeError(
            "Voice input needs GROQ_API_KEY set in .env — the same key used for the chat assistant."
        )

    from groq import Groq

    client = Groq(api_key=settings.GROQ_API_KEY)
    kwargs = dict(file=(filename, audio_bytes), model="whisper-large-v3-turbo", response_format="text")
    if language:
        kwargs["language"] = language
    try:
        result = client.audio.transcriptions.create(**kwargs)
    except Exception as e:
        raise RuntimeError(f"Couldn't transcribe that — please try again. ({e})") from e

    # Different groq-python SDK versions return either a plain str or an
    # object with a .text attribute for response_format="text" — handle both.
    text = result if isinstance(result, str) else getattr(result, "text", "")
    return text.strip()