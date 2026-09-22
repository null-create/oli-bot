"""FastAPI dependencies replacing the old module-global ``app`` lookups."""

import asyncio
from typing import Any

from fastapi import Request, WebSocket

from ..agent import Agent
from ..config import AppConfig
from ..sessions import ConversationStore, WorkspaceManager


def get_agent(request: Request) -> Agent:
    return request.app.state.agent


def get_store(request: Request) -> ConversationStore:
    return request.app.state.session_store


def get_lock(request: Request) -> Any:
    """The process-wide ``asyncio.Lock`` serializing ``Agent.process()`` runs.

    The shared ``Agent`` (and its builtin/todo state and backend connection)
    is not safe for concurrent in-flight requests, so requests are serialized
    in-process. An ``asyncio.Lock`` (not a threading lock) is required: the
    lock is held across ``await`` points, so contention must be scheduled on
    the event loop rather than per-thread.
    """
    return request.app.state.lock


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_workspace_manager(request: Request) -> WorkspaceManager:
    """Return the process-wide ``WorkspaceManager`` (recent workspace list).

    Built in ``init_state``; recreated lazily here so tests that rewire
    ``app.state`` without it keep working.
    """
    manager = getattr(request.app.state, "workspace_manager", None)
    if manager is None:
        manager = WorkspaceManager()
        request.app.state.workspace_manager = manager
    return manager


# --- WebSocket variants ----------------------------------------------------- #
# A WebSocket route can't depend on ``Request``, so mirror the above against
# ``WebSocket``.


def get_ws_agent(websocket: WebSocket) -> Agent:
    return websocket.app.state.agent


def get_ws_lock(websocket: WebSocket) -> Any:
    return websocket.app.state.lock
