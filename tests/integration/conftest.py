"""Shared fixtures for integration tests.

These tests cross *real* boundaries:
  - real HTTP/SSE to a mock OpenAI / Ollama wire server (uvicorn on a real
    localhost port via a background thread),
  - a real MCP stdio / streamable-HTTP server (the official ``mcp`` SDK
    running in a subprocess),
  - real subprocess execution through the built-in tool handlers,
  - the ``oli-server`` process entrypoint.

Like ``tests/unit/conftest.py``, ``OLI_*`` env vars are scrubbed so no stray
host configuration leaks in from the environment.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

# Never let a stray OLI_* env var pollute test defaults (see unit conftest).
for key in list(os.environ):
    if key.startswith("OLI_"):
        os.environ.pop(key)


# --------------------------------------------------------------------------- #
# Scriptable wire-mock helpers                                                 #
# --------------------------------------------------------------------------- #


def cc(
    content=None,
    delta=None,
    finish=None,
    *,
    usage=None,
    chunk_id="chatcmpl-mock-1",
    index=0,
    model="mocked",
):
    """Build one OpenAI ``chat.completion.chunk`` SSE payload dict."""
    d = dict(delta or {})
    if content is not None:
        d["content"] = content
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": 1_700_000_000,
        "model": model,
        "choices": [{"index": index, "delta": d, "finish_reason": finish}],
    }
    if usage is not None:
        payload["usage"] = usage
        payload["choices"] = []
    return payload


def tool_delta(index, tc_id=None, name=None, args=None):
    """Build one OpenAI ``ChatCompletionChunkToolCall`` delta dict with only
    the fields present (later deltas omit ``id``/``name``)."""
    entry = {"index": index}
    if tc_id is not None:
        entry["id"] = tc_id
    if name is not None or args is not None:
        fn = {}
        if name is not None:
            fn["name"] = name
        if args is not None:
            fn["arguments"] = args
        if fn:
            entry["function"] = fn
    return entry


def nonstream_completion(content="final answer", finish="stop", usage=None):
    """Build a non-streaming ``chat.completion`` JSON payload."""
    return {
        "id": "chatcmpl-mock-1",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "mocked",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": usage
        or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def ollama_part(content=None, tool_calls=None, done=False, counts=None):
    """Build one NDJSON ``/api/chat`` response line payload (ollama shape)."""
    message = {"role": "assistant"}
    if content is not None:
        message["content"] = content
    if tool_calls:
        message["tool_calls"] = tool_calls
    part = {
        "model": "mocked",
        "created_at": "2024-01-01T00:00:00Z",
        "done": done,
        "message": message,
    }
    if counts:
        part.update(counts)
    return part


def ollama_tool(name, arguments):
    return {"function": {"name": name, "arguments": arguments}}


# --------------------------------------------------------------------------- #
# WireMockServer — a real uvicorn server backing a scriptable model endpoint  #
# --------------------------------------------------------------------------- #


class WireMockServer:
    """Real HTTP server (uvicorn in a background thread) that answers
    ``/v1/chat/completions`` and ``/v1/models``.

    ``scripts`` is consumed per request: each entry is either a response spec
    tuple or a callable ``(body: dict) -> spec``. Specs::

        ("stream", [event, ...], auto_done=True)  # OpenAI SSE
        ("ollama", [part, ...])                   # ollama NDJSON
        ("json", payload_dict)                    # plain JSON body
        ("status", code, text)                    # bare HTTP status

    Stream events are dicts (json-encoded as ``data:`` frames) or raw strings
    (emitted verbatim, for malformed-wire scenarios).
    """

    def __init__(self, *, route_prefix="/v1"):
        self.scripts: list = []
        self.calls: list[dict] = []
        self.handler = None
        self.route_prefix = route_prefix
        self.app = FastAPI()
        self.app.add_api_route(f"{route_prefix}/models", self._models, methods=["GET"])
        self.app.add_api_route(
            f"{route_prefix}/chat/completions", self._chat, methods=["POST"]
        )
        if route_prefix == "":
            self.app.add_api_route("/api/chat", self._chat, methods=["POST"])
        self.base_url = None
        self._server = None
        self._thread = None
        self._start()

    def _start(self):
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("wire mock server failed to start")
            time.sleep(0.002)
        sock = self._server.servers[0].sockets[0]
        self.base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)

    async def _models(self):
        return {
            "object": "list",
            "data": [
                {
                    "id": "mocked",
                    "object": "model",
                    "created": 1_700_000_000,
                    "owned_by": "oli-tests",
                }
            ],
        }

    async def _chat(self, request: Request):
        body = await request.json()
        self.calls.append(body)
        spec = self._pop_spec(body)
        kind = spec[0]
        if kind == "stream":
            events = spec[1]
            auto_done = spec[2] if len(spec) > 2 else True

            async def gen():
                for ev in events:
                    if isinstance(ev, dict):
                        yield f"data: {json.dumps(ev)}\n\n"
                    else:
                        yield ev if ev.endswith("\n") else f"{ev}\n"
                if auto_done:
                    yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")
        if kind == "ollama":

            async def ollama_gen():
                for part in spec[1]:
                    if isinstance(part, dict):
                        yield json.dumps(part) + "\n"
                    else:
                        yield part if part.endswith("\n") else f"{part}\n"

            return StreamingResponse(ollama_gen(), media_type="application/x-ndjson")
        if kind == "json":
            return JSONResponse(spec[1])
        if kind == "status":
            return Response(status_code=spec[1], content=spec[2])
        raise ValueError(f"unknown wire spec kind: {kind}")

    def _pop_spec(self, body):
        if self.handler is not None:
            return self.handler(body)
        if not self.scripts:
            return ("status", 500, "no script configured for this request")
        entry = self.scripts.pop(0)
        if callable(entry):
            return entry(body)
        return entry

    # -- convenience ----------------------------------------------------------

    def script(self, *specs):
        """Deposit response specs to be consumed in order across requests."""
        self.scripts = list(specs)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_openai():
    server = WireMockServer(route_prefix="/v1")
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def mock_ollama():
    server = WireMockServer(route_prefix="")
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def openai_backend(mock_openai):
    from oli_bot.backends import OpenAIBackend

    return OpenAIBackend(
        api_key="test-key",
        base_url=f"{mock_openai.base_url}/v1",
        model="mocked",
    )


@pytest.fixture
def ollama_backend(mock_ollama):
    from oli_bot.backends import OllamaBackend

    return OllamaBackend(model="mocked", base_url=mock_ollama.base_url)


# --------------------------------------------------------------------------- #
# Real MCP servers (official SDK in a subprocess)                             #
# --------------------------------------------------------------------------- #

_MCP_STDIO_SERVER_SRC = textwrap.dedent("""\
    import asyncio

    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="mock-stdio")

    @server.tool()
    def add(a: int, b: int) -> str:
        return str(a + b)

    @server.tool()
    def echo(text: str) -> str:
        return text

    @server.tool()
    def get_object() -> dict:
        return {"key": "value", "n": 42}

    @server.tool()
    def fail(message: str) -> str:
        raise RuntimeError(message)

    asyncio.run(server.run_stdio_async())
    """)


@pytest.fixture
def mcp_stdio_script(tmp_path):
    """A real MCP stdio server entrypoint script run via ``sys.executable``."""
    path = tmp_path / "mock_mcp_stdio_server.py"
    path.write_text(_MCP_STDIO_SERVER_SRC)
    return path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mcp_http_script(tmp_path):
    """Start a real MCP streamable-HTTP server in a subprocess.

    Yields a dict with ``command`` / ``args`` / ``url`` / ``proc``; the
    subprocess is terminated on teardown.
    """
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    src = textwrap.dedent(f"""\
        import asyncio

        from mcp.server.mcpserver import MCPServer

        server = MCPServer(name="mock-http")

        @server.tool()
        def ping() -> str:
            return "pong"

        @server.tool()
        def add(a: int, b: int) -> str:
            return str(a + b)

        asyncio.run(server.run_streamable_http_async(
            host="127.0.0.1", port={port}, streamable_http_path="/mcp"
        ))
        """)
    path = tmp_path / "mock_mcp_http_server.py"
    path.write_text(src)
    proc = subprocess.Popen(
        [sys.executable, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.kill()
        raise RuntimeError("MCP HTTP server never came up")
    try:
        yield {"command": sys.executable, "args": [str(path)], "url": url, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
async def mcp_manager_factory(tmp_path):
    from oli_bot.mcp_client import MCPClientManager

    created = []

    def make(offline_mode=False):
        m = MCPClientManager(
            offline_mode=offline_mode,
            config_path=str(tmp_path / "mcp_servers.json"),
        )
        created.append(m)
        return m

    yield make
    for m in created:
        await m.disconnect_all()


# --------------------------------------------------------------------------- #
# Full harness: real backend + real tools + real MCP                          #
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_full_agent(tmp_path, mock_openai, mcp_manager_factory):
    """Build a full ``Agent`` wired to the mock OpenAI wire server.

    Real OpenAIBackend (over real HTTP/SSE), default built-in tool set,
    real MCP manager, and a ``Session`` scoped to a temp workspace.
    Auto-approves every permission request (``"session"``).
    """

    def build(mode="agent", *, workspace=None, offline_mode=False):
        from oli_bot.agent import Agent
        from oli_bot.backends import create_model_backend
        from oli_bot.config import AppConfig
        from oli_bot.sessions import Session
        from oli_bot.tools.manager import BuiltinToolManager

        config = AppConfig(
            _env_file=None,
            backend="openai",
            offline_mode=offline_mode,
            openai_base_url=f"{mock_openai.base_url}/v1",
            openai_api_key="test-key",
            openai_model="mocked",
        )
        backend = create_model_backend(
            mock_openai.base_url,
            "openai",
            model="mocked",
            api_key="test-key",
            base_url=f"{mock_openai.base_url}/v1",
        )
        ws = workspace if workspace is not None else tmp_path
        session = Session(workspace=ws)
        builtin = BuiltinToolManager(session=session, backend=backend, config=config)
        mcp = mcp_manager_factory(offline_mode=offline_mode)
        mcp._builtin_tools = builtin
        agent = Agent(
            role="root",
            backend=backend,
            mcp_manager=mcp,
            profile_name="none",
            config=config,
        )
        agent.set_mode(mode)
        agent._session = session
        return agent, session

    return build


@pytest.fixture
def auto_allow():
    async def _allow(description):
        return "session"

    return _allow
