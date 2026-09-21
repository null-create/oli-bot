"""Tests for the session REST API and WebSocket session persistence.

These drive the real ``api_server`` app over ``TestClient`` with the same stub
harness as ``test_api_server.py``, but point the ``ConversationStore`` at a
temporary directory so nothing touches ``~/.config/oli/sessions``.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import oli_bot.api_server as api_server
from oli_bot.agent import Agent
from oli_bot.config import AppConfig
from oli_bot.mcp_client import MCPClientManager
from oli_bot.models import TextChunk
from oli_bot.sessions import ConversationStore, Session
from oli_bot.tools.manager import BuiltinToolManager


class _TextStub:
    model = "stub-text"

    async def stream_generate(self, messages, tools=None):
        yield TextChunk("Hello ")
        yield TextChunk("from stub")


class _HistoryProbeStub:
    """Counts the messages it sees so a test can assert persisted history."""

    model = "stub-text"

    def __init__(self):
        self.seen = []

    async def stream_generate(self, messages, tools=None):
        self.seen.append([m.role for m in messages])
        yield TextChunk(f"n={len(messages)}")


def _make_harness(backend) -> Agent:
    config = AppConfig(_env_file=None, backend="ollama", ollama_model="stub")
    session = Session(workspace=None)
    builtin = BuiltinToolManager(
        session=session,
        backend=backend,
        config=config,
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
    def __init__(self, application: FastAPI, store):
        self.application = application
        self.client = TestClient(application)
        self.store = store

    def reset(self, backend) -> None:
        self.application.state.agent = _make_harness(backend)
        api_server._wire_todo_relay(self.application.state.agent)
        self.application.state.lock = asyncio.Lock()
        self.application.state.config = AppConfig(_env_file=None, backend="ollama")
        self.application.state.session_store = self.store


@pytest.fixture
def api(tmp_path):
    store = ConversationStore(sessions_dir=str(tmp_path / "sessions"))
    harness = _API(api_server.app, store)
    harness.reset(_TextStub())
    yield harness


def _recv_until_done(ws) -> list[dict]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == "done":
            return frames


# --- REST round-trip -------------------------------------------------------- #


def test_sessions_round_trip(api):
    resp = api.client.post("/v1/sessions")
    assert resp.status_code == 200
    created = resp.json()
    assert created["id"]
    assert created["messages"] == []

    sid = created["id"]

    listed = api.client.get("/v1/sessions")
    assert listed.status_code == 200
    ids = [s["id"] for s in listed.json()["sessions"]]
    assert sid in ids

    loaded = api.client.get(f"/v1/sessions/{sid}")
    assert loaded.status_code == 200
    assert loaded.json()["id"] == sid

    renamed = api.client.put(f"/v1/sessions/{sid}", json={"name": "stub session"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "stub session"

    deleted = api.client.delete(f"/v1/sessions/{sid}")
    assert deleted.status_code == 200

    gone = api.client.get(f"/v1/sessions/{sid}")
    assert gone.status_code == 404


def test_sessions_conflicts(api):
    assert api.client.get("/v1/sessions/nope").status_code == 404
    assert api.client.put("/v1/sessions/nope", json={"name": "x"}).status_code == 404
    assert api.client.delete("/v1/sessions/nope").status_code == 404
    created = api.client.post("/v1/sessions").json()
    assert (
        api.client.put(f"/v1/sessions/{created['id']}", json={"name": "  "}).status_code
        == 422
    )


# --- WebSocket persistence -------------------------------------------------- #


def test_websocket_persists_a_turn_to_the_session(api):
    sid = api.client.post("/v1/sessions").json()["id"]
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "hi", "session_id": sid})
        frames = _recv_until_done(ws)
        assert frames[-1]["type"] == "done"
        assert "session_created" not in [f["type"] for f in frames]

    stored = api.client.get(f"/v1/sessions/{sid}").json()
    roles = [m["role"] for m in stored["messages"]]
    assert roles == ["user", "assistant"]
    assert stored["messages"][0]["content"] == "hi"
    assert stored["messages"][1]["content"] == "Hello from stub"


def test_websocket_recreates_missing_session(api):
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "hi", "session_id": "nope"})
        frames = _recv_until_done(ws)
        created = [f for f in frames if f["type"] == "session_created"]
        assert len(created) == 1
        new_id = created[0]["data"]["session"]["id"]
        assert new_id != "nope"

    stored = api.client.get(f"/v1/sessions/{new_id}").json()
    assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]


def test_websocket_stateful_history_over_session(api):
    probe = _HistoryProbeStub()
    api.reset(probe)
    sid = api.client.post("/v1/sessions").json()["id"]
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "first", "session_id": sid})
        _recv_until_done(ws)
        ws.send_json({"content": "second", "session_id": sid})
        _recv_until_done(ws)

    # Each turn sees the full accumulated history from the persisted file:
    # turn 2 sees system header + user(first) + assistant + user(second).
    assert probe.seen[1][-2:] == ["assistant", "user"]
    assert probe.seen[1][-1] == "user"

    stored = api.client.get(f"/v1/sessions/{sid}").json()
    contents = [m["content"] for m in stored["messages"]]
    assert contents == ["first", "n=2", "second", "n=4"]


def test_websocket_clear_wipes_persisted_session(api):
    sid = api.client.post("/v1/sessions").json()["id"]
    with api.client.websocket_connect("/v1/chat") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"content": "hi", "session_id": sid})
        _recv_until_done(ws)
        ws.send_json({"action": "clear", "session_id": sid})
        assert ws.receive_json()["type"] == "cleared"

    stored = api.client.get(f"/v1/sessions/{sid}").json()
    assert stored["messages"] == []
