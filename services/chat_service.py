"""
services/chat_service.py
Persists the chatbot conversation to the existing chat_messages table so
history survives logout / reopening the app. Only plain user/assistant
text is stored — never tool traces or internal prompts.
"""
from typing import List

from core.database import get_session
from models.models import ChatMessage

# Keep the prompt small: only the most recent N messages are replayed to
# the LLM as context. The full history is still stored and shown in the UI.
HISTORY_CONTEXT_LIMIT = 12


def save_message(user_id: int, role: str, content: str) -> None:
    """role is 'user' or 'assistant' — matches the chat_messages.role column."""
    if role not in ("user", "assistant") or not content:
        return
    with get_session() as db:
        db.add(ChatMessage(user_id=user_id, role=role, content=content))


def load_history(user_id: int, limit: int = 200) -> List[dict]:
    """Oldest-first list of {'role', 'content'} dicts, the exact shape both
    st.chat_message rendering and smartcare_agent.ask(history=...) expect.

    The dicts are built INSIDE the session: get_session() commits on exit,
    which expires the ORM objects, so reading r.role/r.content after the
    `with` block would raise DetachedInstanceError."""
    with get_session() as db:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        history = [{"role": r.role, "content": r.content} for r in rows]
    return list(reversed(history))


def clear_history(user_id: int) -> int:
    """Deletes this user's chat history. Returns the number of rows removed."""
    with get_session() as db:
        n = (
            db.query(ChatMessage)
            .filter(ChatMessage.user_id == user_id)
            .delete(synchronize_session=False)
        )
        return n
