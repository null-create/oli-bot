"""Real MCP server end-to-end (official SDK in a subprocess).

The unit suite swaps ``mcp.client.Client`` for fakes. Here a genuine
``MCPServer`` runs as a subprocess over stdio / streamable-HTTP and is driven
through the real ``MCPClientManager`` (Tool Discovery → invoke → is_error /
structured content), plus a full agent tool-loop that calls a server tool.
"""

from __future__ import annotations

import sys

import pytest

from .conftest import cc, tool_delta


async def _make_stdio_manager(mcp_manager_factory, mcp_stdio_script):
    m = mcp_manager_factory(offline_mode=False)
    m.add_server(
        "mock",
        command=sys.executable,
        args=[str(mcp_stdio_script)],
    )
    return m


@pytest.mark.integration
async def test_stdio_tool_discovery(mcp_manager_factory, mcp_stdio_script):
    m = await _make_stdio_manager(mcp_manager_factory, mcp_stdio_script)
    tools = await m.get_available_tools()
    names = {t["name"] for t in tools}
    assert {"mock__add", "mock__echo", "mock__get_object", "mock__fail"} <= names
    add = next(t for t in tools if t["name"] == "mock__add")
    assert add["server"] == "mock"
    assert add["parameters"]["type"] == "object"


@pytest.mark.integration
async def test_stdio_tool_invocation(mcp_manager_factory, mcp_stdio_script):
    m = await _make_stdio_manager(mcp_manager_factory, mcp_stdio_script)
    assert await m.call_tool("mock__add", {"a": 2, "b": 3}) == "5"
    assert await m.call_tool("mock__echo", {"text": "hello"}) == "hello"


@pytest.mark.integration
async def test_stdio_structured_content_surfaces(mcp_manager_factory, mcp_stdio_script):
    m = await _make_stdio_manager(mcp_manager_factory, mcp_stdio_script)
    result = await m.call_tool("mock__get_object", {})
    assert "key" in result and "value" in result


@pytest.mark.integration
async def test_stdio_tool_error_surfaces_as_error(
    mcp_manager_factory, mcp_stdio_script
):
    m = await _make_stdio_manager(mcp_manager_factory, mcp_stdio_script)
    result = await m.call_tool("mock__fail", {"message": "boom"})
    assert result.startswith("Error:")
    assert "tool fail" in result


@pytest.mark.integration
async def test_agent_calls_real_mcp_tool_over_wire(
    make_full_agent, mcp_manager_factory, mcp_stdio_script, mock_openai, auto_allow
):
    """Model wire → agent loop → real MCP subprocess: the model server scripts
    one tool call (``mock__add``), the agent executes it against the real
    stdio server, and the result is fed back for the final answer."""
    agent, _ = make_full_agent(workspace=None, offline_mode=False)
    agent.mcp_manager.add_server(
        "mock",
        command=sys.executable,
        args=[str(mcp_stdio_script)],
    )
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                0,
                                tc_id="t1",
                                name="mock__add",
                                args='{"a": 200, "b": 22}',
                            )
                        ]
                    }
                ),
                cc(
                    finish="tool_calls",
                    usage={"prompt_tokens": 4, "completion_tokens": 2},
                ),
            ],
        ),
        (
            "stream",
            [
                cc(content="The sum is "),
                cc(content="222."),
                cc(finish="stop", usage={"prompt_tokens": 6, "completion_tokens": 3}),
            ],
        ),
    )

    from oli_bot.models import Done, Message, StreamChunk

    messages = [Message(role="user", content="add 200 and 22")]
    events = [ev async for ev in agent.process(messages, confirm_callback=auto_allow)]
    finals = [e.full_text for e in events if isinstance(e, Done)]
    assert finals == ["The sum is 222."]
    assert any(
        isinstance(e, StreamChunk) and e.text.startswith("The sum") for e in events
    )
    # The tool round-trip really hit the subprocess: the model's tool call was
    # executed against the real stdio MCP server and the result was relayed
    # back into the conversation as role= tool before the final reply.
    assert mock_openai.calls[0]["messages"]
    assert any(msg.role == "tool" and "222" in msg.content for msg in messages)


@pytest.mark.integration
async def test_http_mcp_server_end_to_end(mcp_manager_factory, mcp_http_script):
    m = mcp_manager_factory(offline_mode=False)
    m.add_server(
        "httpsrv",
        transport="http",
        url=mcp_http_script["url"],
    )
    tools = await m.get_available_tools()
    names = {t["name"] for t in tools}
    assert "httpsrv__ping" in names
    assert await m.call_tool("httpsrv__add", {"a": 1, "b": 2}) == "3"


@pytest.mark.integration
async def test_http_mcp_blocked_by_offline_mode(tmp_path, mcp_http_script):
    from oli_bot.config import AppConfig
    from oli_bot.mcp_client import MCPClientManager

    m = MCPClientManager(
        config=AppConfig(_env_file=None, offline_mode=True),
        offline_mode=True,
        config_path=str(tmp_path / "offline_mcp.json"),
    )
    m.add_server("httpsrv", transport="http", url=mcp_http_script["url"])
    result = await m.call_tool("httpsrv__ping", {})
    assert "offline" in result.lower()
