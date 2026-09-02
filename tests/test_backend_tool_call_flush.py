"""OpenAIBackend.stream_generate accumulation + flush regressions.

Covers the streaming path in `OpenAIBackend`:
  * `tool_calls_acc` accumulator concatenates argument fragments across deltas
  * Flush fires on `tool_calls`, `stop`, and end-of-stream (fallback)
  * No double-flush after a natural finish reason
  * Multiple parallel tool calls (distinct `index` values) round-trip
  * `id` from the first delta is preserved (used later as `tool_call_id`)
  * Text + tool_call ordering is preserved when both arrive in one delta
  * Exceptions propagate — the stream must NOT silently yield empty output

Sibling coverage for Ollama / HuggingFace / Transformers lives in
`test_backend_stream_flush_extra.py`.
"""

import pytest
from types import SimpleNamespace

from oli_bot.backends import OpenAIBackend
from oli_bot.models import TextChunk, ToolCallChunk

# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


class _DummyChatCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        chunks = self._chunks

        class _Iter:
            def __aiter__(self_inner):
                return self_inner._agen()

            async def _agen(self_inner):
                for c in chunks:
                    yield c

        return _Iter()


class _DummyChat:
    def __init__(self, chunks):
        self.completions = _DummyChatCompletions(chunks)


def _tool_delta(*, index=0, tc_id=None, name=None, args=None):
    """Build a single tool-call delta entry (matching the OpenAI SDK shape).

    Real OpenAI streams typically populate ``id`` and ``name`` only on the
    first delta for a given index, then send argument fragments in
    subsequent deltas. Pass ``None`` for fields that aren't present in
    that particular delta.
    """
    return SimpleNamespace(
        index=index,
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


def _make_chunk(*, content=None, tool_deltas=None, finish=None):
    delta = SimpleNamespace(
        content=content,
        tool_calls=list(tool_deltas) if tool_deltas else None,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice])


def _make_backend(chunks):
    b = OpenAIBackend(api_key="x", base_url="https://ex/", model="gpt-fake")
    b.client = SimpleNamespace(chat=_DummyChat(chunks))
    return b


# --------------------------------------------------------------------------- #
# Flush on natural finish reasons                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_flush_on_tool_calls_finish_reason():
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"a": 1}')]),
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    call = tcs[0].tool_calls[0]
    assert call.id == "c1"
    assert call.name == "do"
    assert call.parameters == {"a": 1}


@pytest.mark.asyncio
async def test_flush_on_stop_finish_reason():
    """Regression: providers that end with `stop` but still emitted tool
    calls in earlier deltas must not silently drop them."""
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"a": 1}')]),
        _make_chunk(finish="stop"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    assert tcs[0].tool_calls[0].id == "c1"
    assert tcs[0].tool_calls[0].parameters == {"a": 1}


@pytest.mark.asyncio
async def test_fallback_flush_when_no_finish_reason():
    """Some providers omit `finish_reason` entirely — fallback flush at
    end-of-stream must still emit the accumulated tool call."""
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"b": 2}')]),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    assert tcs[0].tool_calls[0].parameters == {"b": 2}


@pytest.mark.asyncio
async def test_no_double_flush_after_natural_finish_reason():
    """After the `tool_calls`/`stop` branch flushes, the end-of-stream
    fallback must NOT emit a second identical `ToolCallChunk`."""
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"a": 1}')]),
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1


# --------------------------------------------------------------------------- #
# Accumulator behavior                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_argument_fragments_concatenated_across_deltas():
    """OpenAI streams typically split argument JSON across many deltas
    (`{"a"` → `: 1, "b"` → `: "str"}`). The accumulator must concatenate
    fragments in arrival order before json.loads."""
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"a"')]),
        _make_chunk(tool_deltas=[_tool_delta(args=': 1, "b"')]),
        _make_chunk(tool_deltas=[_tool_delta(args=': "str"}')]),
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    call = tcs[0].tool_calls[0]
    assert call.id == "c1"
    assert call.name == "do"
    assert call.parameters == {"a": 1, "b": "str"}


@pytest.mark.asyncio
async def test_id_and_name_from_first_delta_survive_argument_only_deltas():
    """Regression: the accumulator initializes `id` + `name` from the first
    delta for a given index, and later argument-only deltas (with `id=None`
    and `name=None`) must NOT clobber them."""
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"x":')]),
        _make_chunk(tool_deltas=[_tool_delta(args="1}")]),  # no id / name here
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    call = next(e for e in events if isinstance(e, ToolCallChunk)).tool_calls[0]
    assert call.id == "c1"  # was NOT overwritten with None on second delta
    assert call.name == "do"  # ditto
    assert call.parameters == {"x": 1}


@pytest.mark.asyncio
async def test_multiple_parallel_tool_calls_by_index():
    """Two concurrent tool calls arrive with distinct `index` values and
    interleaved argument fragments. Both must land in the same
    `ToolCallChunk` with the correct id/name/args."""
    chunks = [
        _make_chunk(
            tool_deltas=[
                _tool_delta(index=0, tc_id="c1", name="a", args='{"x":'),
                _tool_delta(index=1, tc_id="c2", name="b", args='{"y":'),
            ]
        ),
        _make_chunk(
            tool_deltas=[
                _tool_delta(index=0, args="1}"),
                _tool_delta(index=1, args="2}"),
            ]
        ),
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    tcs = [e for e in events if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    calls = tcs[0].tool_calls
    assert len(calls) == 2
    by_id = {c.id: c for c in calls}
    assert by_id["c1"].name == "a" and by_id["c1"].parameters == {"x": 1}
    assert by_id["c2"].name == "b" and by_id["c2"].parameters == {"y": 2}


@pytest.mark.asyncio
async def test_empty_arguments_string_produces_empty_dict():
    """A tool call with only id+name and no argument fragments must decode
    to `{}` rather than raise from `json.loads("")`."""
    chunks = [
        _make_chunk(tool_deltas=[_tool_delta(tc_id="c1", name="noop")]),
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    call = next(e for e in events if isinstance(e, ToolCallChunk)).tool_calls[0]
    assert call.parameters == {}


# --------------------------------------------------------------------------- #
# Text vs tool_call ordering                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_text_only_response_yields_no_tool_call_chunk():
    chunks = [
        _make_chunk(content="hello "),
        _make_chunk(content="world"),
        _make_chunk(finish="stop"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    kinds = [type(e).__name__ for e in events]
    assert kinds == ["TextChunk", "TextChunk"]
    assert "".join(e.text for e in events if isinstance(e, TextChunk)) == "hello world"


@pytest.mark.asyncio
async def test_text_and_tool_call_in_same_delta_yields_text_first():
    """Regression on event ordering: when a delta contains both text and
    a tool_call, the TextChunk must be yielded before any subsequent
    ToolCallChunk so downstream UIs can flush the reasoning."""
    chunks = [
        _make_chunk(
            content="thinking...",
            tool_deltas=[_tool_delta(tc_id="c1", name="do", args='{"a": 1}')],
        ),
        _make_chunk(finish="tool_calls"),
    ]
    events = [ev async for ev in _make_backend(chunks).stream_generate([], tools=[])]

    kinds = [type(e).__name__ for e in events]
    text_idx = kinds.index("TextChunk")
    tool_idx = kinds.index("ToolCallChunk")
    assert text_idx < tool_idx


# --------------------------------------------------------------------------- #
# Error propagation                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stream_generate_reraises_on_backend_failure():
    """AGENTS.md invariant: `stream_generate` re-raises rather than
    silently swallowing errors or yielding empty output. The caller
    (`Agent._tool_loop`) is what converts the exception into an Error
    event."""

    class _BoomChat:
        async def create(self, **_k):
            raise RuntimeError("upstream 500")

    b = OpenAIBackend(api_key="x", base_url="https://ex/", model="gpt-fake")
    b.client = SimpleNamespace(chat=SimpleNamespace(completions=_BoomChat()))

    with pytest.raises(RuntimeError, match="upstream 500"):
        [ev async for ev in b.stream_generate([], tools=[])]
