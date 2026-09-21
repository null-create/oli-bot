"""OllamaBackend over the real HTTP/NDJSON wire.

The unit suite injects fake in-memory clients. These tests point a real
``ollama.AsyncClient`` at a real uvicorn server and assert the full decode
path: streamed text chunks, inline tool calls, usage counts from the
``done`` chunk, and error propagation from non-200 / malformed transports.
"""

from __future__ import annotations

import pytest

from oli_bot.models import TextChunk, ToolCallChunk, UsageChunk

from .conftest import ollama_part, ollama_tool


def _texts(events):
    return "".join(e.text for e in events if isinstance(e, TextChunk))


async def _run(backend, messages=None, tools=None):
    return [ev async for ev in backend.stream_generate(messages or [], tools=tools)]


@pytest.mark.integration
async def test_streams_text_over_real_wire(ollama_backend, mock_ollama):
    mock_ollama.script(
        (
            "ollama",
            [
                ollama_part(content="Hello "),
                ollama_part(content="world"),
                ollama_part(done=True, counts={"prompt_eval_count": 9, "eval_count": 4}),
            ],
        )
    )
    events = await _run(ollama_backend)
    assert _texts(events) == "Hello world"
    usage = events[-1].usage
    assert isinstance(events[-1], UsageChunk)
    assert (usage.prompt_tokens, usage.completion_tokens) == (9, 4)
    assert not usage.estimated
    assert [c["model"] for c in mock_ollama.calls] == ["mocked"]
    assert mock_ollama.calls[0]["stream"] is True


@pytest.mark.integration
async def test_tool_calls_round_trip_over_wire(ollama_backend, mock_ollama):
    mock_ollama.script(
        (
            "ollama",
            [
                ollama_part(
                    tool_calls=[ollama_tool("run_command", {"command": "pwd"})]
                ),
                ollama_part(done=True, counts={"prompt_eval_count": 3, "eval_count": 2}),
            ],
        )
    )
    events = await _run(ollama_backend)
    tcs = [tc for e in events if isinstance(e, ToolCallChunk) for tc in e.tool_calls]
    assert len(tcs) == 1
    assert tcs[0].name == "run_command"
    assert tcs[0].parameters == {"command": "pwd"}


@pytest.mark.integration
async def test_thinking_markers_stream_as_thinking_chunks(ollama_backend, mock_ollama):
    from oli_bot.models import ThinkingChunk

    mock_ollama.script(
        (
            "ollama",
            [
                ollama_part(content="hello "),
                ollama_part(content="<think>secret reasoning</think>"),
                ollama_part(content=" world"),
                ollama_part(done=True),
            ],
        )
    )
    events = await _run(ollama_backend)
    thinks = [e.text for e in events if isinstance(e, ThinkingChunk)]
    assert any("secret reasoning" in t for t in thinks)
    assert _texts(events) == "hello  world"


@pytest.mark.integration
async def test_http_500_propagates_from_wire(ollama_backend, mock_ollama):
    mock_ollama.script(("status", 500, "internal"))
    with pytest.raises(Exception) as exc:
        await _run(ollama_backend)
    assert "500" in str(exc.value)


@pytest.mark.integration
async def test_malformed_ndjson_line_propagates(ollama_backend, mock_ollama):
    mock_ollama.script(("ollama", ["not-json"]))
    with pytest.raises(Exception):
        await _run(ollama_backend)


@pytest.mark.integration
async def test_usage_falls_back_to_estimate_when_counts_absent(
    ollama_backend, mock_ollama
):
    mock_ollama.script(("ollama", [ollama_part(content="x"), ollama_part(done=True)]))
    events = await _run(ollama_backend)
    assert events[-1].usage.estimated is True