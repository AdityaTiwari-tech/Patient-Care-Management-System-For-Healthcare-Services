"""
ai/llm.py
Builds the cached ChatGroq client used by the tool-calling agent in
ai/smartcare_agent.py. This file owns exactly one thing: turning
.env config into a usable LangChain chat model. It does not know
about tools, roles, or the database — that's smartcare_agent.py.
"""
from functools import lru_cache

from core.config import settings


def is_configured() -> bool:
    return bool(settings.GROQ_API_KEY)


@lru_cache(maxsize=1)
def get_llm():
    """
    Returns a cached ChatGroq instance, or raises if GROQ_API_KEY isn't
    set / langchain-groq isn't installed. Callers (smartcare_agent.ask)
    are expected to check is_configured() first and handle the "offline"
    case themselves — this function stays simple and just builds the
    client.
    """
    if not is_configured():
        raise RuntimeError("GROQ_API_KEY is not set — add it to your .env file.")

    from langchain_groq import ChatGroq

    return ChatGroq(
        model=settings.LLM_MODEL,
        api_key=settings.GROQ_API_KEY,
        temperature=0.3,
        # Generous cap so answers are never cut off mid-thought. gpt-oss
        # models also spend part of this budget on internal reasoning, so
        # the visible answer needs headroom beyond its own length.
        max_tokens=2048,
    )


@lru_cache(maxsize=1)
def get_vision_llm():
    """
    Separate cached ChatGroq instance for multimodal (image) requests.
    get_llm()'s model (settings.LLM_MODEL, e.g. openai/gpt-oss-120b) is
    text-only — it can't accept an image_url content block. This uses
    settings.VISION_LLM_MODEL, a Groq model with image-input support
    instead, so the two stay independently swappable as Groq's model
    lineup changes. Currently only called from ai/medicine_reader.py
    (bare-pill photo identification, where there's no printed text for
    OCR to read).

    Kept as a separate client rather than swapping get_llm()'s model
    per-call, since a vision model is unnecessary — and often slower or
    pricier — for every plain-text chat turn.
    """
    if not is_configured():
        raise RuntimeError("GROQ_API_KEY is not set — add it to your .env file.")

    from langchain_groq import ChatGroq

    return ChatGroq(
        model=settings.VISION_LLM_MODEL,
        api_key=settings.GROQ_API_KEY,
        temperature=0.2,
        max_tokens=1024,
    )