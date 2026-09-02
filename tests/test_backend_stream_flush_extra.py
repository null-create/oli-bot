"""Stream-flush regressions for OllamaBackend, HuggingFaceBackend, and
TransformersBackend.

The OpenAI equivalent lives in `test_backend_tool_call_flush.py`. AGENTS.md
claims *all* streaming backends flush tool calls on the natural finish
reasons plus a fallback at end-of-stream; this file verifies that for the
other three concrete backends.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oli_bot.backends import HuggingFaceBackend, OllamaBackend, TransformersBackend
from oli_bot.models import TextChunk, ToolCallChunk

# ---------- helpers ----------------------------------------------------------


class _AsyncChunkIter:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for c in self._chunks:
            yield c


# =============================================================================
# OllamaBackend
# =============================================================================


class _OllamaAsyncClient:
    def __init__(self, chunks):
        self._chunks = chunks

    async def chat(self, **_kwargs):
        return _AsyncChunkIter(self._chunks)


def _ollama_chunk(*, content=None, tool_calls=None):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls)
    )


def _ollama_tool(name, args):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))


def _make_ollama(chunks):
    b = OllamaBackend.__new__(OllamaBackend)
    b.model = "ollama-fake"
    b.base_url = "http://x"
    b.client = _OllamaAsyncClient(chunks)
    return b


@pytest.mark.asyncio
async def test_ollama_yields_text_chunks_in_order():
    chunks = [
        _ollama_chunk(content="hello "),
        _ollama_chunk(content="world"),
    ]
    b = _make_ollama(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    texts = [e.text for e in events if isinstance(e, TextChunk)]
    assert texts == ["hello ", "world"]
    assert not any(isinstance(e, ToolCallChunk) for e in events)


@pytest.mark.asyncio
async def test_ollama_flushes_tool_call_from_delta():
    """Ollama's streaming shape emits tool_calls inline on the chunk that
    contains them, so a chunk with tool_calls must produce a ToolCallChunk
    with the decoded arguments."""
    chunks = [
        _ollama_chunk(tool_calls=[_ollama_tool("do", {"a": 1, "b": [2, 3]})]),
    ]
    b = _make_ollama(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    call = tcs[0].tool_calls[0]
    assert call.name == "do"
    assert call.parameters == {"a": 1, "b": [2, 3]}


@pytest.mark.asyncio
async def test_ollama_yields_text_then_tool_call_in_the_same_chunk_order():
    """When a chunk carries both text and a tool_call, both events must be
    yielded and the text must precede the tool call."""
    chunks = [
        _ollama_chunk(
            content="reasoning...",
            tool_calls=[_ollama_tool("do", {"x": 1})],
        ),
    ]
    b = _make_ollama(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["TextChunk", "ToolCallChunk"]


@pytest.mark.asyncio
async def test_ollama_stream_reraises_on_backend_failure():
    class _BoomClient:
        async def chat(self, **_k):
            raise RuntimeError("upstream down")

    b = OllamaBackend.__new__(OllamaBackend)
    b.model = "ollama-fake"
    b.base_url = "http://x"
    b.client = _BoomClient()
    with pytest.raises(RuntimeError, match="upstream down"):
        [ev async for ev in b.stream_generate([], tools=[])]


# =============================================================================
# HuggingFaceBackend  (OpenAI-compatible streaming shape)
# =============================================================================


class _HFCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **_kwargs):
        return _AsyncChunkIter(self._chunks)


class _HFChat:
    def __init__(self, chunks):
        self.completions = _HFCompletions(chunks)


def _hf_chunk(*, content=None, tc_id=None, tc_name=None, tc_args=None, finish=None):
    tc = None
    if tc_id or tc_name or tc_args:
        tc = SimpleNamespace(
            index=0,
            id=tc_id,
            function=SimpleNamespace(name=tc_name, arguments=tc_args),
        )
    delta = SimpleNamespace(content=content, tool_calls=[tc] if tc else None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice])


def _make_hf(chunks):
    b = HuggingFaceBackend.__new__(HuggingFaceBackend)
    b.model = "hf-fake"
    b.remote = False
    b.client = SimpleNamespace(chat=_HFChat(chunks))
    return b


@pytest.mark.asyncio
async def test_hf_flush_on_tool_calls_finish_reason():
    chunks = [
        _hf_chunk(tc_id="c1", tc_name="do", tc_args='{"a": 1}'),
        _hf_chunk(finish="tool_calls"),
    ]
    b = _make_hf(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    assert tcs[0].tool_calls[0].name == "do"
    assert tcs[0].tool_calls[0].parameters == {"a": 1}


@pytest.mark.asyncio
async def test_hf_flush_on_stop_finish_reason():
    chunks = [
        _hf_chunk(tc_id="c1", tc_name="do", tc_args='{"a": 1}'),
        _hf_chunk(finish="stop"),
    ]
    b = _make_hf(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    assert tcs[0].tool_calls[0].parameters == {"a": 1}


@pytest.mark.asyncio
async def test_hf_fallback_flush_when_no_finish_reason():
    chunks = [_hf_chunk(tc_id="c1", tc_name="do", tc_args='{"b": 2}')]
    b = _make_hf(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    assert tcs[0].tool_calls[0].parameters == {"b": 2}


@pytest.mark.asyncio
async def test_hf_does_not_double_flush():
    """Regression: after flushing on `tool_calls`, the fallback flush at
    end-of-stream must NOT emit a second identical ToolCallChunk."""
    chunks = [
        _hf_chunk(tc_id="c1", tc_name="do", tc_args='{"a": 1}'),
        _hf_chunk(finish="tool_calls"),
    ]
    b = _make_hf(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1


@pytest.mark.asyncio
async def test_hf_argument_fragments_are_concatenated_across_chunks():
    """Streamed argument JSON typically arrives split across many deltas.
    The accumulator must concatenate fragments before json.loads."""
    chunks = [
        _hf_chunk(tc_id="c1", tc_name="do", tc_args='{"a": '),
        _hf_chunk(tc_args='1, "b": '),
        _hf_chunk(tc_args='"str"}'),
        _hf_chunk(finish="tool_calls"),
    ]
    b = _make_hf(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]
    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert tcs[0].tool_calls[0].parameters == {"a": 1, "b": "str"}


# =============================================================================
# TransformersBackend
# =============================================================================
# TransformersBackend is heavyweight (torch + HF), so we test the tool-call
# parser directly rather than driving `stream_generate` end-to-end. The
# parser is the piece that would silently drop tool calls if it regressed.


def _make_transformers() -> TransformersBackend:
    b = TransformersBackend.__new__(TransformersBackend)
    b.model = "fake"
    b._device = "cpu"
    b._dtype = "auto"
    b._loaded = False
    b._model = None
    b._tokenizer = None
    b._eos_token_ids = None
    return b


def test_transformers_parses_single_tool_call():
    b = _make_transformers()
    text = 'thinking... <tool_call>{"name": "do", "arguments": {"a": 1}}</tool_call>'
    calls = b._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "do"
    assert calls[0].parameters == {"a": 1}


def test_transformers_parses_multiple_tool_calls():
    b = _make_transformers()
    text = (
        '<tool_call>{"name": "a", "arguments": {}}</tool_call>\n'
        '<tool_call>{"name": "b", "arguments": {"x": 2}}</tool_call>'
    )
    calls = b._parse_tool_calls(text)
    assert [c.name for c in calls] == ["a", "b"]
    assert calls[0].parameters == {}
    assert calls[1].parameters == {"x": 2}


def test_transformers_preserves_nested_objects_in_arguments():
    """Regression: naive `\\{.*?\\}` regex parsers truncate at the first
    inner `}`. The JSON raw_decode path must preserve nested structures."""
    b = _make_transformers()
    args = {"filters": {"tags": ["a", "b"], "meta": {"k": "v"}}}
    payload = json.dumps({"name": "search", "arguments": args})
    text = f"<tool_call>{payload}</tool_call>"
    calls = b._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].parameters == args


def test_transformers_skips_malformed_tool_call_block():
    """A malformed JSON block must be logged & skipped, not raise."""
    b = _make_transformers()
    text = (
        '<tool_call>{"name": "good", "arguments": {}}</tool_call>'
        "<tool_call>NOT-JSON</tool_call>"
    )
    calls = b._parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "good"


def test_transformers_returns_empty_list_when_no_markers():
    b = _make_transformers()
    assert b._parse_tool_calls("no markers here") == []
