"""Tests for the workspace + filesystem REST API (``api_server.py``)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import oli_bot.api_server as api_server
from oli_bot.agent import Agent
from oli_bot.config import AppConfig
from oli_bot.mcp_client import MCPClientManager
from oli_bot.models import TextChunk
from oli_bot.sessions import (
    ConversationStore,
    Session,
    WorkspaceManager,
    is_sensitive_path,
)
from oli_bot.tools.manager import BuiltinToolManager


class _TextStub:
    model = "stub-text"

    async def stream_generate(self, messages, tools=None):
        yield TextChunk("Hello from stub")


def _make_harness(backend, workspace=None) -> Agent:
    config = AppConfig(_env_file=None, backend="ollama", ollama_model="stub")
    session = Session(workspace=workspace)
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
    def __init__(self, application: FastAPI, tmp_path):
        self.application = application
        self.client = TestClient(application)
        self.tmp_path = tmp_path

    def reset(self, backend, workspace=None) -> None:
        self.application.state.agent = _make_harness(backend, workspace)
        api_server._wire_todo_relay(self.application.state.agent)
        self.application.state.lock = api_server._Lock()
        self.application.state.config = AppConfig(_env_file=None, backend="ollama")
        self.application.state.session_store = ConversationStore(
            sessions_dir=str(self.tmp_path / "sessions")
        )
        self.application.state.workspace_manager = WorkspaceManager(
            data_dir=self.tmp_path / "workspaces"
        )


@pytest.fixture
def api(tmp_path):
    harness = _API(api_server.app, tmp_path)
    harness.reset(_TextStub())
    yield harness


# --- Workspace REST ----------------------------------------------------------- #


def test_workspace_defaults_to_none(api):
    body = api.client.get("/v1/workspace")
    assert body.status_code == 200
    state = body.json()
    assert state["current"] is None
    assert state["sensitive"] is False
    assert state["workspaces"] == []


def test_set_workspace_round_trip(api, tmp_path):
    target = tmp_path / "ws"
    target.mkdir()

    resp = api.client.put("/v1/workspace", json={"path": str(target)})
    assert resp.status_code == 200
    state = resp.json()
    assert state["current"] == str(target.resolve())
    assert state["sensitive"] is False
    assert str(target.resolve()) in state["workspaces"]
    assert api.application.state.agent._session.workspace == target.resolve()

    got = api.client.get("/v1/workspace").json()
    assert got["current"] == str(target.resolve())


def test_set_workspace_rejects_missing_path(api):
    assert (
        api.client.put("/v1/workspace", json={"path": "/no/such/dir"}).status_code
        == 422
    )
    assert api.client.put("/v1/workspace", json={"path": "  "}).status_code == 422


def test_set_sensitive_workspace_grants_scope(api, tmp_path):
    sensitive = tmp_path / ".config"
    sensitive.mkdir()
    assert is_sensitive_path(sensitive)

    resp = api.client.put("/v1/workspace", json={"path": str(sensitive)})
    assert resp.status_code == 200
    state = resp.json()
    assert state["sensitive"] is True
    session = api.application.state.agent._session
    assert session.workspace == sensitive.resolve()
    assert "workspace_sensitive" in session._session_grants


def test_setting_workspace_clears_prior_grants(api, tmp_path):
    session = api.application.state.agent._session
    session._session_grants.add("write")

    target = tmp_path / "ws"
    target.mkdir()
    api.client.put("/v1/workspace", json={"path": str(target)})
    assert session._session_grants == set()


def test_recent_workspaces_are_bounded(api, tmp_path):
    for i in range(25):
        d = tmp_path / f"ws-{i}"
        d.mkdir()
        api.client.put("/v1/workspace", json={"path": str(d)})

    state = api.client.get("/v1/workspace").json()
    assert len(state["workspaces"]) == 20
    # Most recently set path is first.
    assert state["workspaces"][0] == str((tmp_path / "ws-24").resolve())


def test_unset_workspace(api, tmp_path):
    target = tmp_path / "ws"
    target.mkdir()
    api.client.put("/v1/workspace", json={"path": str(target)})

    resp = api.client.delete("/v1/workspace")
    assert resp.status_code == 200
    state = resp.json()
    assert state["current"] is None
    assert api.application.state.agent._session.workspace is None
    # The path stays in the recently used list.
    assert str(target.resolve()) in state["workspaces"]


# --- Filesystem listing ------------------------------------------------------- #


def test_fs_list_dirs_first_alphabetical(api, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "b.txt").write_text("b")
    (base / "a_dir").mkdir()
    (base / "z.txt").write_text("z")
    (base / "y_dir").mkdir()

    resp = api.client.get("/v1/fs/list", params={"path": str(base)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(base.resolve())
    names = [e["name"] for e in body["entries"]]
    types = [e["type"] for e in body["entries"]]
    assert names == ["a_dir", "y_dir", "b.txt", "z.txt"]
    assert types[:2] == ["dir", "dir"]
    assert types[2:] == ["file", "file"]


def test_fs_list_marks_sensitive_dirs(api, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / ".ssh").mkdir()
    (base / "plain").mkdir()
    (base / "data.txt").write_text("x")

    resp = api.client.get("/v1/fs/list", params={"path": str(base)})
    assert resp.status_code == 200
    by_name = {e["name"]: e for e in resp.json()["entries"]}
    assert by_name[".ssh"]["type"] == "dir"
    assert by_name[".ssh"]["sensitive"] is True
    assert by_name["plain"]["sensitive"] is False
    assert by_name["data.txt"]["type"] == "file"
    assert by_name["data.txt"]["sensitive"] is False


def test_fs_list_missing_dir_404(api):
    assert (
        api.client.get("/v1/fs/list", params={"path": "/no/such/dir"}).status_code
        == 404
    )


def test_fs_list_falls_back_to_root(api):
    resp = api.client.get("/v1/fs/list")
    assert resp.status_code == 200
    assert resp.json()["path"] == "/"
