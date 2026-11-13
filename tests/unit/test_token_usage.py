"""Token-usage plumbing: backend UsageChunk emission, agent UsageEvent
aggregation, and session persistence of the running total."""

import pytest
from types import SimpleNamespace

from oli_bot.agent import Agent
from oli_bot.config import AppConfig
from oli_bot.models import (
    Done,
    Message,
    TextChunk,
    ThinkingChunk,
    TokenCount,
    ToolCall,
    ToolCallChunk,
    Usage,
    UsageChunk,
    UsageEvent,
)
from oli_bot.sessions import ConversationStore
from oli_bot.tokens import (
    ContextTokenTracker,
    count_message_tokens,
    count_text_tokens,
    estimate_tokens,
)
from oli_bot.backends import OllamaBackend, OpenAIBackend

# --------------------------------------------------------------------------- #
# Local token counting                                                         #
# --------------------------------------------------------------------------- #


class _FakeEncoding:
    def encode(self, text, disallowed_special=()):
        return list(range(len(text.split())))


def test_count_text_tokens_uses_tokenizer(monkeypatch):
    monkeypatch.setattr(
        "oli_bot.tokens._resolve_encoding", lambda model: _FakeEncoding()
    )
    count, estimated = count_text_tokens("hello world", "gpt-fake")
    assert count == 2
    assert estimated is False


def test_count_text_tokens_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setattr("oli_bot.tokens._resolve_encoding", lambda model: None)
    count, estimated = count_text_tokens("hello world", "unknown-model")
    assert count == estimate_tokens("hello world")
    assert estimated is True


def test_count_message_tokens_includes_framing(monkeypatch):
    monkeypatch.setattr("oli_bot.tokens._resolve_encoding", lambda model: None)
    messages = [Message(role="user", content="hello")]
    count, estimated = count_message_tokens(messages, "unknown-model")
    assert count == estimate_tokens("user\nhello") + 4
    assert estimated is True


def test_context_tracker_tracks_history_and_inflight(monkeypatch):
    monkeypatch.setattr("oli_bot.tokens._resolve_encoding", lambda model: None)
    tracker = ContextTokenTracker([Message(role="user", content="hi")], "unknown-model")
    prompt, completion, total, estimated = tracker.snapshot()
    assert prompt > 0
    assert completion == 0
    assert total == prompt
    assert estimated is True

    tracker.add_message(Message(role="assistant", content="hello"))
    assert tracker.snapshot()[0] > prompt

    tracker.add_inflight("thinking about it")
    prompt2, completion2, total2, _ = tracker.snapshot()
    assert completion2 > 0
    assert total2 == prompt2 + completion2

    tracker.set_exact(100, 20, False)
    assert tracker.snapshot() == (100, 20, 120, False)

    tracker.clear_inflight()
    assert tracker.snapshot() == (100, 0, 100, False)


def test_context_tracker_keeps_local_prompt_when_provider_reports_zero(monkeypatch):
    monkeypatch.setattr("oli_bot.tokens._resolve_encoding", lambda model: None)
    tracker = ContextTokenTracker([Message(role="user", content="hi")], "unknown-model")
    prompt, _, _, _ = tracker.snapshot()

    tracker.set_exact(0, 5, False)

    assert tracker.snapshot() == (prompt, 5, prompt + 5, True)


# --------------------------------------------------------------------------- #
# OpenAI streaming                                                             #
# --------------------------------------------------------------------------- #


class _AsyncIter:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for c in self._chunks:
            yield c


class _DummyCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **_kwargs):
        return _AsyncIter(self._chunks)


def _openai_chunk(*, content=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_openai(chunks, *, create=None):
    b = OpenAIBackend.__new__(OpenAIBackend)
    b.model = "gpt-fake"
    b.vision_style = "openai"
    if create is None:
        create = _DummyCompletions(chunks).create
    b.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return b


@pytest.mark.asyncio
async def test_openai_stream_exact_usage_from_final_chunk():
    chunks = [
        _openai_chunk(content="hi ", finish=None),
        _openai_chunk(content="there", finish="stop"),
        _openai_chunk(
            finish=None,
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=9),
        ),
    ]
    b = _make_openai(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]

    usage_chunks = [e for e in events if isinstance(e, UsageChunk)]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage
    assert u.prompt_tokens == 12
    assert u.completion_tokens == 9
    assert u.total_tokens == 21
    assert u.estimated is False


@pytest.mark.asyncio
async def test_openai_stream_estimates_when_no_usage_reported():
    chunks = [
        _openai_chunk(content="hello", finish="stop"),
    ]
    b = _make_openai(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]

    usage_chunks = [e for e in events if isinstance(e, UsageChunk)]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage
    assert u.estimated is True
    assert u.prompt_tokens >= 1
    assert u.completion_tokens >= 1


@pytest.mark.asyncio
async def test_openai_retries_without_stream_options_when_rejected():
    calls = []
    calls_history = []

    async def flaky_create(**kwargs):
        calls.append(1)
        calls_history.append(kwargs)
        if kwargs.get("stream_options") is not None:
            raise RuntimeError("unknown parameter: stream_options")
        return _AsyncIter([_openai_chunk(content="ok", finish="stop")])

    b = _make_openai([], create=flaky_create)
    events = [ev async for ev in b.stream_generate([], tools=[])]

    assert len(calls) == 2
    assert "stream_options" not in calls_history[1]
    texts = [e.text for e in events if isinstance(e, TextChunk)]
    assert "".join(texts) == "ok"
    assert any(isinstance(e, UsageChunk) for e in events)


# --------------------------------------------------------------------------- #
# Ollama streaming                                                             #
# --------------------------------------------------------------------------- #


class _OllamaAsyncClient:
    def __init__(self, chunks):
        self._chunks = chunks

    async def chat(self, **_kwargs):
        return _AsyncIter(self._chunks)


def _ollama_chunk(*, content=None, prompt_eval=None, eval_count=None):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=None, thinking=None),
        prompt_eval_count=prompt_eval,
        eval_count=eval_count,
    )


def _make_ollama(chunks):
    b = OllamaBackend.__new__(OllamaBackend)
    b.model = "ollama-fake"
    b.base_url = "http://x"
    b.client = _OllamaAsyncClient(chunks)
    return b


@pytest.mark.asyncio
async def test_ollama_stream_exact_usage_from_eval_counts():
    chunks = [
        _ollama_chunk(content="hello", prompt_eval=10, eval_count=3),
        _ollama_chunk(content=" world", prompt_eval=10, eval_count=5),
    ]
    b = _make_ollama(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]

    usage_chunks = [e for e in events if isinstance(e, UsageChunk)]
    assert len(usage_chunks) == 1
    u = usage_chunks[0].usage
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 5
    assert u.estimated is False


@pytest.mark.asyncio
async def test_ollama_stream_estimates_when_counts_absent():
    chunks = [_ollama_chunk(content="hi", prompt_eval=None, eval_count=None)]
    b = _make_ollama(chunks)
    events = [ev async for ev in b.stream_generate([], tools=[])]

    usage_chunks = [e for e in events if isinstance(e, UsageChunk)]
    assert len(usage_chunks) == 1
    assert usage_chunks[0].usage.estimated is True


# --------------------------------------------------------------------------- #
# Agent aggregation                                                            #
# --------------------------------------------------------------------------- #


class _UsageToolStub:
    """First call: tool call + usage. Second call: final text + usage."""

    model = "stub"

    def __init__(self):
        self._calls = 0

    async def stream_generate(self, messages, tools=None, max_tokens=None):
        self._calls += 1
        if self._calls == 1:
            yield ToolCallChunk(
                [ToolCall(id="c1", name="builtin__echo", parameters={"x": 1})]
            )
            yield UsageChunk(Usage(prompt_tokens=5, completion_tokens=3))
        else:
            yield TextChunk("done")
            yield UsageChunk(Usage(prompt_tokens=10, completion_tokens=7))


class _StubMCP:
    async def call_tool(self, name, params, confirm_callback=None, **kwargs):
        return "ok"


class _NoUsageStub:
    model = "stub"

    async def stream_generate(self, messages, tools=None, max_tokens=None):
        yield TextChunk("plain text")


class _ThinkingStub:
    model = "stub"

    async def stream_generate(self, messages, tools=None, max_tokens=None):
        yield ThinkingChunk("hmm")
        yield TextChunk("done")
        yield UsageChunk(Usage(prompt_tokens=4, completion_tokens=2))


def _agent(backend) -> Agent:
    return Agent(
        role="default",
        backend=backend,
        mcp_manager=_StubMCP(),
        profile_name="default",
        mode="agent",
        config=AppConfig(_env_file=None, max_tool_iterations=3),
    )


@pytest.mark.asyncio
async def test_agent_emits_single_usage_event_summed_across_tool_iterations():
    a = _agent(_UsageToolStub())
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    events = [ev async for ev in a.process(msgs)]

    usage_events = [e for e in events if isinstance(e, UsageEvent)]
    assert len(usage_events) == 1
    u = usage_events[0].usage
    assert u.prompt_tokens == 15
    assert u.completion_tokens == 10
    assert u.estimated is False

    # UsageChunk is consumed internally, never forwarded to the UI.
    assert not any(isinstance(e, UsageChunk) for e in events)
    assert any(isinstance(e, Done) for e in events)

    token_events = [e for e in events if isinstance(e, TokenCount)]
    phases = [e.phase for e in token_events]
    assert phases[0] == "prompt"
    assert "round" in phases
    assert "tool_result" in phases
    assert phases[-1] == "final"


@pytest.mark.asyncio
async def test_agent_emits_no_usage_event_when_backend_reports_none():
    a = _agent(_NoUsageStub())
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    events = [ev async for ev in a.process(msgs)]

    assert not any(isinstance(e, UsageEvent) for e in events)
    assert any(isinstance(e, Done) for e in events)

    token_events = [e for e in events if isinstance(e, TokenCount)]
    assert token_events[0].phase == "prompt"
    assert token_events[-1].phase == "final"


@pytest.mark.asyncio
async def test_agent_emits_token_count_when_thinking_finishes():
    a = _agent(_ThinkingStub())
    msgs = [Message(role="system", content="sys"), Message(role="user", content="hi")]
    events = [ev async for ev in a.process(msgs)]

    token_events = [e for e in events if isinstance(e, TokenCount)]
    phases = [e.phase for e in token_events]
    assert phases[0] == "prompt"
    assert "thinking" in phases
    assert "round" in phases
    assert phases[-1] == "final"


# --------------------------------------------------------------------------- #
# Session persistence                                                          #
# --------------------------------------------------------------------------- #


def test_session_round_trips_token_totals(tmp_path):
    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "system")
    store.save_session(
        "srv",
        sid,
        [Message(role="user", content="hi")],
        "m",
        "p",
        total_tokens=1234,
        tokens_estimated=True,
    )
    data = store.load_session("srv", sid)
    assert data["total_tokens"] == 1234
    assert data["total_tokens_estimated"] is True


def test_new_session_defaults_to_zero_tokens(tmp_path):
    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "")
    data = store.load_session("srv", sid)
    assert data["total_tokens"] == 0
    assert data["total_tokens_estimated"] is False
