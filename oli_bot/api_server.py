"""OpenAI-compatible REST API over the oli agent harness.

Serves ``/v1/models`` and ``/v1/chat/completions`` (streaming + non-streaming)
using FastAPI, plugging the same ``Agent`` tool loop that powers the TUI into
any workflow that speaks the OpenAI wire protocol (the ``openai`` Python SDK,
curl, or any other HTTP client).

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
import json
import logging
import threading
import time
import uuid
import random
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from art import text2art
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from .agent import Agent
from .backends import create_model_backend, ModelBackend
from .config import AppConfig, configs
from .logger import setup_logging
from .mcp_client import MCPClientManager
from .models import (
    AssistantResponse,
    Done,
    Error,
    ImageAttachment,
    Message,
    StreamChunk,
    ChatCompletionMessage,
    ChatCompletionRequest,
)
from .sessions import Session, is_sensitive_path
from .settings import SettingsManager
from .screens.taglines import TAGLINES
from .tools.manager import BuiltinToolManager

logger = logging.getLogger(__name__)

API_HOST = configs.api_host
API_PORT = configs.api_port
API_PROFILE = configs.api_profile
API_MODE = configs.api_mode


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
        builtin_tools=builtin_tools,
        offline_mode=config.offline_mode,
    )
    agent = Agent(
        role="root",
        backend=backend,
        mcp_manager=mcp_manager,
        profile_name=profile,
        config=config,
    )
    agent.set_mode(mode)
    if agent.permission_enforcer is not None:
        builtin_tools._permission_enforcer = agent.permission_enforcer
    agent._session = session
    return agent


async def _api_confirm(description: str) -> str:
    """Auto-allow every permission scope (API mode has no interactive prompt)."""
    return "session"


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


# --- Routes ----------------------------------------------------------------- #


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    model = str(app.state.agent.backend.model or "")
    return {
        "object": "list",
        "data": [
            {
                "id": model,
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


_initialize_state()


def _print_banner(backend: str, model: str, mode: str, profile: str) -> None:
    """Print a startup banner with ASCII art logo and server config."""
    tagline = random.choice(TAGLINES)
    url = f"http://{API_HOST}:{API_PORT}"

    info_rows = [
        ("backend", backend),
        ("model", model),
        ("mode", mode),
        ("profile", profile),
        ("url", url),
    ]

    key_w = max(len(k) for k, _ in info_rows)

    print()
    print(text2art("oli", font="tarty1").rstrip())
    print("  API Server\n")
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

    _print_banner(backend, model, mode, profile)

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
