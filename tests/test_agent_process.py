"""Tool-loop / final-stream split + non-mutation of caller messages.

Covers `Agent.process()` end-to-end: compose_messages, tool_loop event
ordering, sync_back, chat mode, max-iterations fallback, and the timeout /
exception / MCP-failure error branches.
"""

import asyncio
import json

import pytest

from oli_bot.agent import Agent
from oli_bot.config import AppConfig
from oli_bot.models import (
    AssistantResponse,
    Done,
    Error,
    Message,
    StreamChunk,
    TextChunk,
    ToolCall,
    ToolCallChunk,
    ToolCallExecuting,
    ToolCallResult,
)

# --------------------------------------------------------------------------- #
# Stubs                                                                       #
# --------------------------------------------------------------------------- #


class _TextStub:
    """Backend that yields plain text and no tool calls."""

    model = "stub"

    async def stream_generate(self, messages, tools=None):
        yield TextChunk("hello ")
        yield TextChunk("world")


class _ToolThenTextStub:
    """First call yields a tool call, second call yields a final answer."""

    def __init__(self, text_before_tool: str = ""):
        self.model = "stub"
        self._calls = 0
        self._text_before_tool = text_before_tool

    async def stream_generate(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            if self._text_before_tool:
                yield TextChunk(self._text_before_tool)
            yield ToolCallChunk(
                [ToolCall(id="c1", name="builtin__echo", parameters={"x": 1})]
            )
        else:
            yield TextChunk("done")


class _MultiToolStub:
    """Yields two tool calls in one chunk, then a final answer."""

    def __init__(self):
        self.model = "stub"
        self._calls = 0

    async def stream_generate(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            yield ToolCallChunk(
                [
                    ToolCall(id="c1", name="builtin__a", parameters={"x": 1}),
                    ToolCall(id="c2", name="builtin__b", parameters={"y": 2}),
                ]
            )
        else:
            yield TextChunk("final")


class _HangingStub:
    """Never yields anything — triggers the tool-loop timeout branch."""

    model = "stub"

    async def stream_generate(self, messages, tools=None):
        await asyncio.sleep(10)
        yield TextChunk("never")


class _RaisingStub:
    """Raises immediately from stream_generate."""

    model = "stub"

    def __init__(self, exc):
        self._exc = exc

    async def stream_generate(self, messages, tools=None):
        raise self._exc
        yield  # pragma: no cover — makes this an async generator


class _StubMCP:
    """Records every call_tool invocation and returns a scripted result."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, params, confirm_callback=None):
        self.calls.append((name, params))
        return f"result-of-{name}"


class _RaisingMCP:
    async def call_tool(self, name, params, confirm_callback=None):
        raise RuntimeError("mcp exploded")


async def _run(agent, msgs):
    events = []
    async for ev in agent.process(msgs):
        events.append(ev)
    return events


def _agent(backend, **kwargs) -> Agent:
    return Agent(
        role="default",
        backend=backend,
        mcp_manager=kwargs.pop("mcp_manager", _StubMCP()),
        profile_name="default",
        mode=kwargs.pop("mode", "agent"),
        config=kwargs.pop("config", AppConfig(_env_file=None, max_tool_iterations=3)),
    )


# --------------------------------------------------------------------------- #
# Text-only path                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_text_only_response_yields_stream_then_done_with_full_text():
    a = _agent(_TextStub())
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    events = await _run(a, msgs)

    stream_events = [e for e in events if isinstance(e, StreamChunk)]
    dones = [e for e in events if isinstance(e, Done)]

    # Every StreamChunk fires before the terminal Done, no duplicates.
    assert [type(e).__name__ for e in events] == [
        "StreamChunk",
        "StreamChunk",
        "Done",
    ]
    assert "".join(e.text for e in stream_events) == "hello world"
    assert len(dones) == 1
    assert dones[0].full_text == "hello world"


@pytest.mark.asyncio
async def test_generating_flag_flips_back_to_false_after_process():
    a = _agent(_TextStub())
    assert a.generating is False
    await _run(a, [Message(role="user", content="hi")])
    assert a.generating is False


# --------------------------------------------------------------------------- #
# compose_messages / sync_back                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_does_not_mutate_or_grow_original_when_text_only():
    a = _agent(_TextStub())
    original_system = "sysprompt-immutable"
    msgs = [
        Message(role="system", content=original_system),
        Message(role="user", content="hi"),
    ]
    original_len = len(msgs)
    await _run(a, msgs)

    # Caller's leading system message content is untouched (no ephemeral
    # date header leaks back into the caller's list).
    assert msgs[0].content == original_system
    # Text-only response must NOT append anything to the caller's list.
    assert len(msgs) == original_len


@pytest.mark.asyncio
async def test_compose_prepends_system_when_caller_has_none_and_sync_back_skips_it():
    """Regression on the `_sync_back` offset branch: if the caller passed no
    system message, `_compose_messages` inserts one internally, and
    `_sync_back` must NOT persist that ephemeral system into the caller's
    list."""
    a = _agent(_ToolThenTextStub())
    msgs = [Message(role="user", content="hi")]
    await _run(a, msgs)

    # Original user message intact at head, no ephemeral system prepended.
    assert msgs[0].role == "user"
    assert msgs[0].content == "hi"
    # Caller's list must not have grown a system message.
    assert not any(m.role == "system" for m in msgs)
    # But assistant + tool turns must be appended.
    roles_after = [m.role for m in msgs[1:]]
    assert "assistant" in roles_after
    assert "tool" in roles_after


# --------------------------------------------------------------------------- #
# Tool loop                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_loop_event_ordering_and_synced_messages():
    mcp = _StubMCP()
    a = _agent(_ToolThenTextStub(), mcp_manager=mcp)
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    events = await _run(a, msgs)

    # Exact ordering: tool executes → tool result → final text stream → done.
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["ToolCallExecuting", "ToolCallResult", "StreamChunk", "Done"]

    executing = next(e for e in events if isinstance(e, ToolCallExecuting))
    result = next(e for e in events if isinstance(e, ToolCallResult))
    done = next(e for e in events if isinstance(e, Done))

    assert executing.name == "builtin__echo"
    assert executing.parameters == {"x": 1}
    assert result.name == "builtin__echo"
    assert result.result == "result-of-builtin__echo"
    assert done.full_text == "done"

    # MCP was invoked exactly once, with the tool's parameters.
    assert mcp.calls == [("builtin__echo", {"x": 1})]

    # Caller's list order: system, user, assistant (with tool_calls), tool.
    assert [m.role for m in msgs] == ["system", "user", "assistant", "tool"]

    assistant = msgs[2]
    assert assistant.tool_calls is not None and len(assistant.tool_calls) == 1
    tc = assistant.tool_calls[0]
    assert tc["id"] == "c1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "builtin__echo"
    # Arguments are JSON-encoded (some backends require string args).
    assert json.loads(tc["function"]["arguments"]) == {"x": 1}

    tool_msg = msgs[3]
    assert tool_msg.tool_call_id == "c1"
    assert tool_msg.content == "result-of-builtin__echo"


@pytest.mark.asyncio
async def test_sync_back_precedes_consumer_appending_final_assistant_on_done():
    """Regression: chat.py's _generate_response appends a final assistant
    Message to the caller's list the instant it observes a Done event. If
    `_sync_back` ran after that yield (and recomputed its offset from
    `len(original)` at that later point), the tool-call/tool-result pair
    would be dropped, leaving an orphaned tool message with no matching
    tool_use — the exact shape that breaks strict validators (Anthropic/
    Bedrock)."""
    mcp = _StubMCP()
    a = _agent(_ToolThenTextStub(), mcp_manager=mcp)
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]

    async for event in a.process(msgs):
        if isinstance(event, Done):
            # Mirror chat.py: append the final assistant reply as soon as
            # Done is observed, before the generator resumes.
            msgs.append(Message(role="assistant", content=event.full_text))

    assert [m.role for m in msgs] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    tool_msg = msgs[3]
    assert tool_msg.tool_call_id == "c1"
    final_assistant = msgs[4]
    assert final_assistant.content == "done"


@pytest.mark.asyncio
async def test_text_before_tool_call_yields_assistant_response_event():
    """When the model streams text alongside a tool call, an AssistantResponse
    event carrying that text must be yielded before the tool is executed so
    downstream renderers can flush the reasoning trace."""
    a = _agent(_ToolThenTextStub(text_before_tool="thinking about it "))
    msgs = [Message(role="user", content="hi")]
    events = await _run(a, msgs)

    kinds = [type(e).__name__ for e in events]
    assert "AssistantResponse" in kinds
    assert kinds.index("AssistantResponse") < kinds.index("ToolCallExecuting")

    resp = next(e for e in events if isinstance(e, AssistantResponse))
    assert resp.content == "thinking about it "


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_chunk_are_all_executed_in_order():
    mcp = _StubMCP()
    a = _agent(_MultiToolStub(), mcp_manager=mcp)
    msgs = [Message(role="user", content="hi")]
    events = await _run(a, msgs)

    executing = [e for e in events if isinstance(e, ToolCallExecuting)]
    results = [e for e in events if isinstance(e, ToolCallResult)]
    assert [e.name for e in executing] == ["builtin__a", "builtin__b"]
    assert [e.name for e in results] == ["builtin__a", "builtin__b"]

    # MCP invoked once per tool, in declared order.
    assert mcp.calls == [
        ("builtin__a", {"x": 1}),
        ("builtin__b", {"y": 2}),
    ]

    # Both tool messages appended with matching tool_call_ids.
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["c1", "c2"]


@pytest.mark.asyncio
async def test_mcp_call_tool_exception_is_captured_into_tool_message():
    a = _agent(_ToolThenTextStub(), mcp_manager=_RaisingMCP())
    msgs = [Message(role="user", content="hi")]
    events = await _run(a, msgs)

    result_event = next(e for e in events if isinstance(e, ToolCallResult))
    assert result_event.result.startswith("Error:")
    assert "mcp exploded" in result_event.result

    # And that error becomes the tool message content so the model can see it
    # on the next tool-loop iteration.
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert "mcp exploded" in tool_msg.content


# --------------------------------------------------------------------------- #
# max_tool_iterations fallback                                                #
# --------------------------------------------------------------------------- #


class _AlwaysToolThenFinalStub:
    """Yields a tool call whenever the loop is active, and plain text on
    the fallback ``_final_stream`` call. Records every ``tools`` value it
    receives so tests can assert on the loop-vs-fallback split.

    The stub switches behavior on call count rather than the ``tools``
    kwarg because ``_final_stream`` now forwards the same tools list so
    Bedrock-style backends accept toolUse history without a 400.
    """

    model = "stub"

    def __init__(self):
        self.tools_seen: list = []

    async def stream_generate(self, messages, tools=None):
        self.tools_seen.append(tools)
        if len(self.tools_seen) <= 2:
            yield ToolCallChunk(
                [ToolCall(id="c1", name="builtin__loop", parameters={})]
            )
        else:
            yield TextChunk("final answer after exhausting iterations")


@pytest.mark.asyncio
async def test_max_iterations_exhausted_falls_back_to_final_stream():
    backend = _AlwaysToolThenFinalStub()
    a = _agent(
        backend,
        mcp_manager=_StubMCP(),
        config=AppConfig(_env_file=None, max_tool_iterations=2),
    )
    msgs = [Message(role="user", content="hi")]
    tools = [{"name": "loop"}]
    events = []
    async for ev in a.process(msgs, tools=tools):
        events.append(ev)

    # All three calls (2 loop iters + 1 final_stream) forward the same tools
    # list so Bedrock proxies keep toolConfig in scope for the toolUse history.
    assert len(backend.tools_seen) == 3
    assert all(t == tools for t in backend.tools_seen)

    # Exactly one Done, carrying the fallback text.
    dones = [e for e in events if isinstance(e, Done)]
    assert len(dones) == 1
    assert dones[0].full_text == "final answer after exhausting iterations"


# --------------------------------------------------------------------------- #
# Chat mode                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_mode_skips_tool_loop_entirely():
    mcp = _StubMCP()
    a = _agent(_TextStub(), mode="chat", mcp_manager=mcp)
    msgs = [Message(role="user", content="hi")]
    events = await _run(a, msgs)

    # Ordered, no duplicates: two stream chunks then exactly one Done.
    assert [type(e).__name__ for e in events] == [
        "StreamChunk",
        "StreamChunk",
        "Done",
    ]
    # Chat mode must NOT invoke MCP at all — the whole point of the mode.
    assert mcp.calls == []
    # Done carries the streamed text.
    done = events[-1]
    assert isinstance(done, Done)
    assert done.full_text == "hello world"


@pytest.mark.asyncio
async def test_chat_mode_ignores_tool_call_chunks_from_backend():
    """Even if a backend erroneously yields a ToolCallChunk while in chat
    mode, `_final_stream` iterates only TextChunks and must not invoke MCP."""
    mcp = _StubMCP()

    class _RogueBackend:
        model = "stub"

        async def stream_generate(self, messages, tools=None):
            yield TextChunk("part ")
            yield ToolCallChunk([ToolCall(id="x", name="builtin__nope", parameters={})])
            yield TextChunk("done")

    a = _agent(_RogueBackend(), mode="chat", mcp_manager=mcp)
    events = await _run(a, [Message(role="user", content="hi")])

    assert mcp.calls == []
    done = next(e for e in events if isinstance(e, Done))
    # _final_stream only accumulates TextChunks into full_text.
    assert done.full_text == "part done"


# --------------------------------------------------------------------------- #
# Plan mode                                                                   #
# --------------------------------------------------------------------------- #


def test_set_mode_accepts_plan():
    a = _agent(_TextStub())
    a.set_mode("plan")
    assert a.mode == "plan"


def test_set_mode_rejects_invalid_mode():
    a = _agent(_TextStub())
    with pytest.raises(ValueError):
        a.set_mode("bogus")


@pytest.mark.asyncio
async def test_plan_mode_runs_tool_loop_not_bypassed_like_chat():
    mcp = _StubMCP()
    a = _agent(_ToolThenTextStub(), mode="plan", mcp_manager=mcp)
    msgs = [Message(role="user", content="plan something")]
    events = await _run(a, msgs)

    # Unlike chat mode, plan mode must invoke the tool loop (MCP gets called).
    assert mcp.calls == [("builtin__echo", {"x": 1})]
    done = next(e for e in events if isinstance(e, Done))
    assert done.full_text == "done"


@pytest.mark.asyncio
async def test_plan_mode_injects_ephemeral_plan_note():
    from oli_bot.agent import PLAN_MODE_NOTE

    a = _agent(_TextStub(), mode="plan")
    msgs = [Message(role="user", content="plan something")]
    composed = a._compose_messages(msgs)

    assert composed[0].role == "system"
    assert PLAN_MODE_NOTE in composed[0].content
    # Ephemeral header must not leak back into the caller's original list.
    assert msgs[0].role == "user"


# --------------------------------------------------------------------------- #
# Error / timeout branches                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_backend_exception_yields_error_then_done():
    a = _agent(_RaisingStub(RuntimeError("upstream 500")))
    events = await _run(a, [Message(role="user", content="hi")])

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["Error", "Done"]

    err, done = events
    assert isinstance(err, Error)
    assert isinstance(done, Done)
    assert "upstream 500" in err.message
    # Done.full_text must be empty on error so the UI layer's "skip empty
    # assistant append" branch fires and the error text never poisons history.
    assert done.full_text == ""


@pytest.mark.asyncio
async def test_backend_exception_does_not_persist_error_as_assistant_message():
    """Regression: backend 400s / timeouts must not leave an assistant message
    containing ``"Error: ..."`` in the caller's history \u2014 that poisons the
    next turn's request under strict Anthropic/Bedrock validators (see
    [logs/backend.ndjson](logs/backend.ndjson))."""
    a = _agent(_RaisingStub(RuntimeError("Error code: 400 - toolConfig missing")))
    messages: list[Message] = [Message(role="user", content="hi")]
    await _run(a, messages)

    assert [m.role for m in messages] == ["user"]
    assert not any("Error code: 400" in (m.content or "") for m in messages)


@pytest.mark.asyncio
async def test_backend_no_tool_support_error_produces_actionable_message():
    """The 'does not support tools' branch swaps the raw exception for a
    friendlier message that mentions the model name and `/mode chat`."""
    a = _agent(_RaisingStub(Exception("model X does not support tools")))
    events = await _run(a, [Message(role="user", content="hi")])

    err = next(e for e in events if isinstance(e, Error))
    assert "does not support tool calling" in err.message
    assert "/mode chat" in err.message
    assert "stub" in err.message  # model name from backend.model


@pytest.mark.asyncio
async def test_tool_loop_timeout_yields_error_then_done_and_flips_generating_flag():
    a = _agent(
        _HangingStub(),
        config=AppConfig(_env_file=None, max_tool_iterations=3, stream_timeout=0.05),
    )
    events = await _run(a, [Message(role="user", content="hi")])

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["Error", "Done"]
    err = events[0]
    assert isinstance(err, Error)
    assert "timed out" in err.message

    # Regression: the `finally` in process() must reset _generating even on
    # a timeout path.
    assert a.generating is False


# --------------------------------------------------------------------------- #
# sanitize_tool_history                                                       #
# --------------------------------------------------------------------------- #


def test_sanitize_drops_orphan_assistant_tool_calls():
    from oli_bot.agent import sanitize_tool_history

    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "tooluse_A",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        ),
        # No matching tool result — this pair must be dropped.
        Message(role="user", content="continue"),
    ]
    out = sanitize_tool_history(msgs)
    assert [m.role for m in out] == ["user", "user"]
    assert out[1].content == "continue"


def test_sanitize_drops_orphan_tool_result():
    from oli_bot.agent import sanitize_tool_history

    msgs = [
        Message(role="user", content="hi"),
        Message(role="tool", content="stale", tool_call_id="tooluse_gone"),
        Message(role="user", content="continue"),
    ]
    out = sanitize_tool_history(msgs)
    assert [m.role for m in out] == ["user", "user"]


def test_sanitize_preserves_matched_tool_pair():
    from oli_bot.agent import sanitize_tool_history

    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "tooluse_A",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        ),
        Message(role="tool", content="result", tool_call_id="tooluse_A"),
        Message(role="assistant", content="done"),
    ]
    out = sanitize_tool_history(msgs)
    assert [m.role for m in out] == ["user", "assistant", "tool", "assistant"]


def test_sanitize_drops_partial_multi_tool_pair():
    """If an assistant issues two tool_calls but only one has a matching
    result, the whole pair is dropped (all-or-nothing) so we never send a
    half-answered tool_use to a strict validator."""
    from oli_bot.agent import sanitize_tool_history

    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "tooluse_A",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                },
                {
                    "id": "tooluse_B",
                    "type": "function",
                    "function": {"name": "y", "arguments": "{}"},
                },
            ],
        ),
        Message(role="tool", content="a", tool_call_id="tooluse_A"),
        # tooluse_B result missing.
        Message(role="user", content="continue"),
    ]
    out = sanitize_tool_history(msgs)
    assert [m.role for m in out] == ["user", "user"]


@pytest.mark.asyncio
async def test_compose_messages_sanitizes_before_send():
    a = _agent(_TextStub())
    msgs = [
        Message(role="user", content="hi"),
        Message(role="tool", content="stale", tool_call_id="tooluse_gone"),
    ]
    composed = a._compose_messages(msgs)
    # System header at [0], the orphan tool must not survive.
    assert composed[0].role == "system"
    assert all(m.role != "tool" for m in composed)
    # Caller's list must be untouched.
    assert msgs[1].role == "tool"


class _AttachingMCP:
    """MCP stub that emits one ImageAttachment after each tool call."""

    def __init__(self, caption: str = ""):
        from oli_bot.models import ImageAttachment

        self._att = ImageAttachment(
            data=b"fake-png-bytes",
            media_type="image/png",
            source_description="/tmp/x.png",
            width=10,
            height=10,
        )
        self._caption = caption
        self._drained = False

    async def call_tool(self, name, params, confirm_callback=None):
        return f"loaded {name}"

    def drain_builtin_attachments(self):
        if self._drained:
            return ([], "")
        self._drained = True
        return ([self._att], self._caption)


@pytest.mark.asyncio
async def test_view_image_attachments_produce_synthetic_user_message():
    a = _agent(_ToolThenTextStub(), mcp_manager=_AttachingMCP(caption="what is this?"))
    msgs = [Message(role="user", content="hi")]
    await _run(a, msgs)

    roles = [m.role for m in msgs]
    # user, assistant(tool_calls), tool, user(image), assistant(final)
    assert roles.index("tool") < roles.index("user", roles.index("tool"))
    synthetic = [m for m in msgs if m.role == "user" and m.images]
    assert len(synthetic) == 1
    assert synthetic[0].content == "what is this?"
    assert synthetic[0].images[0].media_type == "image/png"


@pytest.mark.asyncio
async def test_no_synthetic_user_message_when_no_attachments():
    a = _agent(_ToolThenTextStub(), mcp_manager=_StubMCP())
    msgs = [Message(role="user", content="hi")]
    await _run(a, msgs)
    synthetic = [m for m in msgs if m.role == "user" and m.images]
    assert synthetic == []


@pytest.mark.asyncio
async def test_attachments_land_after_all_tool_results_in_multi_tool_round():
    """Sanitizer requires assistant(tool_calls) -> tool -> tool ... contiguous.
    The synthetic user(image) message must land AFTER the last tool result,
    not between them."""
    from oli_bot.agent import sanitize_tool_history

    a = _agent(_MultiToolStub(), mcp_manager=_AttachingMCP())
    msgs = [Message(role="user", content="hi")]
    await _run(a, msgs)

    # Sequence must be: user, assistant(tool_calls), tool, tool, user(image), assistant
    idx_assistant = next(
        i for i, m in enumerate(msgs) if m.role == "assistant" and m.tool_calls
    )
    tool_msgs_after = [m for m in msgs[idx_assistant + 1 :] if m.role == "tool"]
    assert len(tool_msgs_after) == 2
    # No user(image) message may appear between the two tool msgs
    between = [
        m for m in msgs[idx_assistant + 1 : idx_assistant + 3] if m.role == "user"
    ]
    assert between == []

    # And sanitize_tool_history must accept the resulting history unchanged
    # in terms of the assistant->tool grouping.
    healed = sanitize_tool_history(msgs)
    assert any(m.role == "assistant" and m.tool_calls for m in healed)
    assert sum(1 for m in healed if m.role == "tool") == 2
