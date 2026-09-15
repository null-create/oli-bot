"""Tests for the OpenAI-compatible API server (``api_server.py``).

These tests drive the FastAPI app over ``TestClient`` (httpx-based, so it
works with the fully async agent loop via the ASGI transport).  A stub backend
(following the pattern in ``test_agent_process.py``) replaces the real model
backend so the harness runs without a live model or network.  The module-level
``_initialize_state()`` builds a real backend from env/settings, so each test
fixture re-initializes ``app.state`` with the stub harness.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import oli_bot.api_server as api_server
from oli_bot.agent import Agent
from oli_bot.config import AppConfig
from oli_bot.mcp_client import MCPClientManager
from oli_bot.models import TextChunk, ToolCall, ToolCallChunk
from oli_bot.sessions import Session
from oli_bot.tools.manager import BuiltinToolManager


class _TextStub:
    model = "stub-text"

    async def stream_generate(self, messages, tools=None):
        yield TextChunk("Hello ")
        yield TextChunk("from stub")


class _ToolThenTextStub:
    model = "stub-tool"

    def __init__(self):
        self._calls = 0

    async def stream_generate(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            yield ToolCallChunk(
                [ToolCall(id="c1", name="builtin__echo", parameters={"x": 1})]
            )
        else:
            yield TextChunk("done after tool")


class _RaisingStub:
    model = "stub-raise"

    async def stream_generate(self, messages, tools=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover


class _TodoThenTextStub:
    model = "stub-todo"

    def __init__(self):
        self._calls = 0

    async def stream_generate(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            yield ToolCallChunk(
                [
                    ToolCall(
                        id="c1",
                        name="builtin__todowrite",
                        parameters={
                            "todos": [
                                {
                                    "content": "do x",
                                    "status": "pending",
                                    "priority": "high",
                                }
                            ]
                        },
                    )
                ]
            )
        else:
            yield TextChunk("todos updated")


class _HistoryProbeStub:
    model = "stub-probe"

    def __init__(self):
        self.seen = []

    async def stream_generate(self, messages, tools=None):
        self.seen.append(len(messages))
        yield TextChunk(f"n={len(messages)}")


class _StubMCP:
    async def call_tool(self, name, params, confirm_callback=None, **kwargs):
        return "result-of-echo"

    protocol_version = None
    server_capabilities = None


def _make_harness(backend) -> Agent:
    """Build a minimal Agent harness like ``api_server._build_agent`` but with
    a stub backend, so tests don't touch the real network/config."""
    config = AppConfig(_env_file=None, backend="ollama", ollama_model="stub")
    session = Session(workspace=None)
    builtin = BuiltinToolManager(
        session=session,
        backend=backend,
        config=config,
    )

    def _echo(x: int) -> str:
        return f"echoed-{x}"

    builtin.register_tool(
        name="echo",
        description="Echo back the input.",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        },
        handler=_echo,
    )

    mcp = MCPClientManager(
        builtin_tools=builtin,
        offline_mode=True,
        config_path="/tmp/opencode/test-mcp-servers.json",
    )
    agent = Agent(
        role="root",
        backend=backend,
        mcp_manager=mcp,
        profile_name="none",
        config=config,
    )
    agent.set_mode("agent")
    agent._session = session
    return agent


class _API:
    def __init__(self, application: FastAPI):
        self.application = application
        self.client = TestClient(application)

    def reset(self, backend) -> None:
        self.application.state.agent = _make_harness(backend)
        api_server._wire_todo_relay(self.application.state.agent)
        self.application.state.lock = api_server._Lock()
        self.application.state.config = AppConfig(_env_file=None, backend="ollama")


@pytest.fixture
def api():
    harness = _API(api_server.app)
    harness.reset(_TextStub())
    yield harness
    # Restore the real initialized state for any subsequent non-test usage.
    harness.reset(_TextStub())


def _text_chunks(body: str) -> list[str]:
    chunks: list[str] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: ") :].strip()
            if payload == "[DONE]":
                continue
            data = json.loads(payload)
            delta = data["choices"][0]["delta"]
            if delta.get("content"):
                chunks.append(delta["content"])
    return chunks


def _recv_until_done(ws) -> list[dict]:
    """Collect WebSocket frames until a ``done`` frame arrives."""
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done":
            return frames


# --------------------------------------------------------------------------- #
# /v1/models                                                                   #
# --------------------------------------------------------------------------- #


def test_list_models(api):
    resp = api.client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == "oli-bot-stub-text"
    assert body["data"][0]["object"] == "model"


# --------------------------------------------------------------------------- #
# /v1/chat/completions (non-stream)                                            #
# --------------------------------------------------------------------------- #


def test_chat_completion_non_stream(api):
    resp = api.client.post(
        "/v1/chat/completions",
        json={"model": "stub-text", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "stub-text"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hello from stub"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["completion_tokens"] >= 1


def test_chat_completion_tool_loop(api):
    api.reset(_ToolThenTextStub())
    # The first stream_generate yields a tool call, so the harness executes
    # the built-in tool (auto-allowed, no permission blocker since workspace
    # is None and confirm auto-returns "session") then streams the final text.
    resp = api.client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "use the tool"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "done after tool"


def test_chat_completion_error(api):
    api.reset(_RaisingStub())
    resp = api.client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert "error" in body
    assert body["error"]["type"] == "server_error"


# --------------------------------------------------------------------------- #
# /v1/chat/completions (stream)                                                #
# --------------------------------------------------------------------------- #


def test_chat_completion_stream(api):
    resp = api.client.post(
        "/v1/chat/completions",
        json={
            "model": "stub-text",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    text = resp.text
    assert text.rstrip().endswith("data: [DONE]")
    assert "".join(_text_chunks(text)) == "Hello from stub"


def test_chat_completion_stream_error(api):
    api.reset(_RaisingStub())
    resp = api.client.post(
        "/v1/chat/completions",
        json={
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200  # SSE frame carries the error
    assert "error" in resp.text


def test_chat_completion_multimodal_text(api):
    # text/image_url parts: with a text-only backend the image is rendered as
    # a bracketed placeholder; text is preserved.
    resp = api.client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "https://x/i.png"}},
                    ],
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "Hello from stub"


# --------------------------------------------------------------------------- #
# /health                                                                      #
# --------------------------------------------------------------------------- #


def test_health(api):
    resp = api.client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# /v1/chat (WebSocket)                                                         #
# --------------------------------------------------------------------------- #


def test_websocket_chat(api):
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json() == {"type": "connected", "data": {}}
        ws.send_json({"content": "hi"})
        frames = _recv_until_done(ws)
        types = [f["type"] for f in frames]
        assert "text_chunk" in types
        assert (
            "".join(f["data"]["text"] for f in frames if f["type"] == "text_chunk")
            == "Hello from stub"
        )
        done = frames[-1]
        assert done["type"] == "done"
        assert done["data"]["full_text"] == "Hello from stub"


def test_websocket_tool_events(api):
    api.reset(_ToolThenTextStub())
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "use the tool"})
        frames = _recv_until_done(ws)
        types = [f["type"] for f in frames]
        assert "tool_call_executing" in types
        assert "tool_call_result" in types
        exec_frame = next(f for f in frames if f["type"] == "tool_call_executing")
        assert exec_frame["data"]["name"] == "builtin__echo"
        assert exec_frame["data"]["parameters"] == {"x": 1}
        result_frame = next(f for f in frames if f["type"] == "tool_call_result")
        assert result_frame["data"]["result"] == "echoed-1"
        assert frames[-1]["type"] == "done"
        assert frames[-1]["data"]["full_text"] == "done after tool"


def test_websocket_stateful_history(api):
    probe = _HistoryProbeStub()
    api.reset(probe)
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "first"})
        frames = _recv_until_done(ws)
        # stream_generate sees [system header, user1] on the first turn
        assert frames[0]["data"]["text"] == "n=2"
        ws.send_json({"content": "second"})
        frames = _recv_until_done(ws)
        # system + user1 + assistant1 + user2 = 4 messages on the second turn
        assert frames[0]["data"]["text"] == "n=4"
        assert probe.seen == [2, 4]


def test_websocket_clear(api):
    probe = _HistoryProbeStub()
    api.reset(probe)
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "first"})
        _recv_until_done(ws)
        ws.send_json({"action": "clear"})
        assert ws.receive_json() == {"type": "cleared", "data": {}}
        ws.send_json({"content": "after clear"})
        frames = _recv_until_done(ws)
        # history was reset: back down to system + user = 2 messages
        assert frames[0]["data"]["text"] == "n=2"
        assert probe.seen == [2, 2]


def test_websocket_error(api):
    api.reset(_RaisingStub())
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "hi"})
        frames = _recv_until_done(ws)
        types = [f["type"] for f in frames]
        assert "error" in types
        assert frames[-1]["type"] == "done"
        assert frames[-1]["data"]["full_text"] == ""  # empty Done on failure


def test_websocket_invalid_json(api):
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_text("not json")
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["data"]["message"] == "Invalid JSON payload"
        # connection stays alive for a valid message after the bad one
        ws.send_json({"content": "hi"})
        assert _recv_until_done(ws)[-1]["type"] == "done"


def test_websocket_empty_message(api):
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "   "})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert frame["data"]["message"] == "Empty message"


def test_websocket_todo_relay(api):
    api.reset(_TodoThenTextStub())
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "make a todo"})
        frames = _recv_until_done(ws)
        todo_frames = [f for f in frames if f["type"] == "todo"]
        assert len(todo_frames) == 1
        assert todo_frames[0]["data"]["todos"][0]["content"] == "do x"
        assert todo_frames[0]["data"]["todos"][0]["status"] == "pending"
        assert "task_id" not in todo_frames[0]["data"]


# --------------------------------------------------------------------------- #
# /v1/mcp (MCP server configuration)                                           #
# --------------------------------------------------------------------------- #


def _clear_mcp(harness):
    manager = harness.application.state.agent.mcp_manager
    for name in list(manager.servers):
        manager.remove_server(name)


def test_mcp_list_empty(api):
    _clear_mcp(api)
    resp = api.client.get("/v1/mcp")
    assert resp.status_code == 200
    assert resp.json() == []


def test_mcp_add_and_list(api):
    _clear_mcp(api)
    resp = api.client.post(
        "/v1/mcp",
        json={
            "name": "filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "mcp-server-filesystem", "/tmp"],
            "env": {"FOO": "bar"},
        },
    )
    assert resp.status_code == 200
    servers = resp.json()
    assert len(servers) == 1
    assert servers[0]["name"] == "filesystem"
    assert servers[0]["transport"] == "stdio"
    assert servers[0]["command"] == "npx"
    assert servers[0]["args"] == ["-y", "mcp-server-filesystem", "/tmp"]
    assert servers[0]["env"] == {"FOO": "bar"}
    assert servers[0]["url"] == ""

    resp = api.client.get("/v1/mcp")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_mcp_add_http(api):
    _clear_mcp(api)
    resp = api.client.post(
        "/v1/mcp",
        json={
            "name": "remote",
            "transport": "http",
            "url": "http://localhost:3000/mcp",
        },
    )
    assert resp.status_code == 200
    server = resp.json()[0]
    assert server["name"] == "remote"
    assert server["transport"] == "http"
    assert server["url"] == "http://localhost:3000/mcp"
    assert server["command"] == ""


def test_mcp_add_duplicate_conflict(api):
    _clear_mcp(api)
    api.client.post(
        "/v1/mcp",
        json={"name": "dup", "transport": "stdio", "command": "echo"},
    )
    resp = api.client.post(
        "/v1/mcp",
        json={"name": "dup", "transport": "stdio", "command": "echo"},
    )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["error"]["message"]


def test_mcp_add_validation(api):
    _clear_mcp(api)
    # Missing name
    resp = api.client.post("/v1/mcp", json={"transport": "stdio", "command": "echo"})
    assert resp.status_code == 422
    # stdio without a command
    resp = api.client.post(
        "/v1/mcp", json={"name": "x", "transport": "stdio", "command": ""}
    )
    assert resp.status_code == 422
    # http without a url
    resp = api.client.post(
        "/v1/mcp", json={"name": "x", "transport": "http", "url": ""}
    )
    assert resp.status_code == 422


def test_mcp_update(api):
    _clear_mcp(api)
    api.client.post(
        "/v1/mcp",
        json={
            "name": "fs",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "server"],
        },
    )
    resp = api.client.put(
        "/v1/mcp/fs",
        json={
            "name": "fs",
            "transport": "http",
            "url": "http://localhost:9000/mcp",
        },
    )
    assert resp.status_code == 200
    server = next(s for s in resp.json() if s["name"] == "fs")
    assert server["transport"] == "http"
    assert server["url"] == "http://localhost:9000/mcp"
    assert server["command"] == ""


def test_mcp_update_missing_404(api):
    _clear_mcp(api)
    resp = api.client.put(
        "/v1/mcp/nope",
        json={"name": "nope", "transport": "stdio", "command": "echo"},
    )
    assert resp.status_code == 404


def test_mcp_remove(api):
    _clear_mcp(api)
    api.client.post(
        "/v1/mcp", json={"name": "fs", "transport": "stdio", "command": "echo"}
    )
    resp = api.client.delete("/v1/mcp/fs")
    assert resp.status_code == 200
    assert resp.json() == []

    resp = api.client.delete("/v1/mcp/fs")
    assert resp.status_code == 404


def test_mcp_persists_to_disk(api):
    _clear_mcp(api)
    manager = api.application.state.agent.mcp_manager
    api.client.post(
        "/v1/mcp",
        json={"name": "persist", "transport": "stdio", "command": "echo"},
    )
    assert "persist" in manager.servers
    raw = open(manager.config_path).read()
    assert '"persist"' in raw


# --------------------------------------------------------------------------- #
# _event_to_frame (sub-agent relay)                                            #
# --------------------------------------------------------------------------- #


def test_event_to_frame_sub_agent_lifecycle():
    started = api_server._event_to_frame(
        api_server.SubAgentStarted(
            task_id="run-1",
            agent_name="analyst",
            pool_name="default",
            task="analyze x",
        )
    )
    assert started == {
        "type": "sub_agent_started",
        "data": {
            "task_id": "run-1",
            "agent_name": "analyst",
            "pool_name": "default",
            "task": "analyze x",
        },
    }

    progress = api_server._event_to_frame(
        api_server.SubAgentProgress(
            task_id="run-1",
            agent_name="analyst",
            activity="calling grep",
            status="running",
        )
    )
    assert progress["type"] == "sub_agent_progress"
    assert progress["data"]["activity"] == "calling grep"

    completed = api_server._event_to_frame(
        api_server.SubAgentCompleted(
            task_id="run-1",
            agent_name="analyst",
            status="done",
            full_text="result",
        )
    )
    assert completed == {
        "type": "sub_agent_completed",
        "data": {
            "task_id": "run-1",
            "agent_name": "analyst",
            "status": "done",
            "full_text": "result",
        },
    }


def test_event_to_frame_sub_agent_wrapped_inner():
    frame = api_server._event_to_frame(
        api_server.SubAgentEvent(
            task_id="run-1",
            agent_name="analyst",
            event=api_server.StreamChunk("working"),
        )
    )
    # Inner frame is forwarded with the owning run's identity attached.
    assert frame == {
        "type": "text_chunk",
        "data": {"text": "working", "task_id": "run-1", "agent_name": "analyst"},
    }
