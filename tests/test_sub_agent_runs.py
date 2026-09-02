"""Tests for live sub-agent run recording (agent.stream_sub_agent_run)."""

import asyncio

from oli_bot.agent import stream_sub_agent_run
from oli_bot.models import (
    AssistantResponse,
    Done,
    Error,
    StreamChunk,
    SubAgentRun,
    ThinkingChunk,
    ToolCallExecuting,
    ToolCallResult,
)


def _make_run() -> SubAgentRun:
    return SubAgentRun(task_id="run-1", agent_name="analyst", task="analyze")


def test_sub_agent_run_defaults():
    run = SubAgentRun(task_id="t", agent_name="a", task="x")
    assert run.status == "running"
    assert run.activity == ""
    assert run.events == []
    assert run.full_text == ""
    assert run.started_at == ""


def test_records_streaming_events_in_order():
    async def events():
        yield StreamChunk("hel")
        yield StreamChunk("lo")
        yield ThinkingChunk("let me think")
        yield ToolCallExecuting(name="grep", parameters={"pattern": "x"})
        yield ToolCallResult(name="grep", result="line 1")
        yield AssistantResponse("hello\n")

    async def scenario():
        run = _make_run()
        text = await stream_sub_agent_run(run, events())
        assert text == ""
        assert [type(e).__name__ for e in run.events] == [
            "StreamChunk",
            "StreamChunk",
            "ThinkingChunk",
            "ToolCallExecuting",
            "ToolCallResult",
            "AssistantResponse",
        ]
        assert run.status == "running"
        assert run.activity == "streaming..."

    asyncio.run(scenario())


def test_records_done_and_full_text():
    async def events():
        yield StreamChunk("final ")
        yield StreamChunk("answer")
        yield Done(full_text="final answer")

    async def scenario():
        run = _make_run()
        text = await stream_sub_agent_run(run, events())
        assert text == "final answer"
        assert run.full_text == "final answer"
        assert run.status == "done"
        assert run.activity == "done"

    asyncio.run(scenario())


def test_records_error_status():
    async def events():
        yield Error("boom")

    async def scenario():
        run = _make_run()
        text = await stream_sub_agent_run(run, events())
        assert run.status == "error"
        assert run.activity == "error: boom"
        assert text == ""

    asyncio.run(scenario())
