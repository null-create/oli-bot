"""Tests for the agent profile REST API (``/v1/profiles``)."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import oli_bot.api_server as api_server
from oli_bot.agent import Agent
from oli_bot.config import AppConfig
from oli_bot.mcp_client import MCPClientManager
from oli_bot.models import TextChunk
from oli_bot.profiles.manager import ProfileManager
from oli_bot.sessions import ConversationStore, Session, WorkspaceManager
from oli_bot.tools.manager import BuiltinToolManager

_PROFILE_BODIES = {
    "default": "# default\n\nYou are a helpful assistant.\n",
    "coder": "# coder\n\nYou write code.\n",
    "planner": "# planner\n\nYou make plans.\n",
}


class _TextStub:
    model = "stub-text"

    async def stream_generate(self, messages, tools=None):
        yield TextChunk("Hello from stub")


@pytest.fixture
def profiles_dir(tmp_path):
    """A real on-disk profiles directory wired into the test agent."""
    root = tmp_path / "profiles"
    root.mkdir()
    for name, body in _PROFILE_BODIES.items():
        target = root / name
        target.mkdir()
        (target / "AGENTS.md").write_text(body, encoding="utf-8")
    return root


def _make_harness(backend, profiles_dir, profile_name="default") -> Agent:
    config = AppConfig(_env_file=None, backend="ollama", ollama_model="stub")
    session = Session(workspace=None)
    builtin = BuiltinToolManager(session=session, backend=backend, config=config)
    mcp = MCPClientManager(
        builtin_tools=builtin,
        offline_mode=True,
        config_path="/tmp/opencode/test-mcp-servers.json",
    )
    return Agent(
        role="root",
        backend=backend,
        mcp_manager=mcp,
        profile_manager=ProfileManager(profiles_dir=str(profiles_dir)),
        profile_name=profile_name,
        config=config,
    )


class _API:
    def __init__(self, application: FastAPI, tmp_path, profiles_dir):
        self.application = application
        self.client = TestClient(application)
        self.tmp_path = tmp_path
        self.profiles_dir = profiles_dir

    def reset(self, backend, profile_name="default") -> None:
        agent = _make_harness(backend, self.profiles_dir, profile_name)
        self.application.state.agent = agent
        api_server._wire_todo_relay(agent)
        self.application.state.lock = asyncio.Lock()
        self.application.state.config = AppConfig(_env_file=None, backend="ollama")
        self.application.state.session_store = ConversationStore(
            sessions_dir=str(self.tmp_path / "sessions")
        )
        self.application.state.workspace_manager = WorkspaceManager(
            data_dir=self.tmp_path / "workspaces"
        )


@pytest.fixture
def api(tmp_path, profiles_dir, monkeypatch):
    # ``SettingsManager()`` resolves ``~/.config/oli``; redirect HOME so the
    # endpoint's persistence write stays inside tmp_path.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    harness = _API(api_server.app, tmp_path, profiles_dir)
    harness.reset(_TextStub())
    yield harness


def _names(api):
    return {p["name"]: p for p in api.client.get("/v1/profiles").json()}


# --- Listing ------------------------------------------------------------------ #


def test_list_profiles_returns_disk_profiles_with_manifest(api):
    body = api.client.get("/v1/profiles")
    assert body.status_code == 200
    profiles = {p["name"]: p for p in body.json()}
    assert set(profiles) == set(_PROFILE_BODIES)
    assert profiles["coder"]["version"] == "0.1.0"
    assert profiles["coder"]["default_model_tier"] == "large"
    assert profiles["coder"]["allow_tools"] == ["builtin__*"]


def test_list_profiles_flags_exactly_one_active(api):
    active = [p["name"] for p in api.client.get("/v1/profiles").json() if p["active"]]
    assert active == ["default"]


def test_list_profiles_active_follows_selected_profile(api):
    api.client.put("/v1/profiles/coder")
    active = [p["name"] for p in api.client.get("/v1/profiles").json() if p["active"]]
    assert active == ["coder"]


def test_list_profiles_degrades_on_unloadable_profile(api):
    # A manifest whose base chains to itself cannot be resolved.
    broken = api.profiles_dir / "broken"
    broken.mkdir()
    (broken / "AGENTS.md").write_text("# broken\n", encoding="utf-8")
    (broken / "profile.json").write_text(
        '{"schema_version": 1, "name": "broken", "base": "broken"}', encoding="utf-8"
    )
    resp = api.client.get("/v1/profiles")
    assert resp.status_code == 200
    profiles = {p["name"]: p for p in resp.json()}
    assert profiles["broken"]["active"] is False
    assert profiles["broken"]["description"].startswith("unavailable:")
    # Sibling profiles must still be listed.
    assert profiles["coder"]["active"] is False


# --- Selection ---------------------------------------------------------------- #


def test_select_profile_swaps_agent_state_and_returns_summary(api):
    resp = api.client.put("/v1/profiles/coder")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "coder"
    assert body["active"] is True
    agent = api.application.state.agent
    assert agent.profile_name == "coder"
    assert "write code" in agent.system_prompt


def test_select_profile_updates_running_config_and_settings(api, tmp_path):
    api.client.put("/v1/profiles/planner")
    assert api.application.state.config.api_profile == "planner"
    saved = (tmp_path / ".config" / "oli" / "settings.json").read_text(encoding="utf-8")
    assert "planner" in saved


def test_select_unknown_profile_returns_404_and_keeps_current(api):
    resp = api.client.put("/v1/profiles/does-not-exist")
    assert resp.status_code == 404
    assert "does-not-exist" in resp.json()["error"]["message"]
    assert api.application.state.agent.profile_name == "default"
    assert api.application.state.config.api_profile != "does-not-exist"
