"""BuiltinToolManager.call_tool gating matrix.

Covers: unknown-tool error, profile permission enforcement, session
confirm_callback branches (deny/once/session), dry-run gating for destructive
tools, offline gating for network tools, tool-result truncation, and awaiting
of coroutine handlers.
"""

from __future__ import annotations

import pytest

from oli_bot.config import AppConfig
from oli_bot.profiles.permissions import ProfilePermissionEnforcer
from oli_bot.profiles.schema import PermissionsManifest, ProfileManifest
from oli_bot.sessions import Session, SCOPE_WRITE
from oli_bot.tools.manager import BuiltinToolManager


def _bare_manager(**kwargs) -> BuiltinToolManager:
    """Return a manager whose tool table is empty so we can register only
    the tools each test needs — avoids interference from the default set."""
    m = BuiltinToolManager(**kwargs)
    m._tools.clear()
    return m


# ---------- unknown tool ------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    m = _bare_manager()
    result = await m.call_tool("does_not_exist", {})
    assert result.startswith("Error:")
    assert "does_not_exist" in result


# ---------- profile permission gating ----------------------------------------


def _enforcer(allow, deny=()) -> ProfilePermissionEnforcer:
    manifest = ProfileManifest(
        name="p",
        permissions=PermissionsManifest(
            allow_tools=list(allow),
            deny_tools=list(deny),
        ),
    )
    return ProfilePermissionEnforcer(manifest)


@pytest.mark.asyncio
async def test_profile_denies_tool_before_handler_runs():
    called = {"n": 0}

    async def handler():
        called["n"] += 1
        return "should not run"

    m = _bare_manager(permission_enforcer=_enforcer(allow=["builtin__other"]))
    m.register_tool("blocked", "d", {"type": "object", "properties": {}}, handler)

    result = await m.call_tool("blocked", {})
    assert "not permitted" in result
    assert "builtin__blocked" in result
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_profile_allows_tool_lets_handler_run():
    async def handler():
        return "ok"

    m = _bare_manager(permission_enforcer=_enforcer(allow=["builtin__*"]))
    m.register_tool("allowed", "d", {"type": "object", "properties": {}}, handler)

    assert await m.call_tool("allowed", {}) == "ok"


# ---------- session confirm_callback flow ------------------------------------


@pytest.mark.asyncio
async def test_confirm_callback_deny_short_circuits_handler(tmp_path):
    ran = {"n": 0}

    async def handler(file_path, content):
        ran["n"] += 1
        return "wrote"

    session = Session(workspace=tmp_path)
    m = _bare_manager(session=session)
    m.register_tool(
        "write_file",
        "d",
        {"type": "object", "properties": {}},
        handler,
    )

    async def deny_cb(_desc):
        return "deny"

    result = await m.call_tool(
        "write_file",
        {"file_path": str(tmp_path / "a.txt"), "content": "x"},
        confirm_callback=deny_cb,
    )
    assert "denied" in result.lower()
    assert ran["n"] == 0


@pytest.mark.asyncio
async def test_confirm_callback_once_does_not_grant_session_scope(tmp_path):
    async def handler(file_path, content):
        return "wrote"

    session = Session(workspace=tmp_path)
    m = _bare_manager(session=session)
    m.register_tool("write_file", "d", {"type": "object", "properties": {}}, handler)

    prompts = {"n": 0}

    async def once_cb(_desc):
        prompts["n"] += 1
        return "once"

    await m.call_tool(
        "write_file",
        {"file_path": str(tmp_path / "a.txt"), "content": "x"},
        confirm_callback=once_cb,
    )
    await m.call_tool(
        "write_file",
        {"file_path": str(tmp_path / "b.txt"), "content": "y"},
        confirm_callback=once_cb,
    )
    # "once" must re-prompt on the second call.
    assert prompts["n"] == 2
    assert SCOPE_WRITE not in session._session_grants


@pytest.mark.asyncio
async def test_confirm_callback_session_grants_scope_for_rest_of_session(tmp_path):
    async def handler(file_path, content):
        return "wrote"

    session = Session(workspace=tmp_path)
    m = _bare_manager(session=session)
    m.register_tool("write_file", "d", {"type": "object", "properties": {}}, handler)

    prompts = {"n": 0}

    async def session_cb(_desc):
        prompts["n"] += 1
        return "session"

    await m.call_tool(
        "write_file",
        {"file_path": str(tmp_path / "a.txt"), "content": "x"},
        confirm_callback=session_cb,
    )
    await m.call_tool(
        "write_file",
        {"file_path": str(tmp_path / "b.txt"), "content": "y"},
        confirm_callback=session_cb,
    )
    # First call prompts, second must NOT (scope granted for session).
    assert prompts["n"] == 1
    assert SCOPE_WRITE in session._session_grants


# ---------- dry-run gating ---------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_skips_destructive_tool(tmp_path):
    ran = {"n": 0}

    async def handler(file_path, content):
        ran["n"] += 1
        return "wrote"

    session = Session(workspace=tmp_path)
    session.grant(SCOPE_WRITE, session=True)  # bypass permission prompt

    cfg = AppConfig(_env_file=None, dry_run=True, offline_mode=False)
    m = _bare_manager(session=session, config=cfg)
    m.register_tool("write_file", "d", {"type": "object", "properties": {}}, handler)

    result = await m.call_tool(
        "write_file", {"file_path": str(tmp_path / "a.txt"), "content": "x"}
    )
    assert "[DRY RUN]" in result
    assert "write_file" in result
    assert ran["n"] == 0


@pytest.mark.asyncio
async def test_dry_run_does_not_affect_read_only_tools(tmp_path):
    async def handler():
        return "read"

    cfg = AppConfig(_env_file=None, dry_run=True, offline_mode=False)
    m = _bare_manager(config=cfg)
    m.register_tool("think", "d", {"type": "object", "properties": {}}, handler)
    result = await m.call_tool("think", {})
    assert result == "read"


# ---------- offline gating ---------------------------------------------------


@pytest.mark.asyncio
async def test_offline_mode_blocks_network_tool_before_handler():
    ran = {"n": 0}

    async def handler(query):
        ran["n"] += 1
        return "results"

    cfg = AppConfig(_env_file=None, offline_mode=True)
    m = _bare_manager(config=cfg)
    m.register_tool("websearch", "d", {"type": "object", "properties": {}}, handler)

    result = await m.call_tool("websearch", {"query": "x"})
    assert "offline" in result.lower()
    assert ran["n"] == 0


@pytest.mark.asyncio
async def test_offline_mode_does_not_block_local_tools():
    async def handler():
        return "ok"

    cfg = AppConfig(_env_file=None, offline_mode=True)
    m = _bare_manager(config=cfg)
    m.register_tool("think", "d", {"type": "object", "properties": {}}, handler)
    assert await m.call_tool("think", {}) == "ok"


# ---------- coroutine handler awaiting ---------------------------------------


@pytest.mark.asyncio
async def test_sync_handler_result_is_returned_as_string():
    def handler(x):
        return x + 1  # returns int — manager must stringify

    m = _bare_manager()
    m.register_tool("inc", "d", {"type": "object", "properties": {}}, handler)
    assert await m.call_tool("inc", {"x": 5}) == "6"


@pytest.mark.asyncio
async def test_coroutine_handler_is_awaited_not_returned_as_object():
    async def handler():
        return "async-ok"

    m = _bare_manager()
    m.register_tool("aok", "d", {"type": "object", "properties": {}}, handler)
    result = await m.call_tool("aok", {})
    # Regression: if the manager forgot to `await` the coroutine, the result
    # would stringify to something like "<coroutine object ...>".
    assert result == "async-ok"
    assert "coroutine" not in result


@pytest.mark.asyncio
async def test_handler_exception_is_captured_not_propagated():
    async def handler():
        raise RuntimeError("boom")

    m = _bare_manager()
    m.register_tool("boom", "d", {"type": "object", "properties": {}}, handler)
    result = await m.call_tool("boom", {})
    assert result.startswith("Error executing boom:")
    assert "boom" in result


# ---------- truncation -------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_is_truncated_at_configured_tier():
    async def handler():
        return "x" * 500

    cfg = AppConfig(
        _env_file=None,
        offline_mode=False,
        truncation_max_chars_small=100,
        truncation_max_chars_large=10000,
    )
    m = _bare_manager(config=cfg)
    m.register_tool("big", "d", {"type": "object", "properties": {}}, handler)

    m.model_tier = "small"
    small = await m.call_tool("big", {})
    assert len(small) < 500
    assert "truncated" in small

    m.model_tier = "large"
    large = await m.call_tool("big", {})
    assert large == "x" * 500


# ---------- plan mode tool set ------------------------------------------------


def test_get_plan_tool_definitions_matches_plan_tools_set():
    from oli_bot.tools.manager import PLAN_TOOLS

    m = BuiltinToolManager()
    names = {t["name"] for t in m.get_plan_tool_definitions()}
    assert names == {f"builtin__{n}" for n in PLAN_TOOLS}
    assert "builtin__notebook" in names
    assert "builtin__todowrite" in names
    assert "builtin__write_file" not in names


# ---------- notebook plan-page auto-increment --------------------------------


def test_notebook_set_auto_increments_plan_pages(tmp_path, monkeypatch):
    from oli_bot.tools.memory import _notebook_handler

    monkeypatch.chdir(tmp_path)

    r1 = _notebook_handler("set", page="plan-foo", content="v1")
    assert "plan-foo" in r1
    assert (tmp_path / "notes" / "plan-foo.md").read_text() == "v1"

    r2 = _notebook_handler("set", page="plan-foo", content="v2")
    assert "plan-foo-2" in r2
    assert (tmp_path / "notes" / "plan-foo-2.md").read_text() == "v2"
    assert (tmp_path / "notes" / "plan-foo.md").read_text() == "v1"

    r3 = _notebook_handler("set", page="plan-foo", content="v3")
    assert "plan-foo-3" in r3
    assert (tmp_path / "notes" / "plan-foo-3.md").read_text() == "v3"


def test_notebook_set_overwrites_non_plan_pages(tmp_path, monkeypatch):
    from oli_bot.tools.memory import _notebook_handler

    monkeypatch.chdir(tmp_path)

    _notebook_handler("set", page="scratch", content="v1")
    _notebook_handler("set", page="scratch", content="v2")
    assert (tmp_path / "notes" / "scratch.md").read_text() == "v2"
    assert not (tmp_path / "notes" / "scratch-2.md").exists()
