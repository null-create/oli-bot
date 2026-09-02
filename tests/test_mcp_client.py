"""MCPClientManager against the v2 SDK surface.

Covers: snake_case field mapping (input_schema / is_error / structured_content),
server__tool name prefixing, per-server tool listing cache + invalidation,
stdio vs HTTP client construction, disconnect teardown, and builtin tool routing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from oli_bot.mcp_client import MCPClientManager


class FakeClient:
    def __init__(self, target, *, list_result=None, call_results=None):
        self.target = target
        self.list_result = list_result
        self.call_results = call_results or {}
        self.entered = 0
        self.list_call_count = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc):
        self.entered -= 1
        return False

    async def list_tools(self, **kwargs):
        self.list_call_count += 1
        return self.list_result

    async def call_tool(self, name, arguments, **kwargs):
        return self.call_results[name]


def _tool(name="foo", description="does a thing", schema=None):
    return SimpleNamespace(
        name=name, description=description, input_schema=schema or {"type": "object"}
    )


def _manager(tmp_path, monkeypatch, *, make=None, stdio_capture=None):
    """Build a manager with `mcp.client.Client` replaced by `make` (default FakeClient).

    `stdio_capture`, when given, is a list; `mcp.client.stdio_client` is replaced by a
    recorder so tests can inspect the StdioServerParameters the manager passes through.
    """
    if make is None:
        make = FakeClient
    monkeypatch.setattr("oli_bot.mcp_client.Client", make)

    if stdio_capture is not None:
        monkeypatch.setattr(
            "oli_bot.mcp_client.stdio_client",
            lambda params: stdio_capture.append(params) or object(),
        )

    return MCPClientManager(config_path=str(tmp_path / "mcp_servers.json"))


@pytest.mark.asyncio
async def test_list_tools_maps_snake_case_fields_and_prefixes(tmp_path, monkeypatch):
    fake = FakeClient(
        "stdio",
        list_result=SimpleNamespace(
            tools=[_tool(name="alpha"), _tool(name="beta", schema=None)]
        ),
    )
    m = _manager(tmp_path, monkeypatch)

    async def fake_get(name):
        return fake

    monkeypatch.setattr(m, "_get_client", fake_get)
    m.add_server("srv", command="echo")

    tools = await m.get_available_tools()

    assert {t["name"] for t in tools} == {"srv__alpha", "srv__beta"}
    assert tools[0]["server"] == "srv"
    assert tools[0]["description"] == "does a thing"
    assert tools[0]["parameters"] == {"type": "object"}


@pytest.mark.asyncio
async def test_tool_listing_cache_invalidated(tmp_path, monkeypatch):
    fake = FakeClient("stdio", list_result=SimpleNamespace(tools=[_tool()]))
    m = _manager(tmp_path, monkeypatch)

    async def fake_get(name):
        return fake

    monkeypatch.setattr(m, "_get_client", fake_get)
    m.add_server("srv", command="echo")

    await m.get_available_tools()
    await m.get_available_tools()
    assert fake.list_call_count == 1
    assert m._tool_cache["srv"][0]["name"] == "srv__foo"

    fake.list_result = SimpleNamespace(tools=[_tool(name="changed")])
    m.remove_server("srv")
    assert "srv" not in m._tool_cache
    assert "srv" not in m._clients

    m.add_server("srv", command="echo")
    await m.get_available_tools()
    assert fake.list_call_count == 2
    assert m._tool_cache["srv"][0]["name"] == "srv__changed"


@pytest.mark.asyncio
async def test_call_tool_surfaces_is_error_and_structured_content(
    tmp_path, monkeypatch
):
    fake = FakeClient(
        "stdio",
        call_results={
            "ok": SimpleNamespace(
                content=[SimpleNamespace(text="hello")],
                structured_content=None,
                is_error=False,
            ),
            "structured": SimpleNamespace(
                content=[],
                structured_content={"key": "value"},
                is_error=False,
            ),
            "boom": SimpleNamespace(
                content=[SimpleNamespace(text="kaboom")],
                structured_content=None,
                is_error=True,
            ),
        },
    )
    m = _manager(tmp_path, monkeypatch)

    async def fake_get(name):
        return fake

    monkeypatch.setattr(m, "_get_client", fake_get)
    m.add_server("srv", command="echo")

    assert await m.call_tool("srv__ok", {}) == "hello"
    assert await m.call_tool("srv__structured", {}) == str({"key": "value"})
    assert await m.call_tool("srv__boom", {}) == "Error: kaboom"


@pytest.mark.asyncio
async def test_http_and_stdio_client_construction(tmp_path, monkeypatch):
    captured = []
    m = _manager(tmp_path, monkeypatch, stdio_capture=captured)
    m.add_server("http-srv", transport="http", url="http://localhost:8000/mcp")
    m.add_server("stdio-srv", command="echo", args=["-n", "hi"])

    http_client = await m._get_client("http-srv")
    stdio_client = await m._get_client("stdio-srv")

    assert isinstance(http_client, FakeClient)
    assert http_client.target == "http://localhost:8000/mcp"

    assert isinstance(stdio_client, FakeClient)
    assert len(captured) == 1
    assert captured[0].command == "echo"
    assert captured[0].args == ["-n", "hi"]


@pytest.mark.asyncio
async def test_disconnect_all_closes_clients_and_clears_cache(tmp_path, monkeypatch):
    make = lambda target: FakeClient(
        target, list_result=SimpleNamespace(tools=[_tool()])
    )
    m = _manager(tmp_path, monkeypatch, make=make)
    m.add_server("srv", command="echo")

    await m.get_available_tools()
    fake = m._clients["srv"]
    assert fake.entered == 1
    assert m._tool_cache

    await m.disconnect_all()

    assert not m._clients
    assert not m._tool_cache
    assert fake.entered == 0


@pytest.mark.asyncio
async def test_builtin_tool_routing_uses_builtin_manager(tmp_path, monkeypatch):
    from oli_bot.tools.manager import BuiltinToolManager

    builtin = BuiltinToolManager()
    builtin._tools.clear()

    async def handler(x=None, **kwargs):
        return "ok"

    builtin.register_tool("echo", "echo tool", {"type": "object"}, handler)
    m = _manager(tmp_path, monkeypatch)
    m._builtin_tools = builtin

    assert await m.call_tool("builtin__echo", {"x": 1}) == "ok"
    assert (
        await m.call_tool("builtin__missing", {})
        == "Error: Unknown built-in tool 'missing'"
    )


@pytest.mark.asyncio
async def test_get_plan_tools_combines_mcp_and_builtin_plan_tools(
    tmp_path, monkeypatch
):
    from oli_bot.tools.manager import BuiltinToolManager, PLAN_TOOLS

    fake = FakeClient("stdio", list_result=SimpleNamespace(tools=[_tool(name="alpha")]))
    builtin = BuiltinToolManager()

    m = _manager(tmp_path, monkeypatch)
    m._builtin_tools = builtin

    async def fake_get(name):
        return fake

    monkeypatch.setattr(m, "_get_client", fake_get)
    m.add_server("srv", command="echo")

    tools = await m.get_plan_tools()
    names = {t["name"] for t in tools}

    assert "srv__alpha" in names
    assert names & {f"builtin__{n}" for n in PLAN_TOOLS} == {
        f"builtin__{n}" for n in PLAN_TOOLS
    }
    assert "builtin__write_file" not in names
