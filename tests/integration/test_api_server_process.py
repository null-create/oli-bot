"""Real ``oli-server`` process boot over the OpenAI-compatible REST API.

Launches ``python -m oli_bot.api`` as a subprocess (isolated ``HOME`` so a
real ``~/.config/oli/settings.json`` cannot leak host configuration in,
isolated ``OLI_*`` env) and drives the live server against the scriptable
mock upstream wire server. Every request crosses a real process boundary.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from .conftest import _free_port, WireMockServer, cc

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def server_env(isolated_home):
    env = {}
    for key, value in os.environ.items():
        if key.startswith("OLI_"):
            continue
        env[key] = value
    env["HOME"] = str(isolated_home)
    env["PYTHONUNBUFFERED"] = "1"
    return env


@pytest.mark.process
def test_server_boots_and_streams_chat_completion(server_env, tmp_path):
    mock = WireMockServer(route_prefix="/v1")
    mock.script(
        (
            "stream",
            [
                cc(content="Hello from "),
                cc(content="the mocked backend."),
                cc(finish="stop", usage={"prompt_tokens": 3, "completion_tokens": 4}),
            ],
        )
    )

    port = _free_port()
    env = dict(server_env)
    env.update(
        {
            "OLI_API_HOST": "127.0.0.1",
            "OLI_API_PORT": str(port),
            "OLI_BACKEND": "openai",
            "OLI_OPENAI_BASE_URL": f"{mock.base_url}/v1",
            "OLI_OPENAI_API_KEY": "test-key",
            "OLI_OPENAI_MODEL": "mocked",
        }
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "oli_bot.api"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        assert _wait_healthy(port)["status"] == "ok"

        resp = httpx.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            json={
                "model": "mocked",
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=30,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert (
            data["choices"][0]["message"]["content"] == "Hello from the mocked backend."
        )
        assert mock.calls  # the chat completion really went out over the wire

        models = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=15)
        assert models.status_code == 200
        ids = [m["id"] for m in models.json()["data"]]
        assert any("mocked" in mid for mid in ids), ids

        sessions = httpx.get(f"http://127.0.0.1:{port}/v1/sessions", timeout=15)
        assert sessions.status_code == 200
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


def _wait_healthy(port, timeout=25.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return r.json()
        except Exception as e:
            last = e
        time.sleep(0.25)
    raise AssertionError(f"oli-server at :{port} never came up healthy: {last}")
