"""Stateful WebSocket chat endpoint ``/v1/chat``."""

import json
import logging
from typing import Any, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..convert import _event_to_frame
from ..harness import _api_confirm
from ...models import Done, Message, UsageEvent
from ..runner import _resolve_tools
from ..session_service import (
    _load_conversation,
    _persist_session,
    _session_created_frame,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


@router.websocket("/v1/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """Stateful WebSocket chat endpoint.

    The client may send ``{"content": ...}`` for a turn or
    ``{"action": "clear"}`` to reset the connection history.  When a
    ``session_id`` is included, the conversation is persisted to the shared
    ``ConversationStore`` (under ``SESSION_SERVER``) after every turn,
    mirroring the TUI's per-turn save.  Missing or corrupt session files are
    recreated and the client is notified via a ``session_created`` frame.
    ``{"action": "clear", "session_id": ...}`` wipes the persisted session too.
    Runs are serialized on ``app.state.lock`` like the REST endpoints.
    """
    await websocket.accept()
    agent = websocket.app.state.agent
    store = getattr(websocket.app.state, "session_store", None)
    lock: Any = websocket.app.state.lock
    messages: List[Message] = []
    connection_session_id = ""
    try:
        await websocket.send_json({"type": "connected", "data": {}})
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Invalid JSON payload"}}
                )
                continue
            if not isinstance(data, dict):
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Expected a JSON object"}}
                )
                continue

            if data.get("action") == "clear":
                messages = []
                requested = data.get("session_id")
                if requested:
                    connection_session_id = str(requested)
                    if store is not None:
                        try:
                            new_id = _persist_session(
                                store, agent, connection_session_id, [], 0, False
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to clear persisted session: %s", e
                            )
                            new_id = connection_session_id
                        if new_id != connection_session_id:
                            connection_session_id = new_id
                            await websocket.send_json(
                                _session_created_frame(store, new_id)
                            )
                await websocket.send_json({"type": "cleared", "data": {}})
                continue

            content = data.get("content")
            if not content or not str(content).strip():
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Empty message"}}
                )
                continue

            requested_session = data.get("session_id")
            if requested_session and store is not None:
                try:
                    connection_session_id, messages = _load_conversation(
                        store, agent, str(requested_session)
                    )
                    if connection_session_id != str(requested_session):
                        await websocket.send_json(
                            _session_created_frame(store, connection_session_id)
                        )
                except Exception as e:
                    logger.exception("Failed to load session %s", requested_session)
                    await websocket.send_json(
                        {"type": "error", "data": {"message": str(e)}}
                    )
                    continue

            messages.append(Message(role="user", content=str(content)))

            async with lock:
                try:
                    tools = await _resolve_tools(agent)
                except Exception as e:
                    logger.warning("Failed to list tools: %s", e)
                    tools = None
                total_tokens = 0
                tokens_estimated = False
                try:
                    async for event in agent.process(
                        messages, tools=tools, confirm_callback=_api_confirm
                    ):
                        await websocket.send_json(_event_to_frame(event))
                        while agent.mcp_manager.pending_todos:
                            todo_data = agent.mcp_manager.pending_todos.pop(0)
                            await websocket.send_json(
                                {"type": "todo", "data": todo_data}
                            )
                        if isinstance(event, UsageEvent):
                            total_tokens += event.usage.total_tokens
                            tokens_estimated = tokens_estimated or event.usage.estimated
                        if isinstance(event, Done):
                            if event.full_text:
                                messages.append(
                                    Message(
                                        role="assistant", content=event.full_text
                                    )
                                )
                            if requested_session and store is not None:
                                try:
                                    new_id = _persist_session(
                                        store,
                                        agent,
                                        connection_session_id,
                                        messages,
                                        total_tokens,
                                        tokens_estimated,
                                    )
                                    if new_id != connection_session_id:
                                        connection_session_id = new_id
                                        await websocket.send_json(
                                            _session_created_frame(store, new_id)
                                        )
                                except Exception as e:
                                    logger.warning(
                                        "Failed to persist session %s: %s",
                                        connection_session_id,
                                        e,
                                    )
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.exception("Agent process failed over websocket: %s", e)
                    await websocket.send_json(
                        {"type": "error", "data": {"message": str(e)}}
                    )
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected from /v1/chat")