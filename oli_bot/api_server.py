"""OpenAI-compatible REST API over the oli agent harness.

Serves ``/v1/models`` and ``/v1/chat/completions`` (streaming + non-streaming)
with FastAPI, plus a stateful ``/v1/chat`` WebSocket that relays every agent
event as a typed JSON frame for real-time browser UIs.  All routes plug the
same ``Agent`` tool loop that powers the TUI into any client that speaks the
OpenAI wire protocol (the ``openai`` Python SDK, curl, or any other HTTP
client).

The server is stateless from the caller's perspective: each
``/v1/chat/completions`` request carries the full message history, mirroring
real OpenAI semantics. Behind the scenes a single process-private ``Agent``
instance (with its backend, tool registrations, and MCP wiring) is shared
across requests so connections and profiles are not rebuilt per call.

Because there is no human to prompt at permission time in API mode, the
confirm-callback auto-allows every permission scope for the current request
(the API server's equivalent of the TUI's "Allow for session").  Offline and
dry-run gating from ``AppConfig`` still apply unchanged.  In-process requests
are serialized with a lock since the shared ``Agent`` is not concurrent-safe.
"""

import base64
import dataclasses
import json
import logging
import threading
import time
import uuid
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from art import text2art
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from .agent import (
    Agent,
    AgentEvent,
    AgentPool,
    register_dispatch_tool,
    run_agent_dispatch,
    sanitize_tool_history,
)
from .backends import create_model_backend, ModelBackend
from .config import AppConfig, configs
from .logger import setup_logging
from .mcp_client import MCPClientManager
from .models import (
    AssistantResponse,
    Done,
    Error,
    ImageAttachment,
    MCPServerConfig,
    Message,
    StreamChunk,
    SubAgentCompleted,
    SubAgentEvent,
    SubAgentProgress,
    SubAgentRun,
    SubAgentStarted,
    ThinkingChunk,
    ToolCallExecuting,
    ToolCallResult,
    UsageEvent,
    ChatCompletionMessage,
    ChatCompletionRequest,
)
from .sessions import (
    SCOPE_WORKSPACE_SENSITIVE,
    ConversationStore,
    Session,
    WorkspaceManager,
    _message_from_dict,
    is_sensitive_path,
)
from .settings import SettingsManager
from .screens.taglines import TAGLINES
from .tools.manager import BuiltinToolManager

logger = logging.getLogger(__name__)

API_HOST = configs.api_host
API_PORT = configs.api_port
API_PROFILE = configs.api_profile
API_MODE = configs.api_mode

# Sessions are namespaced per "server". The browser shares the TUI's store by
# using the same default namespace the TUI falls back to when no server is set.
_SESSION_SERVER = "default"


class _Lock:
    """Process-wide lock serializing concurrent ``Agent.process()`` runs.

    The shared ``Agent`` (and its builtin/todo state and backend connection)
    is not safe for concurrent in-flight requests, so requests are serialized
    in-process.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self) -> "_Lock":
        self._lock.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._lock.release()


class AgentError(Exception):
    """Raised when the agent run fails; formatted as an OpenAI error body."""

    def __init__(self, message: str, code: str = "agent_error") -> None:
        super().__init__(message)
        self.response = JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": message,
                    "type": "server_error",
                    "code": code,
                }
            },
        )


# --- FastAPI app ------------------------------------------------------------- #

app = FastAPI(title="oli", version="1.0.0")


@app.exception_handler(AgentError)
async def _agent_error_handler(request, exc: AgentError):
    return exc.response


# --- Agent harness construction -------------------------------------------- #


def _select_model(config: AppConfig, backend: ModelBackend) -> None:
    """Fill in the active model if the factory defaulted to empty."""
    if backend.model and str(backend.model):
        return
    if config.backend == "openai":
        backend.model = config.openai_model
    elif config.backend == "huggingface":
        backend.model = config.huggingface_model
    elif config.backend == "transformers":
        backend.model = config.transformers_model
    else:
        backend.model = config.ollama_model
    backend.model = str(backend.model) or None


def _build_agent(config: AppConfig, mode: str, profile: str) -> Agent:
    """Construct the shared Agent harness exactly like ``chat.py`` does,
    minus the Textual TUI."""
    url = config.ollama_base_url
    backend = create_model_backend(url, config.backend, None)
    _select_model(config, backend)

    cwd = Path.cwd()
    session = Session(workspace=None if is_sensitive_path(cwd) else cwd)
    builtin_tools = BuiltinToolManager(
        session=session,
        backend=backend,
        config=config,
    )
    mcp_manager = MCPClientManager(
        config=config,
        builtin_tools=builtin_tools,
        offline_mode=config.offline_mode,
        session=session,
    )
    agent = Agent(
        role="root",
        backend=backend,
        mcp_manager=mcp_manager,
        profile_name=profile,
        config=config,
    )
    agent.set_mode(mode)
    agent._session = session
    return agent


async def _api_confirm(description: str) -> str:
    """Auto-allow every permission scope (API mode has no interactive prompt)."""
    return "session"


def _wire_todo_relay(agent: Agent) -> None:
    """Relay ``builtin__todowrite`` updates to WebSocket clients.

    The tool manager invokes these synchronously from inside the tool handler;
    we append snapshots to ``mcp_manager.pending_todos`` which the socket loop
    drains after each agent event. ``task_id`` is present for sub-agent runs so
    the client can demux the update.
    """

    def _on_todos_changed(todos: list) -> None:
        agent.mcp_manager.pending_todos.append({"todos": list(todos)})

    def _on_sub_todos_changed(run: SubAgentRun, todos: list) -> None:
        agent.mcp_manager.pending_todos.append(
            {
                "todos": list(todos),
                "task_id": run.task_id,
                "agent_name": run.agent_name,
            }
        )

    agent.mcp_manager._builtin_tools.set_todo_callback(_on_todos_changed)
    agent.mcp_manager._builtin_tools.set_sub_todo_callback(_on_sub_todos_changed)


async def _dispatch_tasks(tasks: list[dict]) -> str:
    """API-server ``dispatch`` tool handler.

    Fans a batch of tasks out to pooled sub-agents concurrently, mirroring the
    TUI's implementation. Sub-agent lifecycle events are pushed onto
    ``mcp_manager.sub_agent_queue`` (set by the agent's tool loop while the
    dispatch call is in flight) so the WebSocket relay streams them live.
    """
    pool = getattr(app.state, "agent_pool", None)
    if pool is None or not tasks:
        return "Error: dispatch called with no tasks"

    available_tools = await app.state.agent.mcp_manager.get_available_tools()
    sub_tools = [t for t in available_tools if t.get("name") != "builtin__dispatch"]

    now = datetime.now(timezone.utc).isoformat()
    runs: List[SubAgentRun] = []
    for i, spec in enumerate(tasks):
        runs.append(
            SubAgentRun(
                task_id=f"run-{i + 1}",
                agent_name=str(spec.get("agent", "")),
                pool_name=str(spec.get("pool", "default")),
                task=str(spec.get("task", "")),
                started_at=now,
            )
        )

    return await run_agent_dispatch(
        pool=pool,
        runs=runs,
        tools=sub_tools,
        confirm_callback=_api_confirm,
        event_sink=app.state.agent.mcp_manager.sub_agent_queue,
    )


# --- Message conversion ----------------------------------------------------- #


def _media_type_from_data_uri(data_uri: str) -> str:
    header = data_uri.split(",", 1)[0]
    if ";" in header:
        header = header.split(";", 1)[0]
    if ":" in header:
        header = header.split(":", 1)[1]
    return header or "application/octet-stream"


def _decode_data_uri(data_uri: str) -> bytes:
    return base64.b64decode(data_uri.split(",", 1)[1])


def _to_message(msg: ChatCompletionMessage) -> Message:
    """Convert an OpenAI chat message into an internal ``Message``.

    ``content`` may be a plain string or a list of parts (for multimodal);
    ``image_url`` parts with ``data:`` URIs become ``ImageAttachment``
    instances carried through the tool loop for vision-capable backends.
    """
    role = msg.role
    content = msg.content
    images: List[ImageAttachment] = []

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                text_parts.append(part.get("text", ""))
            elif ptype == "image_url":
                url = part.get("image_url")
                if isinstance(url, dict):
                    url = url.get("url", "")
                if isinstance(url, str) and url.startswith("data:"):
                    images.append(
                        ImageAttachment(
                            data=_decode_data_uri(url),
                            media_type=_media_type_from_data_uri(url),
                        )
                    )
                elif isinstance(url, str):
                    text_parts.append(f"[image: {url}]")
        text = "\n".join(text_parts)
    else:
        text = str(content)

    return Message(
        role=role,
        content=text,
        images=images or None,
        name=msg.name,
    )


# --- Agent execution -------------------------------------------------------- #


async def _resolve_tools(agent: Agent) -> Optional[List[Dict[str, Any]]]:
    mode = agent.mode
    if mode == "agent":
        return await agent.mcp_manager.get_available_tools()
    if mode == "ask":
        return await agent.mcp_manager.get_readonly_tools()
    if mode == "plan":
        return await agent.mcp_manager.get_plan_tools()
    return None


async def _collect_response(request: ChatCompletionRequest) -> str:
    """Run the agent tool loop for a request and return the final assistant
    text. Raises ``AgentError`` (formatted as an OpenAI error body) when the
    run fails or produces no text."""
    config = app.state.config
    with app.state.lock:
        messages = [_to_message(m) for m in request.messages]
        try:
            tools = await _resolve_tools(app.state.agent)
        except Exception as e:
            logger.warning("Failed to list tools: %s", e)
            tools = None

        full_text = ""
        error_text: Optional[str] = None
        try:
            async for event in app.state.agent.process(
                messages, tools=tools, confirm_callback=_api_confirm
            ):
                if isinstance(event, StreamChunk):
                    full_text += event.text
                elif isinstance(event, AssistantResponse):
                    full_text += event.content
                elif isinstance(event, Error):
                    error_text = event.message
                elif isinstance(event, Done):
                    if event.full_text:
                        full_text = event.full_text
        except Exception as e:
            logger.exception("Agent process failed: %s", e)
            error_text = str(e)

    if error_text:
        raise AgentError(error_text)
    if not full_text:
        logger.warning("Agent produced empty response for request")
        raise AgentError("The agent produced no response.", code="empty_response")
    return full_text


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _usage(completion_text: str) -> Dict[str, int]:
    # Rustic token estimate; real prompt usage depends on the backend. Kept
    # approximate since the agent loop does not surface exact counts.
    completion = max(1, (len(completion_text) + 3) // 4)
    return {
        "prompt_tokens": 0,
        "completion_tokens": completion,
        "total_tokens": completion,
    }


async def _stream_response(request: ChatCompletionRequest) -> AsyncIterator[str]:
    completion_id = _completion_id()
    model = str(app.state.agent.backend.model or "")
    created = int(time.time())

    def chunk(delta: Dict[str, Any], finish_reason: Any = None) -> str:
        return (
            "data: "
            + json.dumps(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": finish_reason}
                    ],
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    yield chunk({"role": "assistant", "content": ""})

    error_text: Optional[str] = None
    with app.state.lock:
        messages = [_to_message(m) for m in request.messages]
        try:
            tools = await _resolve_tools(app.state.agent)
        except Exception as e:
            logger.warning("Failed to list tools: %s", e)
            tools = None
        try:
            async for event in app.state.agent.process(
                messages, tools=tools, confirm_callback=_api_confirm
            ):
                if isinstance(event, StreamChunk):
                    yield chunk({"content": event.text})
                elif isinstance(event, AssistantResponse):
                    yield chunk({"content": event.content})
                elif isinstance(event, Error):
                    error_text = event.message
        except Exception as e:
            logger.exception("Agent process failed during stream: %s", e)
            error_text = str(e)

    if error_text:
        error = {"message": error_text, "type": "server_error", "code": "agent_error"}
        yield "data: " + json.dumps({"error": error}) + "\n\n"
    else:
        yield chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


# --- Sessions ------------------------------------------------------------- #


def _session_meta(session: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a stored session dict to the metadata browsers need after a
    ``session_created`` notification."""
    return {
        "id": session.get("id", ""),
        "name": session.get("name", ""),
        "created_at": session.get("created_at", ""),
        "updated_at": session.get("updated_at", ""),
        "server": session.get("server", _SESSION_SERVER),
        "model": session.get("model", ""),
        "profile": session.get("profile", ""),
        "total_tokens": session.get("total_tokens", 0) or 0,
        "total_tokens_estimated": session.get("total_tokens_estimated", False),
    }


def _session_created_frame(session_id: str) -> Dict[str, Any]:
    """Build a ``session_created`` frame carrying the stored session's meta."""
    data = app.state.session_store.load_session(_SESSION_SERVER, session_id) or {}
    return {"type": "session_created", "data": {"session": _session_meta(data)}}


def _persist_session(
    session_id: str,
    messages: List[Message],
    total_tokens: int,
    tokens_estimated: bool,
) -> str:
    """Save a WebSocket conversation turn to the shared store.

    Returns the (possibly new) session id — the store recreates a session
    under a fresh UUID when the file is missing or corrupt.
    """
    store: ConversationStore = app.state.session_store
    agent = app.state.agent
    return store.save_session(
        server=_SESSION_SERVER,
        session_id=session_id,
        messages=messages,
        model=str(agent.backend.model or ""),
        profile=agent.profile_name or "",
        total_tokens=total_tokens,
        tokens_estimated=tokens_estimated,
    )


def _load_conversation(session_id: str) -> tuple[str, List[Message]]:
    """Load a session's persisted messages from disk.

    If the file is missing or corrupt a fresh session is created and its new
    id is returned so the caller can tell the client via ``session_created``.
    """
    store: ConversationStore = app.state.session_store
    data = store.load_session(_SESSION_SERVER, session_id)
    if data is None:
        agent = app.state.agent
        new_id = store.create_session(
            server=_SESSION_SERVER,
            model=str(agent.backend.model or ""),
            profile=agent.profile_name or "",
            system_prompt=agent.system_prompt or "",
        )
        return new_id, []
    messages = sanitize_tool_history(
        [_message_from_dict(m) for m in data.get("messages", [])]
    )
    return session_id, messages


# --- WebSocket ------------------------------------------------------------- #


def _event_to_frame(event: AgentEvent) -> Dict[str, Any]:
    """Convert an ``AgentEvent`` into a typed JSON envelope for the browser.

    The ``type`` field lets the client distinguish event kinds and render them
    differently (streamed text, thinking blocks, tool calls, errors, etc.).
    """
    if isinstance(event, SubAgentEvent):
        # Wrapped sub-agent activity: forward the inner frame with the owning
        # run's identity attached so the client can demux by task_id.
        frame = _event_to_frame(event.event)
        frame["data"]["task_id"] = event.task_id
        frame["data"]["agent_name"] = event.agent_name
        return frame
    if isinstance(event, StreamChunk):
        return {"type": "text_chunk", "data": {"text": event.text}}
    if isinstance(event, ThinkingChunk):
        return {"type": "thinking", "data": {"text": event.text}}
    if isinstance(event, ToolCallExecuting):
        return {
            "type": "tool_call_executing",
            "data": {"name": event.name, "parameters": event.parameters},
        }
    if isinstance(event, ToolCallResult):
        return {
            "type": "tool_call_result",
            "data": {"name": event.name, "result": event.result},
        }
    if isinstance(event, AssistantResponse):
        return {"type": "assistant_response", "data": {"content": event.content}}
    if isinstance(event, UsageEvent):
        return {"type": "usage", "data": dataclasses.asdict(event.usage)}
    if isinstance(event, Error):
        return {"type": "error", "data": {"message": event.message}}
    if isinstance(event, Done):
        return {"type": "done", "data": {"full_text": event.full_text}}
    if isinstance(event, SubAgentStarted):
        return {
            "type": "sub_agent_started",
            "data": {
                "task_id": event.task_id,
                "agent_name": event.agent_name,
                "pool_name": event.pool_name,
                "task": event.task,
            },
        }
    if isinstance(event, SubAgentProgress):
        return {
            "type": "sub_agent_progress",
            "data": {
                "task_id": event.task_id,
                "agent_name": event.agent_name,
                "activity": event.activity,
                "status": event.status,
            },
        }
    if isinstance(event, SubAgentCompleted):
        return {
            "type": "sub_agent_completed",
            "data": {
                "task_id": event.task_id,
                "agent_name": event.agent_name,
                "status": event.status,
                "full_text": event.full_text,
            },
        }
    logger.warning("Unknown agent event in websocket relay: %r", event)
    return {"type": "unknown", "data": {"event": repr(event)}}


@app.websocket("/v1/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """Stateful WebSocket chat endpoint.

    The client may send ``{"content": ...}`` for a turn or
    ``{"action": "clear"}`` to reset the connection history.  When a
    ``session_id`` is included, the conversation is persisted to the shared
    ``ConversationStore`` (under ``_SESSION_SERVER``) after every turn,
    mirroring the TUI's per-turn save.  Missing or corrupt session files are
    recreated and the client is notified via a ``session_created`` frame.
    ``{"action": "clear", "session_id": ...}`` wipes the persisted session too.
    Runs are serialized on ``app.state.lock`` like the REST endpoints.
    """
    await websocket.accept()
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
                    try:
                        new_id = _persist_session(connection_session_id, [], 0, False)
                    except Exception as e:
                        logger.warning("Failed to clear persisted session: %s", e)
                        new_id = connection_session_id
                    if new_id != connection_session_id:
                        connection_session_id = new_id
                        await websocket.send_json(_session_created_frame(new_id))
                await websocket.send_json({"type": "cleared", "data": {}})
                continue

            content = data.get("content")
            if not content or not str(content).strip():
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Empty message"}}
                )
                continue

            requested_session = data.get("session_id")
            if requested_session:
                try:
                    connection_session_id, messages = _load_conversation(
                        str(requested_session)
                    )
                    if connection_session_id != str(requested_session):
                        await websocket.send_json(
                            _session_created_frame(connection_session_id)
                        )
                except Exception as e:
                    logger.exception("Failed to load session %s", requested_session)
                    await websocket.send_json(
                        {"type": "error", "data": {"message": str(e)}}
                    )
                    continue

            messages.append(Message(role="user", content=str(content)))

            try:
                tools = await _resolve_tools(app.state.agent)
            except Exception as e:
                logger.warning("Failed to list tools: %s", e)
                tools = None

            with app.state.lock:
                total_tokens = 0
                tokens_estimated = False
                try:
                    async for event in app.state.agent.process(
                        messages, tools=tools, confirm_callback=_api_confirm
                    ):
                        await websocket.send_json(_event_to_frame(event))
                        while app.state.agent.mcp_manager.pending_todos:
                            todo_data = app.state.agent.mcp_manager.pending_todos.pop(0)
                            await websocket.send_json(
                                {"type": "todo", "data": todo_data}
                            )
                        if isinstance(event, UsageEvent):
                            total_tokens += event.usage.total_tokens
                            tokens_estimated = tokens_estimated or event.usage.estimated
                        if isinstance(event, Done):
                            if event.full_text:
                                messages.append(
                                    Message(role="assistant", content=event.full_text)
                                )
                            if requested_session:
                                try:
                                    new_id = _persist_session(
                                        connection_session_id,
                                        messages,
                                        total_tokens,
                                        tokens_estimated,
                                    )
                                    if new_id != connection_session_id:
                                        connection_session_id = new_id
                                        await websocket.send_json(
                                            _session_created_frame(new_id)
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


# --- Config API ------------------------------------------------------------- #


# Mapping between the browser UI's flat ``OliConfig`` keys and the nested
# settings.json format the ``SettingsManager`` persists.
_FLAT_TO_NESTED = {
    "backend": ("", "backend"),
    "openai_api_key": ("openai", "api_key"),
    "openai_base_url": ("openai", "base_url"),
    "openai_model": ("openai", "large_model"),
    "openai_small_model": ("openai", "small_model"),
    "openai_vision_style": ("openai", "vision_style"),
    "openai_optional_headers": ("openai", "optional_headers"),
    "ollama_base_url": ("ollama", "base_url"),
    "ollama_model": ("ollama", "large_model"),
    "ollama_small_model": ("ollama", "small_model"),
    "huggingface_base_url": ("huggingface", "base_url"),
    "huggingface_api_key": ("huggingface", "api_key"),
    "huggingface_model": ("huggingface", "large_model"),
    "huggingface_small_model": ("huggingface", "small_model"),
    "huggingface_remote": ("huggingface", "remote"),
    "transformers_model": ("transformers", "model"),
    "transformers_small_model": ("transformers", "small_model"),
    "transformers_device": ("transformers", "device"),
    "transformers_dtype": ("transformers", "dtype"),
    "transformers_is_multi_model": ("transformers", "is_multi_model"),
    "voice_whisper_model": ("voice", "whisper_model"),
    "voice_piper_model": ("voice", "piper_model"),
    "voice_sample_rate": ("voice", "sample_rate"),
    "voice_frame_duration_ms": ("voice", "frame_duration_ms"),
    "voice_vad_aggressiveness": ("voice", "vad_aggressiveness"),
    "voice_silence_timeout_ms": ("voice", "silence_timeout_ms"),
    "voice_max_record_seconds": ("voice", "max_record_seconds"),
    "max_tokens": ("model_params", "max_tokens"),
    "temperature": ("model_params", "temperature"),
    "max_retries": ("model_params", "max_retries"),
    "retry_delay": ("model_params", "retry_delay"),
    "request_timeout": ("model_params", "request_timeout"),
    "max_messages": ("model_params", "max_messages"),
    "max_tool_iterations": ("model_params", "max_tool_iterations"),
    "stream_timeout": ("model_params", "stream_timeout"),
    "model_filters": ("model_params", "model_filters"),
    "truncation_max_chars_small": ("model_params", "truncation_max_chars_small"),
    "truncation_max_chars_large": ("model_params", "truncation_max_chars_large"),
    "dry_run": ("model_params", "dry_run"),
    "offline_mode": ("model_params", "offline_mode"),
    "use_agent_pool": ("model_params", "use_agent_pool"),
    "agent_pool_size": ("model_params", "agent_pool_size"),
    "agents_yaml": ("model_params", "agents_yaml"),
    "log_level": ("logging", "log_level"),
    "log_file": ("logging", "log_file"),
    "profiles_dir": ("paths", "profiles_dir"),
    "logs_dir": ("paths", "logs_dir"),
    "api_host": ("api_server", "host"),
    "api_port": ("api_server", "port"),
    "api_profile": ("api_server", "profile"),
    "api_mode": ("api_server", "mode"),
}


def _flat_to_nested(flat: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay a flat OliConfig dict (from the browser) onto nested settings."""
    for flat_key, (group, nested_key) in _FLAT_TO_NESTED.items():
        if flat_key not in flat:
            continue
        if group:
            settings.setdefault(group, {})[nested_key] = flat[flat_key]
        else:
            settings[nested_key] = flat[flat_key]
    return settings


def _nested_to_flat(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten nested settings into the browser's OliConfig shape."""
    flat: Dict[str, Any] = {}
    for flat_key, (group, nested_key) in _FLAT_TO_NESTED.items():
        if group:
            flat[flat_key] = settings.get(group, {}).get(nested_key)
        else:
            flat[flat_key] = settings.get(nested_key)
    return flat


@app.get("/v1/config")
async def get_config() -> Dict[str, Any]:
    """Return the server's current configuration in flat OliConfig form."""
    settings = SettingsManager().load()
    return _nested_to_flat(settings)


@app.put("/v1/config")
async def update_config(flat: Dict[str, Any]) -> Any:
    """Persist an updated config to ``~/.config/oli/settings.json``.

    Only the OliConfig fields from the browser are overlaid onto the existing
    settings, so secrets/env-driven values untouched by the UI are preserved.
    The running agent is not rebuilt; restart the server for changes to take
    effect.
    """
    manager = SettingsManager()
    settings = _flat_to_nested(flat, manager.load())
    try:
        config = manager.to_appconfig(settings)
    except Exception as e:
        return JSONResponse(
            status_code=422,
            content={"error": {"message": f"Invalid config: {e}"}},
        )
    manager.save(settings)
    app.state.config = config
    return _nested_to_flat(settings)


# --- MCP API ---------------------------------------------------------------- #


def _mcp_list() -> List[Dict[str, Any]]:
    """Snapshot the current MCP server configs as a JSON-safe list."""
    return [
        dataclasses.asdict(cfg) for cfg in app.state.agent.mcp_manager.list_servers()
    ]


def _validate_mcp_config(cfg: MCPServerConfig) -> Optional[str]:
    """Return an error message for an invalid MCP server config, else None."""
    if not cfg.name or not cfg.name.strip():
        return "Server name is required"
    if cfg.transport not in ("stdio", "http"):
        return f"Unknown transport: {cfg.transport}"
    if cfg.transport == "http":
        if not cfg.url or not cfg.url.strip():
            return "URL is required for HTTP transport"
    elif not cfg.command or not cfg.command.strip():
        return "Command is required for stdio transport"
    return None


@app.get("/v1/mcp")
async def list_mcp_servers() -> List[Dict[str, Any]]:
    """Return the configured MCP servers (from mcp_servers.json)."""
    return _mcp_list()


@app.post("/v1/mcp")
async def add_mcp_server(cfg: MCPServerConfig) -> Any:
    """Register a new MCP server and persist it to disk."""
    error = _validate_mcp_config(cfg)
    if error:
        return JSONResponse(status_code=422, content={"error": {"message": error}})
    try:
        app.state.agent.mcp_manager.add_server(
            name=cfg.name,
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            transport=cfg.transport,
            url=cfg.url,
        )
    except ValueError as e:
        return JSONResponse(status_code=409, content={"error": {"message": str(e)}})
    return _mcp_list()


@app.put("/v1/mcp/{name}")
async def update_mcp_server(name: str, cfg: MCPServerConfig) -> Any:
    """Update an existing MCP server (matches by path name) and persist."""
    error = _validate_mcp_config(cfg)
    if error:
        return JSONResponse(status_code=422, content={"error": {"message": error}})
    try:
        app.state.agent.mcp_manager.update_server(
            name=name,
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            transport=cfg.transport,
            url=cfg.url,
        )
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": {"message": str(e)}})
    return _mcp_list()


@app.delete("/v1/mcp/{name}")
async def remove_mcp_server(name: str) -> Any:
    """Remove a configured MCP server and persist to disk."""
    try:
        app.state.agent.mcp_manager.remove_server(name)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": {"message": str(e)}})
    return _mcp_list()


# --- Session API ------------------------------------------------------------- #


@app.get("/v1/sessions")
async def list_sessions() -> Dict[str, Any]:
    """List saved sessions for the shared server namespace."""
    store: ConversationStore = app.state.session_store
    return {"sessions": store.list_sessions(_SESSION_SERVER)}


@app.post("/v1/sessions")
async def create_session() -> Dict[str, Any]:
    """Create a new empty session and return it."""
    store: ConversationStore = app.state.session_store
    agent = app.state.agent
    session_id = store.create_session(
        server=_SESSION_SERVER,
        model=str(agent.backend.model or ""),
        profile=agent.profile_name or "",
        system_prompt=agent.system_prompt or "",
    )
    return store.load_session(_SESSION_SERVER, session_id) or {}


@app.get("/v1/sessions/{session_id}")
async def get_session(session_id: str) -> Any:
    """Return a full session including its messages, or 404."""
    store: ConversationStore = app.state.session_store
    data = store.load_session(_SESSION_SERVER, session_id)
    if data is None:
        return JSONResponse(
            status_code=404, content={"error": {"message": "Session not found"}}
        )
    return data


@app.put("/v1/sessions/{session_id}")
async def rename_session(session_id: str, payload: Dict[str, Any]) -> Any:
    """Rename a session, returning the updated session or 404."""
    name = str(payload.get("name", "")).strip()
    if not name:
        return JSONResponse(
            status_code=422, content={"error": {"message": "Name is required"}}
        )
    store: ConversationStore = app.state.session_store
    if not store.rename_session(_SESSION_SERVER, session_id, name):
        return JSONResponse(
            status_code=404, content={"error": {"message": "Session not found"}}
        )
    return store.load_session(_SESSION_SERVER, session_id) or {}


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str) -> Any:
    """Delete a session, returning 404 when it does not exist."""
    store: ConversationStore = app.state.session_store
    if not store.delete_session(_SESSION_SERVER, session_id):
        return JSONResponse(
            status_code=404, content={"error": {"message": "Session not found"}}
        )
    return {"deleted": session_id}


# --- Workspace & filesystem API ---------------------------------------------- #


_FS_LIST_LIMIT = 500


def _get_workspace_manager() -> WorkspaceManager:
    """Return the process-wide ``WorkspaceManager`` (recent workspace list).

    Built in ``_initialize_state``; recreated lazily here so tests that
    rewire ``app.state`` without it keep working.
    """
    manager = getattr(app.state, "workspace_manager", None)
    if manager is None:
        manager = WorkspaceManager()
        app.state.workspace_manager = manager
    return manager


def _workspace_state(agent: Agent, manager: WorkspaceManager) -> Dict[str, Any]:
    """Snapshot the active workspace plus the recently used list."""
    current = getattr(agent._session, "workspace", None)
    current_str = str(current) if current else None
    return {
        "current": current_str,
        "sensitive": bool(current and is_sensitive_path(current)),
        "workspaces": [str(w) for w in manager.list_workspaces()],
    }


@app.get("/v1/workspace")
async def get_workspace() -> Dict[str, Any]:
    """Return the active workspace and the recently used workspace list."""
    return _workspace_state(app.state.agent, _get_workspace_manager())


@app.put("/v1/workspace")
async def set_workspace(payload: Dict[str, Any]) -> Any:
    """Set the shared agent's workspace to an existing directory.

    Mirrors the TUI's ``/workspace set`` (without interactive prompts — the
    browser already required confirmation for sensitive paths): the session
    grants are cleared and, for a sensitive path, ``workspace_sensitive`` is
    re-granted so read scoping is respected.  The path is recorded in the
    recently-used list.
    """
    path_str = str(payload.get("path") or "").strip()
    if not path_str:
        return JSONResponse(
            status_code=422, content={"error": {"message": "Path is required"}}
        )
    try:
        path = Path(path_str).expanduser().resolve()
    except (OSError, RuntimeError):
        return JSONResponse(
            status_code=422,
            content={"error": {"message": f"Invalid path: {path_str}"}},
        )
    if not path.is_dir():
        return JSONResponse(
            status_code=422,
            content={"error": {"message": f"Not a valid directory: {path}"}},
        )
    session = app.state.agent._session
    session.workspace = path
    session._session_grants.clear()
    if is_sensitive_path(path):
        session._session_grants.add(SCOPE_WORKSPACE_SENSITIVE)
    manager = _get_workspace_manager()
    manager.add_workspace(path)
    return _workspace_state(app.state.agent, manager)


@app.delete("/v1/workspace")
async def unset_workspace() -> Any:
    """Clear the active workspace (mirrors the TUI's ``/workspace unset``)."""
    session = app.state.agent._session
    session.workspace = None
    session._session_grants.clear()
    return _workspace_state(app.state.agent, _get_workspace_manager())


def _list_dir(path: Path) -> List[Dict[str, Any]]:
    """List one directory as JSON-safe entries (dirs first, alphabetized)."""
    entries: List[Dict[str, Any]] = []
    for child in path.iterdir():
        try:
            child_type = "dir" if child.is_dir() else "file"
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "path": str(child.resolve()),
                "type": child_type,
                "sensitive": is_sensitive_path(child) if child_type == "dir" else False,
            }
        )
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return entries[:_FS_LIST_LIMIT]


@app.get("/v1/fs/list")
async def list_fs_directory(path: str = "/") -> Any:
    """List a directory on the server for the workspace browser."""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return JSONResponse(
            status_code=422, content={"error": {"message": f"Invalid path: {path}"}}
        )
    if not resolved.is_dir():
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"Not a valid directory: {resolved}"}},
        )
    try:
        entries = _list_dir(resolved)
    except PermissionError:
        return JSONResponse(
            status_code=403,
            content={"error": {"message": f"Permission denied: {resolved}"}},
        )
    return {
        "path": str(resolved),
        "sensitive": is_sensitive_path(resolved),
        "entries": entries,
    }


# --- Chat Completion Routes ----------------------------------------------------------------- #


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    model = str(app.state.agent.backend.model or "")
    return {
        "object": "list",
        "data": [
            {
                "id": f"oli-bot-{model}",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "oli",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest) -> Any:
    if request.stream:
        return StreamingResponse(
            _stream_response(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    completion_text = await _collect_response(request)
    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(app.state.agent.backend.model or ""),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": completion_text},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(completion_text),
    }


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


# --- Startup ---------------------------------------------------------------- #


def _initialize_state() -> None:
    config = SettingsManager().to_appconfig(SettingsManager().load())
    agent = _build_agent(config, mode=API_MODE, profile=API_PROFILE)
    app.state.config = config
    app.state.agent = agent
    app.state.lock = _Lock()
    app.state.session_store = ConversationStore()
    app.state.workspace_manager = WorkspaceManager()

    # Relay todo-list updates (from ``builtin__todowrite``) to WebSocket
    # clients. The tool manager invokes these synchronously; we just append
    # snapshots to a queue the socket loop drains after each agent event.
    _wire_todo_relay(agent)

    # Agent pooling: when enabled, build the pool and register the dispatch
    # tool exactly like the TUI so the root agent can fan work out to
    # specialist sub-agents. Sub-agent events flow to the WebSocket live.
    if config.use_agent_pool:
        try:
            pool = AgentPool(agent.mcp_manager, config=config)
            register_dispatch_tool(
                agent.mcp_manager._builtin_tools, pool, _dispatch_tasks
            )
            app.state.agent_pool = pool
            logger.info("Agent pooling enabled: %s", list(pool.agent_pool.keys()))
            if not pool.has_agents():
                logger.error(
                    "Agent pooling enabled but no sub-agents loaded. "
                    "Checked $OLI_AGENTS_YAML, the package dir, the repo root, "
                    "and ~/.config/oli for agents.yaml. "
                    "The 'dispatch' tool will not be available."
                )
        except Exception as e:
            logger.error("Failed to build agent pool: %s", e)
            app.state.agent_pool = None
    else:
        app.state.agent_pool = None


_initialize_state()


def _print_banner(backend: str, model: str, mode: str, profile: str, pool: str) -> None:
    """Print a startup banner with ASCII art logo and server config."""
    tagline = random.choice(TAGLINES)
    url = f"http://{API_HOST}:{API_PORT}"

    info_rows = [
        ("backend", backend),
        ("model", model),
        ("mode", mode),
        ("profile", profile),
        ("pool", pool),
        ("url", url),
    ]

    key_w = max(len(k) for k, _ in info_rows)

    print()
    print(text2art("oli", font="tarty1").rstrip())
    print("  The API Server\n")
    for key, val in info_rows:
        print(f"  {key:<{key_w}}  {val}")
    print(f"\n  {tagline}")
    print()


def main() -> None:
    import uvicorn

    setup_logging(log_path=app.state.config.log_file)

    backend = app.state.config.backend
    model = str(app.state.agent.backend.model or "(default)")
    mode = app.state.agent.mode
    profile = app.state.agent.profile_name

    pool_state = app.state.agent_pool
    if pool_state is None:
        pool_status = "off"
    elif pool_state.has_agents():
        pool_status = ", ".join(
            f"{p}: {', '.join(pool_state.list_agents(p))}"
            for p in pool_state.agent_pool
        )
    else:
        pool_status = "ENABLED BUT EMPTY — agents.yaml not found"

    _print_banner(backend, model, mode, profile, pool_status)

    logger.info(
        "starting api server host=%s port=%s backend=%s model=%s mode=%s profile=%s",
        API_HOST,
        API_PORT,
        backend,
        model,
        mode,
        profile,
    )
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")


if __name__ == "__main__":
    main()
