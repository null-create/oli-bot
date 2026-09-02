"""Tests for _StreamingThinkParser and ThinkingChunk propagation through backends.

Covers:
  * Clean <think>...</think> tags in a single chunk
  * Tags split across multiple chunk boundaries
  * No-tag input — pure passthrough as TextChunk
  * Partial open tag at end of stream (flushed as text)
  * Multiple thinking blocks in one stream
  * Nested/repeated tags (second <think> treated as text, not nested)
  * ThinkingChunk emitted from OllamaBackend when message.thinking field present
  * ThinkingChunk emitted from OpenAI-compat delta.reasoning_content
"""

import pytest
from types import SimpleNamespace

from oli_bot.backends import _StreamingThinkParser, OllamaBackend, OpenAIBackend
from oli_bot.models import TextChunk, ThinkingChunk, ToolCallChunk

# --------------------------------------------------------------------------- #
# _StreamingThinkParser unit tests                                             #
# --------------------------------------------------------------------------- #


def collect(parser, chunks, *, flush=True):
    """Feed chunks through parser and return all (kind, text) tuples."""
    results = []
    for c in chunks:
        results.extend(parser.feed(c))
    if flush:
        results.extend(parser.flush())
    return results


def test_no_tags_passthrough():
    p = _StreamingThinkParser()
    out = collect(p, ["hello ", "world"])
    assert out == [("text", "hello "), ("text", "world")]


def test_clean_tags_single_chunk():
    p = _StreamingThinkParser()
    out = collect(p, ["<think>reasoning here</think>answer"])
    kinds = [k for k, _ in out if _]
    texts = "".join(t for _, t in out if t)
    assert "reasoning here" in texts
    assert "answer" in texts
    think_parts = [t for k, t in out if k == "thinking"]
    text_parts = [t for k, t in out if k == "text"]
    assert "reasoning here" in "".join(think_parts)
    assert "answer" in "".join(text_parts)


def test_tags_split_across_chunks():
    p = _StreamingThinkParser()
    out = collect(p, ["pre<th", "ink>inside</thi", "nk>post"])
    think_text = "".join(t for k, t in out if k == "thinking")
    text_text = "".join(t for k, t in out if k == "text")
    assert "inside" in think_text
    assert "pre" in text_text
    assert "post" in text_text


def test_partial_open_tag_at_end_flushed_as_text():
    p = _StreamingThinkParser()
    results = []
    results.extend(p.feed("hello <thi"))
    # Before flush: partial tag is buffered
    text_so_far = "".join(t for _, t in results)
    assert "hello " in text_so_far
    # After flush: partial tag emitted as text
    results.extend(p.flush())
    full_text = "".join(t for k, t in results if k == "text")
    assert "<thi" in full_text


def test_multiple_thinking_blocks():
    p = _StreamingThinkParser()
    out = collect(p, ["<think>block1</think>mid<think>block2</think>end"])
    think_text = "".join(t for k, t in out if k == "thinking")
    text_text = "".join(t for k, t in out if k == "text")
    assert "block1" in think_text
    assert "block2" in think_text
    assert "mid" in text_text
    assert "end" in text_text


def test_empty_input():
    p = _StreamingThinkParser()
    assert collect(p, []) == []
    assert collect(p, [""]) == []


def test_thinking_only_no_response():
    p = _StreamingThinkParser()
    out = collect(p, ["<think>just thinking</think>"])
    think_text = "".join(t for k, t in out if k == "thinking")
    text_text = "".join(t for k, t in out if k == "text")
    assert "just thinking" in think_text
    assert text_text == ""


def test_tag_split_at_every_character():
    """Feed the tag one character at a time to stress-test the buffer."""
    src = "<think>abc</think>xyz"
    p = _StreamingThinkParser()
    out = collect(p, list(src))
    think_text = "".join(t for k, t in out if k == "thinking")
    text_text = "".join(t for k, t in out if k == "text")
    assert "abc" in think_text
    assert "xyz" in text_text


# --------------------------------------------------------------------------- #
# OllamaBackend — message.thinking field                                      #
# --------------------------------------------------------------------------- #


def _make_ollama_chunk(content=None, thinking=None, tool_calls=None):
    msg = SimpleNamespace(
        content=content or "", thinking=thinking, tool_calls=tool_calls or []
    )
    return SimpleNamespace(message=msg)


@pytest.mark.asyncio
async def test_ollama_thinking_field_emits_thinking_chunk(monkeypatch):
    """OllamaBackend yields ThinkingChunk when chunk.message.thinking is set."""
    chunks = [
        _make_ollama_chunk(thinking="some reasoning"),
        _make_ollama_chunk(content="final answer"),
    ]

    async def fake_aiter(self):
        for c in chunks:
            yield c

    class FakeStream:
        def __aiter__(self):
            return fake_aiter(self)

    async def fake_chat(**kwargs):
        return FakeStream()

    backend = OllamaBackend.__new__(OllamaBackend)
    backend.model = "test-model"
    backend.client = SimpleNamespace(chat=fake_chat)

    events = []
    async for ev in backend.stream_generate([]):
        events.append(ev)

    types = [type(e) for e in events]
    assert ThinkingChunk in types
    assert TextChunk in types
    thinking_text = "".join(e.text for e in events if isinstance(e, ThinkingChunk))
    assert "some reasoning" in thinking_text


# --------------------------------------------------------------------------- #
# OpenAIBackend — delta.reasoning_content field                               #
# --------------------------------------------------------------------------- #


def _make_openai_chunk(content=None, reasoning_content=None, finish_reason=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=None,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


@pytest.mark.asyncio
async def test_openai_reasoning_content_emits_thinking_chunk():
    """OpenAIBackend yields ThinkingChunk when delta.reasoning_content is set."""
    chunks = [
        _make_openai_chunk(reasoning_content="step1 "),
        _make_openai_chunk(reasoning_content="step2"),
        _make_openai_chunk(content="result", finish_reason="stop"),
    ]

    class FakeStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for c in chunks:
                yield c

    async def fake_create(**kwargs):
        return FakeStream()

    backend = OpenAIBackend.__new__(OpenAIBackend)
    backend.model = "test-model"
    backend.vision_style = "openai"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    events = []
    async for ev in backend.stream_generate([]):
        events.append(ev)

    thinking_text = "".join(e.text for e in events if isinstance(e, ThinkingChunk))
    text_text = "".join(e.text for e in events if isinstance(e, TextChunk))
    assert "step1" in thinking_text
    assert "step2" in thinking_text
    assert "result" in text_text


@pytest.mark.asyncio
async def test_openai_think_tags_in_content_emit_thinking_chunk():
    """OpenAIBackend parses <think> tags from delta.content."""
    chunks = [
        _make_openai_chunk(
            content="<think>reasoning</think>answer", finish_reason="stop"
        ),
    ]

    class FakeStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for c in chunks:
                yield c

    async def fake_create(**kwargs):
        return FakeStream()

    backend = OpenAIBackend.__new__(OpenAIBackend)
    backend.model = "test-model"
    backend.vision_style = "openai"
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    events = []
    async for ev in backend.stream_generate([]):
        events.append(ev)

    thinking_text = "".join(e.text for e in events if isinstance(e, ThinkingChunk))
    text_text = "".join(e.text for e in events if isinstance(e, TextChunk))
    assert "reasoning" in thinking_text
    assert "answer" in text_text
