"""OpenAIBackend over the real HTTP/SSE wire.

The unit suite drives the OpenAI SDK with in-memory fake client objects. These
tests instead point a real ``AsyncOpenAI`` client (httpx) at a real uvicorn
server and assert the full decode path: chunk parsing, tool-call fragment
accumulation, finish-reason flushing, usage extraction, ``stream_options``
fallback, and error propagation on malformed/erroring transports.
"""

from __future__ import annotations

import pytest

from oli_bot.models import TextChunk, ThinkingChunk, ToolCallChunk, UsageChunk

from .conftest import cc, tool_delta


def _texts(events):
    return "".join(e.text for e in events if isinstance(e, TextChunk))


def _tool_calls(events):
    return [tc for e in events if isinstance(e, ToolCallChunk) for tc in e.tool_calls]


async def _run(backend, messages=None, tools=None):
    return [ev async for ev in backend.stream_generate(messages or [], tools=tools)]


# --------------------------------------------------------------------------- #
# Text streaming + usage                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_streams_text_over_real_wire(openai_backend, mock_openai):
    mock_openai.script(
        (
            "stream",
            [
                cc(content="Hello "),
                cc(content="world"),
                cc(finish="stop", usage={"prompt_tokens": 12, "completion_tokens": 7}),
            ],
        )
    )
    events = await _run(openai_backend)
    assert _texts(events) == "Hello world"
    kinds = [type(e).__name__ for e in events]
    assert kinds[-1] == "UsageChunk"
    usage = events[-1].usage
    assert (usage.prompt_tokens, usage.completion_tokens) == (12, 7)
    assert not usage.estimated
    assert mock_openai.calls[0]["model"] == "mocked"
    assert mock_openai.calls[0]["stream"] is True


@pytest.mark.integration
async def test_reasoning_content_becomes_thinking_chunk(openai_backend, mock_openai):
    mock_openai.script(
        (
            "stream",
            [
                cc(delta={"reasoning_content": "let me think"}, content="answer"),
                cc(finish="stop"),
            ],
        )
    )
    events = await _run(openai_backend)
    thinks = [e.text for e in events if isinstance(e, ThinkingChunk)]
    assert any("let me think" == t for t in thinks)
    assert _texts(events) == "answer"


# --------------------------------------------------------------------------- #
# Tool calls                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_tool_call_fragments_round_trip_over_wire(openai_backend, mock_openai):
    """Argument JSON split across SSE deltas must be reassembled server-side."""
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(
                                0, tc_id="c1", name="run_command", args='{"command"'
                            ),
                        ]
                    }
                ),
                cc(delta={"tool_calls": [tool_delta(0, args=': "ls"}')]}),
                cc(finish="tool_calls"),
            ],
        )
    )
    calls = _tool_calls(await _run(openai_backend))
    assert len(calls) == 1
    assert calls[0].id == "c1"
    assert calls[0].name == "run_command"
    assert calls[0].parameters == {"command": "ls"}


@pytest.mark.integration
async def test_parallel_tool_calls_by_index_over_wire(openai_backend, mock_openai):
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(0, tc_id="c1", name="a", args='{"x":'),
                            tool_delta(1, tc_id="c2", name="b", args='{"y":'),
                        ]
                    }
                ),
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(0, args="1}"),
                            tool_delta(1, args="2}"),
                        ]
                    }
                ),
                cc(finish="tool_calls"),
            ],
        )
    )
    calls = _tool_calls(await _run(openai_backend))
    assert len(calls) == 2
    by_id = {c.id: c for c in calls}
    assert by_id["c1"].parameters == {"x": 1}
    assert by_id["c2"].parameters == {"y": 2}


@pytest.mark.integration
async def test_flush_on_stop_finish_reason_over_wire(openai_backend, mock_openai):
    """>= Some providers finish with ``stop`` while still carrying tool deltas."""
    mock_openai.script(
        (
            "stream",
            [
                cc(
                    delta={
                        "tool_calls": [
                            tool_delta(0, tc_id="c1", name="do", args='{"a": 1}')
                        ]
                    }
                ),
                cc(finish="stop"),
            ],
        )
    )
    calls = _tool_calls(await _run(openai_backend))
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# stream_options fallback + error paths                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_retries_without_stream_options_when_rejected(
    openai_backend, mock_openai
):
    """Bedrock proxies / older vLLM reject ``stream_options``; the backend
    retries once without it and usage falls back to estimation."""

    def handler(body):
        if "stream_options" in body:
            return ("status", 400, '{"error": {"message": "bad stream_options"}}')
        return (
            "stream",
            [cc(content="retried ok"), cc(finish="stop")],
        )

    mock_openai.handler = handler
    events = await _run(openai_backend)
    assert _texts(events) == "retried ok"
    assert len(mock_openai.calls) == 2
    assert "stream_options" not in mock_openai.calls[1]
    usage = events[-1].usage
    assert usage.estimated is True


@pytest.mark.integration
async def test_http_500_propagates_from_wire(openai_backend, mock_openai):
    mock_openai.handler = lambda body: (
        "status",
        500,
        '{"error": {"message": "upstream 500"}}',
    )
    with pytest.raises(Exception) as exc:
        await _run(openai_backend)
    message = str(exc.value)
    assert "500" in message or getattr(exc.value, "status_code", None) == 500


@pytest.mark.integration
async def test_malformed_sse_data_propagates(openai_backend, mock_openai):
    mock_openai.script(("stream", ["data: {not-json", "data: [DONE]"]))
    with pytest.raises(Exception):
        await _run(openai_backend)


# --------------------------------------------------------------------------- #
# /v1/models                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.integration
async def test_list_models_over_wire(mock_openai):
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key="x", base_url=f"{mock_openai.base_url}/v1")
    models = await client.models.list()
    ids = [m.id for m in models.data]
    assert "mocked" in ids
