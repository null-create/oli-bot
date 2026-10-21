"""Tests for the browser UI's flat ``/v1/config`` routes."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import oli_bot.api.routers.config as config_router
from oli_bot.settings import DEFAULT_SETTINGS, SettingsManager


def _leaf_paths(node, prefix=()):
    paths = set()
    for key, value in node.items():
        path = prefix + (key,)
        if isinstance(value, dict) and value:
            paths |= _leaf_paths(value, path)
        else:
            paths.add(path)
    return paths


def _mapped_paths():
    return {
        (group, nested_key) if group else (nested_key,)
        for group, nested_key in config_router._FLAT_TO_NESTED.values()
    }


def test_flat_mapping_covers_every_default_setting():
    """Every settings.json leaf must be reachable from the browser, and every
    flat key must point at a real leaf — so a new setting can't ship
    invisible to ``GET/PUT /v1/config``."""
    defaults = _leaf_paths(DEFAULT_SETTINGS)
    mapped = _mapped_paths()
    assert mapped == defaults, f"unmapped: {defaults - mapped}; stale: {mapped - defaults}"


def test_flat_to_nested_and_back_round_trips():
    flat = config_router._nested_to_flat(DEFAULT_SETTINGS)
    settings = config_router._flat_to_nested(flat, {})
    assert settings == DEFAULT_SETTINGS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_router,
        "SettingsManager",
        lambda: SettingsManager(config_dir=tmp_path),
    )
    app = FastAPI()
    app.include_router(config_router.router)
    return TestClient(app)


def test_get_returns_session_workspace_and_backend_extras(client):
    data = client.get("/v1/config").json()
    assert data["openai_responses_enabled"] is False
    assert data["ollama_context_window"] == 8192
    assert data["first_chunk_timeout"] == 600.0
    assert data["default_profile"] == "default"
    assert data["session_auto_save"] is True
    assert data["session_resume_prompt"] is True
    assert data["workspace_max_workspaces"] == 20
    assert data["use_oauth"] is False
    assert data["agents_yaml"] == ""


def test_put_persists_new_keys(tmp_path, client):
    payload = {
        "openai_responses_enabled": True,
        "ollama_context_window": 16384,
        "first_chunk_timeout": 120.0,
        "default_profile": "researcher",
        "session_auto_save": False,
        "session_resume_prompt": False,
        "workspace_max_workspaces": 5,
    }
    res = client.put("/v1/config", json=payload)
    assert res.status_code == 200
    echoed = res.json()
    for key, value in payload.items():
        assert echoed[key] == value

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["openai"]["responses_enabled"] is True
    assert saved["ollama"]["context_window"] == 16384
    assert saved["model_params"]["first_chunk_timeout"] == 120.0
    assert saved["session"] == {
        "auto_save": False,
        "resume_prompt": False,
        "default_profile": "researcher",
    }
    assert saved["workspace"] == {"max_workspaces": 5}

    fetched = client.get("/v1/config").json()
    for key, value in payload.items():
        assert fetched[key] == value


def test_put_leaves_untouched_settings_alone(tmp_path, client):
    settings = SettingsManager(config_dir=tmp_path).load()
    settings["openai"]["api_key"] = "sk-secret"
    settings["temperature"] = 0.42
    SettingsManager(config_dir=tmp_path).save(settings)

    client.put("/v1/config", json={"session_auto_save": False})

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["openai"]["api_key"] == "sk-secret"
    assert saved["temperature"] == 0.42
