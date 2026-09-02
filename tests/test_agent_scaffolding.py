"""Regression tests for AgentPool dispatch."""

import asyncio
from unittest.mock import mock_open, patch

import pytest

from oli_bot.agent import Agent, AgentPool, _expand_env
from oli_bot.backends import create_model_backend
from oli_bot.models import ProfileData
from oli_bot.profiles.permissions import ProfilePermissionEnforcer
from oli_bot.profiles.schema import ProfileManifest


class _StubBackend:
    model = "stub"


class _StubMCP:
    async def call_tool(self, *a, **k):
        return ""


class _StubProfileManager:
    def load_profile(self, name: str) -> ProfileData:
        manifest = ProfileManifest(name=name)
        return ProfileData(
            system_prompt="",
            manifest=manifest,
            permission_enforcer=ProfilePermissionEnforcer(manifest),
        )


def _make_agent(role: str) -> Agent:
    return Agent(
        role=role,
        backend=_StubBackend(),
        mcp_manager=_StubMCP(),
        profile_manager=_StubProfileManager(),
    )


@pytest.fixture
def pool():
    """Return an AgentPool whose _build_agent_pool is bypassed."""
    with patch.object(AgentPool, "_build_agent_pool", lambda self: None):
        yield AgentPool(_StubMCP())


def test_agent_pool_returns_agent_by_name(pool):
    a = _make_agent("search")
    b = _make_agent("write")

    pool.agent_pool["default"] = {"search": a, "write": b}
    assert pool.select_agent("default", "search") is a
    assert pool.select_agent("default", "write") is b


def test_agent_pool_missing_pool_raises_with_name(pool):
    with pytest.raises(ValueError, match="default not found in agent pool"):
        pool.select_agent("default", "search")


def test_agent_pool_missing_agent_raises_with_name(pool):
    pool.agent_pool["default"] = {}

    with pytest.raises(ValueError, match="search not found in agent pool 'default'"):
        pool.select_agent("default", "search")


def test_expand_env_substitutes_variables(monkeypatch):
    monkeypatch.setenv("OLI_TEST_VAR", "resolved-value")
    assert _expand_env("${OLI_TEST_VAR}") == "resolved-value"


def test_expand_env_passes_through_none_and_empty():
    assert _expand_env(None) is None
    assert _expand_env("") == ""


def test_expand_env_falls_back_to_extra_env_when_not_in_os_environ(monkeypatch):
    """Regression: vars set only in .env (pydantic-settings) must still
    expand.  pydantic-settings does NOT inject into os.environ, so
    os.path.expandvars leaves ${VAR} unresolved.  _expand_env must fall
    back to the extra_env dict derived from AppConfig.model_dump().
    """
    # Ensure the var is NOT present in the real environment.
    monkeypatch.delenv("OLI_OPENAI_API_KEY", raising=False)
    extra = {"OLI_OPENAI_API_KEY": "dotenv-secret-key"}
    assert _expand_env("${OLI_OPENAI_API_KEY}", extra_env=extra) == "dotenv-secret-key"


def test_expand_env_extra_env_does_not_shadow_real_os_environ(monkeypatch):
    """os.environ takes priority over extra_env: os.path.expandvars runs
    first and resolves the token before the fallback regex is applied.
    """
    monkeypatch.setenv("OLI_OPENAI_API_KEY", "shell-key")
    extra = {"OLI_OPENAI_API_KEY": "dotenv-key-should-not-win"}
    assert _expand_env("${OLI_OPENAI_API_KEY}", extra_env=extra) == "shell-key"


def test_create_model_backend_overrides_take_precedence():
    backend = create_model_backend(
        url=None,
        backend_type="ollama",
        model="some-model",
        base_url="http://override:1234",
    )
    assert backend.base_url == "http://override:1234"


def test_agent_pool_skips_root_agent_and_expands_env(monkeypatch):
    monkeypatch.setenv("OLI_TEST_BACKEND_URL", "http://sub-agent-host:9999")
    fake_config = {
        "agent-pools": [
            {
                "name": "default",
                "agents": [
                    {
                        "name": "root-agent",
                        "model": "gpt-x",
                        "backend": {"type": "ollama"},
                    },
                    {
                        "name": "worker",
                        "model": "gpt-y",
                        "backend": {
                            "type": "ollama",
                            "base_url": "${OLI_TEST_BACKEND_URL}",
                        },
                    },
                ],
            }
        ]
    }
    with (
        patch("oli_bot.agent.os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data="")),
        patch("oli_bot.agent.yaml.safe_load", return_value=fake_config),
    ):
        built_pool = AgentPool(_StubMCP())

    assert "root-agent" not in built_pool.agent_pool.get("default", {})
    assert built_pool.list_agents("default") == ["worker"]
    assert (
        built_pool.agent_pool["default"]["worker"].backend.base_url
        == "http://sub-agent-host:9999"
    )


def test_agent_pool_expands_env_in_model(monkeypatch):
    """Regression: a `${VAR}` model reference must be expanded against the
    process environment like base_url/api_key, not passed through literally.
    """
    monkeypatch.setenv("OLI_TEST_MODEL", "acme/test-model")
    fake_config = {
        "agent-pools": [
            {
                "name": "default",
                "agents": [
                    {
                        "name": "worker",
                        "model": "${OLI_TEST_MODEL}",
                        "backend": {"type": "openai", "api_key": "test-key"},
                    },
                ],
            }
        ]
    }
    with (
        patch("oli_bot.agent.os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data="")),
        patch("oli_bot.agent.yaml.safe_load", return_value=fake_config),
    ):
        built_pool = AgentPool(_StubMCP())

    backend = built_pool.agent_pool["default"]["worker"].backend
    assert backend.model == "acme/test-model"


def test_dispatch_runs_sub_agents_concurrently():
    """Prove a batch dispatch runs tasks in parallel, not sequentially."""

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        entered_count = 0

        async def slow_task(name: str) -> str:
            nonlocal entered_count
            entered_count += 1
            if entered_count == 2:
                entered.set()
            await asyncio.wait_for(entered.wait(), timeout=1)
            await release.wait()
            return f"done-{name}"

        async def run_task(name: str) -> str:
            return await slow_task(name)

        gathered = asyncio.gather(run_task("a"), run_task("b"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert entered_count == 2
        release.set()
        results = await gathered
        assert results == ["done-a", "done-b"]

    asyncio.run(scenario())


def test_agent_pool_expands_api_key_from_dotenv_only(monkeypatch):
    """Regression: ${OLI_OPENAI_API_KEY} in agents.yaml must resolve even
    when the var is only in .env (pydantic-settings) and NOT in os.environ.
    """
    # Ensure OLI_OPENAI_API_KEY is absent from the real environment.
    monkeypatch.delenv("OLI_OPENAI_API_KEY", raising=False)

    fake_config = {
        "agent-pools": [
            {
                "name": "default",
                "agents": [
                    {
                        "name": "analyst-agent",
                        "model": "haiku",
                        "backend": {
                            "type": "openai",
                            "base_url": "https://api.example.com",
                            "api_key": "${OLI_OPENAI_API_KEY}",
                        },
                    },
                ],
            }
        ]
    }

    # Simulate pydantic-settings having loaded the key from .env by patching
    # configs.openai_api_key — this is what AppConfig provides even when
    # the env var is not exported to the shell.
    import oli_bot.agent as agent_module

    original_api_key = agent_module.configs.openai_api_key
    try:
        agent_module.configs.openai_api_key = "dotenv-only-api-key"
        with (
            patch("oli_bot.agent.os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="")),
            patch("oli_bot.agent.yaml.safe_load", return_value=fake_config),
        ):
            built_pool = AgentPool(_StubMCP())
    finally:
        agent_module.configs.openai_api_key = original_api_key

    analyst = built_pool.agent_pool["default"]["analyst-agent"]
    assert analyst.backend.api_key == "dotenv-only-api-key"


def test_agent_pool_last_agent_wins_on_duplicate_name(pool):
    """Regression: agent names are keys in the pool dict, so duplicate names
    resolve to the last assignment and do not silently merge or crash.
    """
    a = _make_agent("search")
    b = _make_agent("search")

    pool.agent_pool["default"] = {"search": a}
    pool.agent_pool["default"]["search"] = b
    assert pool.select_agent("default", "search") is b
