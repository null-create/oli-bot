"""Session CRUD routes under ``/v1/sessions``."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ...agent import Agent
from ..constants import SESSION_SERVER
from ..deps import get_agent, get_store
from ..schemas import RenameSessionBody
from ...sessions import ConversationStore

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(
    store: ConversationStore = Depends(get_store),
) -> Dict[str, Any]:
    """List saved sessions for the shared server namespace."""
    return {"sessions": store.list_sessions(SESSION_SERVER)}


@router.post("")
async def create_session(
    store: ConversationStore = Depends(get_store),
    agent: Agent = Depends(get_agent),
) -> Dict[str, Any]:
    """Create a new empty session and return it."""
    session_id = store.create_session(
        server=SESSION_SERVER,
        model=str(agent.backend.model or ""),
        profile=agent.profile_name or "",
        system_prompt=agent.system_prompt or "",
    )
    return store.load_session(SESSION_SERVER, session_id) or {}


@router.get("/{session_id}")
async def get_session(
    session_id: str, store: ConversationStore = Depends(get_store)
) -> Any:
    """Return a full session including its messages, or 404."""
    data = store.load_session(SESSION_SERVER, session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@router.put("/{session_id}")
async def rename_session(
    session_id: str,
    payload: RenameSessionBody,
    store: ConversationStore = Depends(get_store),
) -> Any:
    """Rename a session, returning the updated session or 404."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    if not store.rename_session(SESSION_SERVER, session_id, name):
        raise HTTPException(status_code=404, detail="Session not found")
    return store.load_session(SESSION_SERVER, session_id) or {}


@router.delete("/{session_id}")
async def delete_session(
    session_id: str, store: ConversationStore = Depends(get_store)
) -> Any:
    """Delete a session, returning 404 when it does not exist."""
    if not store.delete_session(SESSION_SERVER, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}
