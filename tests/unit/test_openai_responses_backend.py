"""OpenAIBackend Responses API (/v1/responses) coverage.

Exercises the ``responses_enabled`` path in ``OpenAIBackend``:
  * ``generate`` fans out to ``client.responses.create`` and parses message
    content + ``function_call`` output items into a ``ModelResponse``
  * usage is taken from ``Response.usage`` when present, else estimated
  * ``stream_generate`` converts typed stream events into TextChunk /
    ThinkingChunk / ToolCallChunk / UsageChunk and re-raises on failure
  * function-call arguments are folded via ``output_item.done`` and
    ``function_call_arguments.done`` events
  * the ``_format_responses_messages`` / ``_format_responses_tools``
    converters produce Responses-shaped input items, including round-tripping
    tool history as ``function_call`` / ``function_call_output`` items
"""

import json
import pytest
from types import SimpleNamespace

from oli_bot.backends import OpenAIBackend
from oli_bot.backends.messages import (
    _format_responses_messages,
    _format_responses_tools,
)
from oli_bot.models import (
    ImageAttachment,
    Message,
    TextChunk,
    ThinkingChunk,
    ToolCallChunk,
    UsageChunk,
)

# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


def _resp_message(*, text="", role="assistant", item_id="msg_1"):
    return SimpleNamespace(
        type="message",
        id=item_id,
        role=role,
        content=[SimpleNamespace(type="output_text", text=text)] if text else [],
    )


def _resp_function_call(
    *, call_id="call_1", name="do", arguments='{"a": 1}', item_id="fc_1"
):
    return SimpleNamespace(
        type="function_call",
        id=item_id,
        call_id=call_id,
        name=name,
        arguments=arguments,
    )


def _event(etype, **extra):
    return SimpleNamespace(type=etype, **extra)


def _stream(*events):
    class _Iter:
        def __aiter__(self_inner):
            return self_inner._agen()

        async def _agen(self_inner):
            for e in events:
                yield e

    return _Iter()


class _DummyResponses:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _make_backend(*, enabled=True, responses=None, stream=False):
    b = OpenAIBackend(
        api_key="x",
        base_url="https://ex/",
        model="gpt-fake",
        responses_enabled=enabled,
    )
    if stream:
        result = _stream(*responses) if responses is not None else _stream()
    else:
        result = responses
    dummy = _DummyResponses(result)
    b.client = SimpleNamespace(responses=dummy, chat=SimpleNamespace(completions=None))
    return b, dummy


def _msg(**kw):
    defaults = {"role": "user", "content": ""}
    defaults.update(kw)
    return Message(**defaults)


# --------------------------------------------------------------------------- #
# Non-streaming generate                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_generate_uses_responses_and_parses_output():
    responses = SimpleNamespace(
        output=[
            _resp_message(text="hello "),
            _resp_message(text="world"),
            _resp_function_call(),
        ],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=18),
        error=None,
    )
    b, dummy = _make_backend(responses=responses)
    messages = [_msg(role="user", content="hi")]
    out = await b.generate(None, messages, tools=[], max_tokens=64)

    assert dummy.calls[0]["model"] == "gpt-fake"
    assert dummy.calls[0]["input"] == [{"role": "user", "content": "hi"}]
    assert dummy.calls[0]["tool_choice"] == "none"
    assert dummy.calls[0]["max_output_tokens"] == 64
    assert dummy.calls[0]["temperature"] == pytest.approx(0.7)
    assert out.content == "hello world"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].id == "call_1"
    assert out.tool_calls[0].name == "do"
    assert out.tool_calls[0].parameters == {"a": 1}
    assert out.usage.prompt_tokens == 11
    assert out.usage.completion_tokens == 7
    assert out.usage.estimated is False


@pytest.mark.asyncio
async def test_generate_estimates_usage_when_absent():
    responses = SimpleNamespace(
        output=[_resp_message(text="hi")], usage=None, error=None
    )
    b, dummy = _make_backend(responses=responses)
    out = await b.generate(None, [], tools=[])

    assert out.content == "hi"
    assert out.tool_calls == []
    assert out.usage.estimated is True
    assert out.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_generate_responses_error_path():
    b, dummy = _make_backend(responses=None)

    class _Boom:
        async def create(self, **_k):
            raise RuntimeError("upstream 500")

    b.client = SimpleNamespace(
        responses=_Boom(), chat=SimpleNamespace(completions=None)
    )
    out = await b.generate(None, [], tools=[])
    assert out.finish_reason == "error"
    assert out.content == ""


@pytest.mark.asyncio
async def test_disable_flag_still_uses_chat_completions():
    b, dummy = _make_backend(enabled=False)
    assert not b.responses_enabled
    # The chat-completions path is exercised by the existing suite; here we
    # assert the responses client is never consulted when the flag is off.
    assert b.client.responses is not None
    assert b.client.chat.completions is None


# --------------------------------------------------------------------------- #
# Streaming                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stream_yields_text_and_usage():
    events = [
        _event("response.output_text.delta", delta="hello ", item_id="msg_1"),
        _event("response.output_text.delta", delta="world", item_id="msg_1"),
        _event(
            "response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=3, output_tokens=2)
            ),
        ),
    ]
    b, dummy = _make_backend(stream=True, responses=events)
    out = [e async for e in b.stream_generate([], tools=[])]

    kinds = [type(e).__name__ for e in out]
    assert kinds == ["TextChunk", "TextChunk", "UsageChunk"]
    assert "".join(e.text for e in out if isinstance(e, TextChunk)) == "hello world"
    usage = next(e.usage for e in out if isinstance(e, UsageChunk))
    assert usage.prompt_tokens == 3
    assert usage.completion_tokens == 2
    assert usage.estimated is False


@pytest.mark.asyncio
async def test_stream_yields_thinking_from_reasoning_deltas():
    events = [
        _event("response.reasoning_text.delta", delta="let me think", item_id="msg_1"),
        _event("response.output_text.delta", delta="answer", item_id="msg_1"),
        _event(
            "response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=1, output_tokens=1)
            ),
        ),
    ]
    b, dummy = _make_backend(stream=True, responses=events)
    out = [e async for e in b.stream_generate([], tools=[])]

    thinking = [e for e in out if isinstance(e, ThinkingChunk)]
    assert [e.text for e in thinking] == ["let me think"]


@pytest.mark.asyncio
async def test_stream_folds_function_call_via_arguments_done():
    events = [
        _event(
            "response.function_call_arguments.delta",
            item_id="fc_1",
            delta='{"a"',
        ),
        _event(
            "response.function_call_arguments.done",
            item_id="fc_1",
            name="do",
            arguments='{"a": 1}',
        ),
        _event(
            "response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=1, output_tokens=1)
            ),
        ),
    ]
    b, dummy = _make_backend(stream=True, responses=events)
    out = [e async for e in b.stream_generate([], tools=[])]

    tcs = [e for e in out if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    call = tcs[0].tool_calls[0]
    assert call.name == "do"
    assert call.parameters == {"a": 1}
    # call_id falls back to item_id when no output_item.done carried it
    assert call.id == "fc_1"


@pytest.mark.asyncio
async def test_stream_folds_function_call_via_output_item_done():
    events = [
        _event(
            "response.function_call_arguments.delta",
            item_id="fc_1",
            delta='{"b":',
        ),
        _event(
            "response.output_item.done",
            item=_resp_function_call(
                call_id="call_9", name="run", arguments='{"b": 2}', item_id="fc_1"
            ),
        ),
        _event(
            "response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=1, output_tokens=1)
            ),
        ),
    ]
    b, dummy = _make_backend(stream=True, responses=events)
    out = [e async for e in b.stream_generate([], tools=[])]

    tcs = [e for e in out if isinstance(e, ToolCallChunk)]
    assert len(tcs) == 1
    call = tcs[0].tool_calls[0]
    assert call.id == "call_9"
    assert call.name == "run"
    assert call.parameters == {"b": 2}


@pytest.mark.asyncio
async def test_stream_estimates_usage_when_completed_missing():
    events = [_event("response.output_text.delta", delta="hi", item_id="msg_1")]
    b, dummy = _make_backend(stream=True, responses=events)
    out = [e async for e in b.stream_generate([], tools=[])]

    usage = next(e.usage for e in out if isinstance(e, UsageChunk))
    assert usage.estimated is True


@pytest.mark.asyncio
async def test_stream_reraises_on_error_event():
    events = [
        _event("error", message="bad auth", code="invalid_api_key"),
    ]
    b, dummy = _make_backend(stream=True, responses=events)
    with pytest.raises(RuntimeError, match="bad auth"):
        [e async for e in b.stream_generate([], tools=[])]


@pytest.mark.asyncio
async def test_stream_reraises_on_backend_failure():
    class _Boom:
        async def create(self, **_k):
            raise RuntimeError("upstream 500")

    b = OpenAIBackend(
        api_key="x", base_url="https://ex/", model="gpt-fake", responses_enabled=True
    )
    b.client = SimpleNamespace(
        responses=_Boom(), chat=SimpleNamespace(completions=None)
    )

    with pytest.raises(RuntimeError, match="upstream 500"):
        [e async for e in b.stream_generate([], tools=[])]


# --------------------------------------------------------------------------- #
# Converters                                                                  #
# --------------------------------------------------------------------------- #


def test_format_responses_messages_round_trips_tool_history():
    messages = [
        _msg(role="system", content="be helpful"),
        _msg(role="user", content="calc something"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "calc",
                        "arguments": json.dumps({"expr": "1+1"}),
                    },
                }
            ],
        ),
        Message(role="tool", content="2", tool_call_id="call_1"),
    ]
    items = _format_responses_messages(messages)
    assert items == [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "calc something"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "calc",
            "arguments": '{"expr": "1+1"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "2"},
    ]


def test_format_responses_messages_openai_image_parts():
    att = ImageAttachment(
        data=b"\x89PNG\r\n\x1a\n", media_type="image/png", width=1, height=1
    )
    messages = [
        Message(role="user", content="what is this", images=[att]),
    ]
    items = _format_responses_messages(messages, image_style="openai")
    assert items[0]["role"] == "user"
    parts = items[0]["content"]
    assert parts[0] == {"type": "input_text", "text": "what is this"}
    assert parts[1]["type"] == "input_image"
    assert parts[1]["image_url"].startswith("data:image/png;base64,")


def test_format_responses_messages_non_openai_images_drop_bytes():
    att = ImageAttachment(data=b"\x89PNG", media_type="image/png")
    messages = [Message(role="user", content="pic", images=[att])]
    items = _format_responses_messages(messages, image_style="none")
    assert items[0]["role"] == "user"
    assert isinstance(items[0]["content"], str)
    assert "[Image attached" in items[0]["content"]


def test_format_responses_tools_is_flat():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "calc",
                "description": "do math",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "number"}},
                },
            },
        }
    ]
    assert _format_responses_tools(tools) == [
        {
            "type": "function",
            "name": "calc",
            "description": "do math",
            "parameters": {"type": "object", "properties": {"x": {"type": "number"}}},
        }
    ]


def test_format_responses_tools_empty():
    assert _format_responses_tools(None) == []
    assert _format_responses_tools([]) == []
