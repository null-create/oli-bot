"""Tests for turn-activity capture and persistence.

The browser rebuilds a reopened session from the persisted transcript. The
transcript alone only carries *what was said*, so the agent also records
*what happened* (model thinking, tool-call results and timings, dispatched
sub-agent runs) and the API layer attaches it to the message that closes the
turn. These tests cover the capture, the round-trip through the session file
and the WebSocket frame.
"""

import json

import pytest

from oli_bot.activity import TurnActivity, attach_turn_activity
from oli_bot.agent import Agent
from oli_bot.api.convert import _event_to_frame
from oli_bot.backends.messages import _format_messages
from oli_bot.config import AppConfig
from oli_bot.models import (
    Done,
    Message,
    StreamChunk,
    SubAgentCompleted,
    SubAgentEvent,
    SubAgentProgress,
    SubAgentStarted,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
    ToolCallExecuting,
    ToolCallResult,
)
from oli_bot.sessions import ConversationStore, _message_from_dict, _message_to_dict

# --------------------------------------------------------------------------- #
# Capture                                                                      #
# --------------------------------------------------------------------------- #


def test_activity_is_empty_when_nothing_happened():
    activity = TurnActivity()
    assert activity.to_dict() is None


def test_activity_records_thinking_as_segments():
    activity = TurnActivity()
    activity.add_thinking("first ")
    activity.add_thinking("part")
    activity.close_thinking_segment()
    activity.add_thinking("second segment")

    data = activity.to_dict()
    assert data is not None
    assert data["thinking"] == "first part\n\nsecond segment"


def test_activity_records_tool_calls_with_results_and_timings():
    activity = TurnActivity()
    activity.start_tool_call("call_0", "read_file", {"path": "a.py"})
    activity.finish_tool_call("call_0", "file contents", 0.25)

    data = activity.to_dict()
    assert data is not None
    assert data["tool_calls"] == [
        {
            "id": "call_0",
            "name": "read_file",
            "parameters": {"path": "a.py"},
            "result": "file contents",
            "elapsed": 0.25,
        }
    ]


def test_activity_collects_tool_calls_across_rounds():
    activity = TurnActivity()
    activity.start_tool_call("call_0", "a", {})
    activity.finish_tool_call("call_0", "r0", 0.1)
    activity.start_tool_call("call_0", "b", {})
    activity.finish_tool_call("call_0", "r1", 0.2)

    data = activity.to_dict()
    assert data is not None
    assert [call["name"] for call in data["tool_calls"]] == ["a", "b"]
    assert [call["result"] for call in data["tool_calls"]] == ["r0", "r1"]


def test_activity_folds_sub_agent_events():
    activity = TurnActivity()
    activity.observe(
        SubAgentStarted(
            task_id="run-1",
            agent_name="researcher",
            pool_name="default",
            task="find X",
        )
    )
    activity.observe(
        SubAgentEvent(
            task_id="run-1", agent_name="researcher", event=StreamChunk("par")
        )
    )
    activity.observe(
        SubAgentEvent(
            task_id="run-1", agent_name="researcher", event=StreamChunk("tial")
        )
    )
    activity.observe(
        SubAgentEvent(
            task_id="run-1",
            agent_name="researcher",
            event=ToolCallExecuting(name="read", parameters={"path": "x"}),
        )
    )
    activity.observe(
        SubAgentEvent(
            task_id="run-1",
            agent_name="researcher",
            event=ToolCallResult(name="read", result="contents"),
        )
    )
    activity.observe(
        SubAgentProgress(
            task_id="run-1", agent_name="researcher", activity="done", status="done"
        )
    )
    activity.observe(
        SubAgentCompleted(
            task_id="run-1",
            agent_name="researcher",
            status="done",
            full_text="all done",
            todos=[{"content": "step", "status": "completed", "priority": "high"}],
        )
    )

    data = activity.to_dict()
    assert data is not None
    assert len(data["sub_agents"]) == 1
    run = data["sub_agents"][0]
    assert run["task_id"] == "run-1"
    assert run["agent_name"] == "researcher"
    assert run["pool_name"] == "default"
    assert run["task"] == "find X"
    assert run["status"] == "done"
    assert run["full_text"] == "all done"
    assert run["text"] == "partial"
    assert run["tool_calls"] == [
        {"name": "read", "parameters": {"path": "x"}, "result": "contents"}
    ]
    assert run["todos"] == [
        {"content": "step", "status": "completed", "priority": "high"}
    ]


# --------------------------------------------------------------------------- #
# Placement                                                                    #
# --------------------------------------------------------------------------- #


def test_attach_prefers_the_final_assistant_message():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="calling", tool_calls=[{"id": "c1"}]),
        Message(role="tool", content="ok", tool_call_id="c1"),
        Message(role="assistant", content="done"),
    ]
    assert attach_turn_activity(messages, 1, {"thinking": "hm"}) is True
    assert messages[3].activity == {"thinking": "hm"}
    assert messages[2].activity is None


def test_attach_falls_back_to_the_last_tool_message():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="calling", tool_calls=[{"id": "c1"}]),
        Message(role="tool", content="ok", tool_call_id="c1"),
    ]
    assert attach_turn_activity(messages, 1, {"thinking": "hm"}) is True
    assert messages[2].activity == {"thinking": "hm"}


def test_attach_does_not_cross_the_turn_boundary():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="old"),
    ]
    assert attach_turn_activity(messages, 2, {"thinking": "hm"}) is False
    assert messages[1].activity is None


def test_attach_ignores_empty_activity():
    messages = [Message(role="assistant", content="x")]
    assert attach_turn_activity(messages, 0, None) is False
    assert attach_turn_activity(messages, 0, {}) is False
    assert messages[0].activity is None


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #


def test_activity_survives_the_message_round_trip():
    message = Message(
        role="assistant",
        content="done",
        activity={"thinking": "hm", "elapsed": 1.5, "tool_calls": []},
    )
    restored = _message_from_dict(json.loads(json.dumps(_message_to_dict(message))))
    assert restored.activity == message.activity


def test_activity_is_omitted_when_absent():
    assert "activity" not in _message_to_dict(Message(role="user", content="hi"))


def test_session_file_persists_activity_and_todos(tmp_path):
    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "")
    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="done",
            activity={"thinking": "hm", "elapsed": 2.0, "tool_calls": []},
        ),
    ]
    todos = [{"content": "step", "status": "completed", "priority": "low"}]
    store.save_session("srv", sid, messages, "m", "p", todos=todos)

    data = store.load_session("srv", sid)
    assert data is not None
    assert data["messages"][1]["activity"]["thinking"] == "hm"
    assert data["todos"] == todos


def test_activity_never_reaches_the_model_payload():
    messages = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="done",
            activity={"thinking": "secret reasoning", "tool_calls": []},
        ),
    ]
    formatted = _format_messages(messages)
    assert all("activity" not in msg for msg in formatted)
    assert "secret reasoning" not in json.dumps(formatted)


def test_done_frame_carries_the_activity():
    frame = _event_to_frame(Done(full_text="hi", activity={"thinking": "hm"}))
    assert frame["type"] == "done"
    assert frame["data"]["activity"] == {"thinking": "hm"}


def test_done_frame_without_activity_is_null():
    frame = _event_to_frame(Done(full_text="hi"))
    assert frame["data"]["activity"] is None


def test_sub_agent_completed_frame_carries_the_todos():
    frame = _event_to_frame(
        SubAgentCompleted(
            task_id="run-1",
            agent_name="researcher",
            status="done",
            full_text="all done",
            todos=[{"content": "step", "status": "completed", "priority": "high"}],
        )
    )
    assert frame["type"] == "sub_agent_completed"
    assert frame["data"]["todos"] == [
        {"content": "step", "status": "completed", "priority": "high"}
    ]


# --------------------------------------------------------------------------- #
# Agent turn capture                                                          #
# --------------------------------------------------------------------------- #


class _ToolStub:
    """Backend that requests one tool call, then answers."""

    model = "stub"

    def __init__(self):
        self._calls = 0

    async def stream_generate(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            yield ThinkingChunk("let me check")
            yield TextChunk("checking")
            yield ToolCallChunk(
                [ToolCall(id="c1", name="read_file", parameters={"path": "a.py"})]
            )
        else:
            yield TextChunk("all good")


class _StubMCP:
    async def call_tool(self, name, params, confirm_callback=None, **kwargs):
        return "file contents"


def _agent(backend):
    return Agent(
        role="default",
        backend=backend,
        mcp_manager=_StubMCP(),
        profile_name="default",
        mode="agent",
        config=AppConfig(_env_file=None, max_tool_iterations=3),
    )


async def _run(agent, messages):
    return [event async for event in agent.process(messages)]


@pytest.mark.asyncio
async def test_tool_turn_done_carries_the_activity():
    agent = _agent(_ToolStub())
    messages = [Message(role="user", content="hi")]
    events = await _run(agent, messages)

    done = [e for e in events if isinstance(e, Done)][-1]
    assert done.activity is not None
    assert done.activity["thinking"] == "let me check"
    assert done.activity["elapsed"] >= 0
    assert len(done.activity["tool_calls"]) == 1
    call = done.activity["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["name"] == "read_file"
    assert call["parameters"] == {"path": "a.py"}
    assert call["result"] == "file contents"
    assert call["elapsed"] >= 0


@pytest.mark.asyncio
async def test_text_only_turn_has_no_activity():
    class _TextOnly:
        model = "stub"

        async def stream_generate(self, messages, tools=None):
            yield TextChunk("hello")

    agent = _agent(_TextOnly())
    events = await _run(agent, [Message(role="user", content="hi")])
    done = [e for e in events if isinstance(e, Done)][-1]
    assert done.activity is None
