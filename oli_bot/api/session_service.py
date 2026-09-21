"""Session persistence helpers shared by the WebSocket and session routes."""

from typing import Any, Dict, List

from ..agent import sanitize_tool_history
from ..models import Message
from ..sessions import ConversationStore, _message_from_dict
from .constants import SESSION_SERVER


def _session_meta(session: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a stored session dict to the metadata browsers need after a
    ``session_created`` notification."""
    return {
        "id": session.get("id", ""),
        "name": session.get("name", ""),
        "created_at": session.get("created_at", ""),
        "updated_at": session.get("updated_at", ""),
        "server": session.get("server", SESSION_SERVER),
        "model": session.get("model", ""),
        "profile": session.get("profile", ""),
        "total_tokens": session.get("total_tokens", 0) or 0,
        "total_tokens_estimated": session.get("total_tokens_estimated", False),
    }


def _session_created_frame(store: ConversationStore, session_id: str) -> Dict[str, Any]:
    """Build a ``session_created`` frame carrying the stored session's meta."""
    data = store.load_session(SESSION_SERVER, session_id) or {}
    return {"type": "session_created", "data": {"session": _session_meta(data)}}


def _persist_session(
    store: ConversationStore,
    agent: Any,
    session_id: str,
    messages: List[Message],
    total_tokens: int,
    tokens_estimated: bool,
) -> str:
    """Save a WebSocket conversation turn to the shared store.

    Returns the (possibly new) session id — the store recreates a session
    under a fresh UUID when the file is missing or corrupt.
    """
    return store.save_session(
        server=SESSION_SERVER,
        session_id=session_id,
        messages=messages,
        model=str(agent.backend.model or ""),
        profile=agent.profile_name or "",
        total_tokens=total_tokens,
        tokens_estimated=tokens_estimated,
    )


def _load_conversation(
    store: ConversationStore, agent: Any, session_id: str
) -> tuple[str, List[Message]]:
    """Load a session's persisted messages from disk.

    If the file is missing or corrupt a fresh session is created and its new
    id is returned so the caller can tell the client via ``session_created``.
    """
    data = store.load_session(SESSION_SERVER, session_id)
    if data is None:
        agent_backend = getattr(agent, "backend", None)
        new_id = store.create_session(
            server=SESSION_SERVER,
            model=str(getattr(agent_backend, "model", "") or ""),
            profile=agent.profile_name or "",
            system_prompt=agent.system_prompt or "",
        )
        return new_id, []
    messages = sanitize_tool_history(
        [_message_from_dict(m) for m in data.get("messages", [])]
    )
    return session_id, messages