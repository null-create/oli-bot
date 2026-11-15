"""Stateful WebSocket chat endpoint ``/v1/chat``."""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..attachments import attachment_note, save_attachments
from ..convert import _event_to_frame
from ..harness import (
    _api_confirm,
    reset_active_confirm,
    set_active_confirm,
)
from ..permissions import PermissionBroker
from ...activity import attach_turn_activity
from ...models import AssistantResponse, Done, Message, StreamChunk, UsageEvent
from ..runner import _resolve_tools
from ..session_service import (
    _load_conversation,
    _persist_session,
    _session_created_frame,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


async def _run_turn(
    agent: Any,
    lock: Any,
    store: Any,
    messages: List[Message],
    requested_session: Any,
    connection_session_id: str,
    user_index: int,
    confirm_callback: Callable[[str], Any],
    send: Callable[[dict], Awaitable[None]],
) -> str:
    """Execute one agent turn under the process-wide agent lock.

    The caller appends the user message at ``user_index`` before starting
    this coroutine. On cancellation the partial turn is rolled back so an
    interrupted message behaves exactly like the TUI's Ctrl+I rollback.
    """
    async with lock:
        token = set_active_confirm(confirm_callback)
        try:
            try:
                tools = await _resolve_tools(agent)
            except Exception as e:
                logger.warning("Failed to list tools: %s", e)
                tools = None
            total_tokens = 0
            tokens_estimated = False
            root_todos: List[dict] = []
            partial_text = ""
            turn_start = user_index + 1
            try:
                async for event in agent.process(
                    messages, tools=tools, confirm_callback=confirm_callback
                ):
                    await send(_event_to_frame(event))
                    while agent.mcp_manager.pending_todos:
                        todo_data = agent.mcp_manager.pending_todos.pop(0)
                        if "task_id" not in todo_data:
                            root_todos = list(todo_data.get("todos") or [])
                        await send({"type": "todo", "data": todo_data})
                    if isinstance(event, UsageEvent):
                        total_tokens += event.usage.total_tokens
                        tokens_estimated = (
                            tokens_estimated or event.usage.estimated
                        )
                    elif isinstance(event, StreamChunk):
                        partial_text += event.text
                    elif isinstance(event, AssistantResponse):
                        # A new round started; only the latest round's text is
                        # meaningful if the turn is interrupted mid-stream.
                        partial_text = ""
                    elif isinstance(event, Done):
                        if event.full_text:
                            messages.append(
                                Message(role="assistant", content=event.full_text)
                            )
                        attach_turn_activity(messages, turn_start, event.activity)
                        if requested_session and store is not None:
                            try:
                                new_id = _persist_session(
                                    store,
                                    agent,
                                    connection_session_id,
                                    messages,
                                    total_tokens,
                                    tokens_estimated,
                                    root_todos,
                                )
                                if new_id != connection_session_id:
                                    connection_session_id = new_id
                                    await send(
                                        _session_created_frame(store, new_id)
                                    )
                            except Exception as e:
                                logger.warning(
                                    "Failed to persist session %s: %s",
                                    connection_session_id,
                                    e,
                                )
            except asyncio.CancelledError:
                # Roll the interrupted turn back out of the in-memory history
                # (the session file already holds the pre-turn state, so no
                # persistence is needed). Then tell the client the turn ended
                # so it can release its "generating" state.
                if len(messages) > user_index:
                    del messages[user_index:]
                await send(
                    {
                        "type": "interrupted",
                        "data": {"full_text": partial_text},
                    }
                )
                await send(
                    {
                        "type": "done",
                        "data": {"full_text": partial_text, "interrupted": True},
                    }
                )
                raise
            except Exception as e:
                logger.exception("Agent process failed over websocket: %s", e)
                await send({"type": "error", "data": {"message": str(e)}})
        finally:
            reset_active_confirm(token)
    return connection_session_id


@router.websocket("/v1/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """Stateful WebSocket chat endpoint.

    The client may send ``{"content": ...}`` for a turn,
    ``{"action": "clear"}`` to reset the connection history, or
    ``{"action": "interrupt"}`` to cancel the in-flight turn. Permission
    prompts are delivered as ``permission_request`` frames; the client
    answers with ``{"action": "permission_response", "id": ..., "decision":
    "once"|"session"|"deny"}``. ``api_server.permission_mode = "auto"``
    restores the legacy auto-approve behaviour.

    When a ``session_id`` is included, the conversation is persisted to the
    shared ``ConversationStore`` (under ``SESSION_SERVER``) after every turn,
    mirroring the TUI's per-turn save. Missing or corrupt session files are
    recreated and the client is notified via a ``session_created`` frame.
    Runs are serialized on ``app.state.lock`` like the REST endpoints.
    """
    await websocket.accept()
    agent = websocket.app.state.agent
    store = getattr(websocket.app.state, "session_store", None)
    lock: Any = websocket.app.state.lock
    session_locks: dict = getattr(websocket.app.state, "session_locks", None)
    if session_locks is None:
        # Older test harnesses may build ``app.state`` without this attr.
        session_locks = {}
        websocket.app.state.session_locks = session_locks

    def _session_lock(sid: str) -> asyncio.Lock:
        existing = session_locks.get(sid)
        if existing is None:
            existing = asyncio.Lock()
            session_locks[sid] = existing
        return existing

    config = getattr(websocket.app.state, "config", None) or getattr(
        agent, "config", None
    )
    permission_mode = str(
        getattr(config, "api_permission_mode", "prompt") or "prompt"
    ).lower()
    permission_timeout = float(
        getattr(config, "api_permission_timeout", 120.0) or 120.0
    )

    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        # A send racing a disconnect must not abort the agent turn; the
        # receive loop observes the disconnect and cancels the turn.
        async with send_lock:
            try:
                await websocket.send_json(frame)
            except (WebSocketDisconnect, RuntimeError) as exc:
                logger.debug("WebSocket send failed: %s", exc)

    broker = PermissionBroker(send, timeout=permission_timeout)
    confirm_callback = (
        _api_confirm if permission_mode == "auto" else broker.request
    )

    messages: List[Message] = []
    connection_session_id = ""
    turn_task: Optional[asyncio.Task] = None

    def _turn_active() -> bool:
        return turn_task is not None and not turn_task.done()

    async def _handle_turn(data: dict) -> None:
        nonlocal connection_session_id, messages
        requested_session = data.get("session_id")
        # Acquire a per-session lock so concurrent WebSocket connections on
        # the same session_id serialize their load->run->save turns
        # (last-write-wins race would otherwise clobber history). Fall back
        # to a no-op lock when no session is requested.
        sess_lock = (
            _session_lock(str(requested_session))
            if requested_session
            else asyncio.Lock()
        )
        async with sess_lock:
            if requested_session and store is not None:
                try:
                    connection_session_id, messages = _load_conversation(
                        store, agent, str(requested_session)
                    )
                    if connection_session_id != str(requested_session):
                        await send(
                            _session_created_frame(store, connection_session_id)
                        )
                except Exception as e:
                    logger.exception("Failed to load session %s", requested_session)
                    await send({"type": "error", "data": {"message": str(e)}})
                    return

            content = data.get("content")
            attachments = data.get("attachments") or []
            text = str(content) if content else ""
            user_images = None
            if attachments:
                try:
                    images, saved = save_attachments(
                        config, connection_session_id, attachments
                    )
                except Exception as e:
                    logger.warning("Failed to save attachments: %s", e)
                    await send({"type": "error", "data": {"message": str(e)}})
                    return
                note = attachment_note(saved, images)
                if note:
                    text = f"{text}\n\n{note}" if text.strip() else note
                user_images = images or None
            if not text.strip():
                await send({"type": "error", "data": {"message": "Empty message"}})
                return

            user_index = len(messages)
            messages.append(
                Message(role="user", content=text, images=user_images)
            )
            connection_session_id = await _run_turn(
                agent,
                lock,
                store,
                messages,
                requested_session,
                connection_session_id,
                user_index,
                confirm_callback,
                send,
            )

    async def _turn_wrapper(data: dict) -> None:
        nonlocal turn_task
        try:
            await _handle_turn(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("WebSocket turn failed: %s", e)
            await send({"type": "error", "data": {"message": str(e)}})
        finally:
            if turn_task is asyncio.current_task():
                turn_task = None

    try:
        await send({"type": "connected", "data": {}})
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send(
                    {"type": "error", "data": {"message": "Invalid JSON payload"}}
                )
                continue
            if not isinstance(data, dict):
                await send(
                    {"type": "error", "data": {"message": "Expected a JSON object"}}
                )
                continue

            action = data.get("action")
            if action == "permission_response":
                request_id = data.get("id")
                decision = data.get("decision")
                if not isinstance(request_id, str) or not isinstance(
                    decision, str
                ):
                    await send(
                        {
                            "type": "error",
                            "data": {
                                "message": (
                                    "permission_response requires string 'id' "
                                    "and 'decision'"
                                )
                            },
                        }
                    )
                    continue
                try:
                    resolved = broker.resolve(request_id, decision)
                except ValueError as e:
                    await send({"type": "error", "data": {"message": str(e)}})
                    continue
                if not resolved:
                    await send(
                        {
                            "type": "error",
                            "data": {
                                "message": (
                                    "Unknown or expired permission request id"
                                )
                            },
                        }
                    )
                continue

            if action == "interrupt":
                if not _turn_active():
                    await send(
                        {
                            "type": "error",
                            "data": {"message": "No active turn to interrupt"},
                        }
                    )
                    continue
                broker.cancel_all("deny")
                assert turn_task is not None
                turn_task.cancel()
                await asyncio.gather(turn_task, return_exceptions=True)
                continue

            if action == "clear":
                if _turn_active():
                    await send(
                        {
                            "type": "error",
                            "data": {
                                "message": "Cannot clear while a turn is running"
                            },
                        }
                    )
                    continue
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
                            await send(_session_created_frame(store, new_id))
                await send({"type": "cleared", "data": {}})
                continue

            attachments = data.get("attachments") or []
            if not isinstance(attachments, list):
                await send(
                    {
                        "type": "error",
                        "data": {"message": "attachments must be a list"},
                    }
                )
                continue
            content = data.get("content")
            has_text = bool(content and str(content).strip())
            if not has_text and not attachments:
                await send({"type": "error", "data": {"message": "Empty message"}})
                continue
            if _turn_active():
                await send(
                    {
                        "type": "error",
                        "data": {
                            "message": (
                                "A turn is already running; send "
                                '{"action": "interrupt"} first'
                            )
                        },
                    }
                )
                continue
            turn_task = asyncio.create_task(_turn_wrapper(data))
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected from /v1/chat")
    finally:
        broker.cancel_all("deny")
        if _turn_active():
            assert turn_task is not None
            turn_task.cancel()
            await asyncio.gather(turn_task, return_exceptions=True)
