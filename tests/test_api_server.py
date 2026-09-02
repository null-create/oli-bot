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


class _StubMCP:
    async def call_tool(self, name, params, confirm_callback=None):
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

    def _echo(x: dict) -> str:
        return f"echoed-{x.get('x')}"

    builtin.register_tool(
        name="echo",
        description="Echo back the input.",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
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


# --------------------------------------------------------------------------- #
# /v1/models                                                                   #
# --------------------------------------------------------------------------- #


def test_list_models(api):
    resp = api.client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == "stub-text"
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
