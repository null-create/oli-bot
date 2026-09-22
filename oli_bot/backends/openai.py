from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion as OpenAIResponse
from openai.types.responses import Response as OpenAIResponsesResponse

from ..models import (
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
    Usage,
    UsageChunk,
)

from ..config import configs

from .base import (
    MAX_TOKENS,
    TEMPERATURE,
    ModelBackend,
    StreamEvent,
    estimate_tokens,
)
from .messages import (
    _format_messages,
    _format_responses_messages,
    _format_responses_tools,
    _format_tools,
    _validate_message_content_blocks,
)
from .streaming import _StreamingThinkParser

logger = logging.getLogger(__name__)

_raw_headers = configs.openai_optional_headers
if isinstance(_raw_headers, str):
    _OPTIONAL_HEADERS = json.loads(_raw_headers)
elif isinstance(_raw_headers, dict):
    _OPTIONAL_HEADERS = _raw_headers
else:
    _OPTIONAL_HEADERS = {}


class OpenAIBackend(ModelBackend):
    # Class-level default so instances built via `__new__` (unit-test stubs
    # that set a bare client mock) still have the flag defined.
    responses_enabled: bool = False

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        vision_style: str = "openai",
        responses_enabled: Optional[bool] = None,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.vision_style = vision_style
        self.responses_enabled = (
            configs.openai_responses_enabled
            if responses_enabled is None
            else responses_enabled
        )
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, default_headers=_OPTIONAL_HEADERS
        )

    def set_base_url(self, url: str) -> None:
        self.base_url = url
        self.client = AsyncOpenAI(
            api_key=self.api_key, base_url=url, default_headers=_OPTIONAL_HEADERS
        )

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        tool_calls: list[ToolCall] = []
        try:
            formatted = _format_messages(
                messages, stringify_arguments=True, image_style=self.vision_style
            )
            _validate_message_content_blocks(formatted)

            logger.debug(
                "Generating response with %s: model=%s, tools=%s, messages=%s",
                self.__class__.__name__,
                model or self.model,
                json.dumps(formatted, indent=2),
                tools if tools else [],
            )
            if self.responses_enabled:
                response: OpenAIResponsesResponse = await self.client.responses.create(
                    model=model if model else self.model,
                    input=_format_responses_messages(
                        messages, image_style=self.vision_style
                    ),
                    tools=_format_responses_tools(tools) if tools else [],
                    tool_choice="auto" if tools else "none",
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                )
                content, tool_calls = self._parse_responses_output(response, tool_calls)
                usage = None
                if response.usage is not None:
                    usage = Usage(
                        prompt_tokens=response.usage.input_tokens or 0,
                        completion_tokens=response.usage.output_tokens or 0,
                    )
                else:
                    usage = Usage(
                        prompt_tokens=estimate_tokens(json.dumps(formatted)),
                        completion_tokens=estimate_tokens(content),
                        estimated=True,
                    )
                return ModelResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason="stop",
                    usage=usage,
                )

            response = await self.client.chat.completions.create(
                model=model if model else self.model,
                messages=formatted,
                max_tokens=max_tokens,
                tools=_format_tools(tools) if tools else [],
                tool_choice="auto" if tools else "none",
                temperature=temperature,
            )

            for item in response.choices:
                if item.message.tool_calls:
                    for call in item.message.tool_calls:
                        tool_calls.append(
                            ToolCall(
                                id=call.id,
                                name=call.function.name,
                                description="",
                                parameters=json.loads(call.function.arguments or "{}"),
                            )
                        )
            usage = None
            if response.usage is not None:
                usage = Usage(
                    prompt_tokens=response.usage.prompt_tokens or 0,
                    completion_tokens=response.usage.completion_tokens or 0,
                )
            else:
                usage = Usage(
                    prompt_tokens=estimate_tokens(json.dumps(formatted)),
                    completion_tokens=estimate_tokens(
                        response.choices[0].message.content or ""
                    ),
                    estimated=True,
                )
            return ModelResponse(
                content=response.choices[0].message.content,
                tool_calls=tool_calls,
                finish_reason="stop",
                usage=usage,
            )
        except Exception as e:
            logger.exception(
                "Error in %s.generate: %s", self.__class__.__name__, str(e)
            )
            return ModelResponse(
                content=(
                    response.choices[0].message.content
                    if "response" in locals()
                    and hasattr(response, "choices")
                    and response.choices
                    else ""
                ),
                tool_calls=tool_calls,
                finish_reason="error",
            )

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[StreamEvent]:
        try:
            if self.responses_enabled:
                async for event in self._responses_stream_generate(messages, tools):
                    yield event
                return

            formatted_messages = _format_messages(
                messages, stringify_arguments=True, image_style=self.vision_style
            )
            _validate_message_content_blocks(formatted_messages)

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=formatted_messages,
                    tools=_format_tools(tools) if tools else [],
                    tool_choice="auto" if tools else "none",
                    stream=True,
                    stream_options={"include_usage": True},
                )
            except Exception as e:
                # Some OpenAI-compatible providers (Bedrock proxies, LM Studio,
                # older vLLM, ...) reject the stream_options param. Retry once
                # without it; usage then falls back to estimation.
                logger.debug(
                    "Provider rejected stream_options, retrying without usage: %s", e
                )
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=formatted_messages,
                    tools=_format_tools(tools) if tools else [],
                    tool_choice="auto" if tools else "none",
                    stream=True,
                )
            tool_calls_acc: Dict[int, dict] = {}
            flushed = False
            parser = _StreamingThinkParser()
            streamed_text = ""
            chunk_usage = None
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if getattr(chunk, "usage", None) is not None:
                    chunk_usage = chunk.usage
                if delta:
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield ThinkingChunk(reasoning)
                    if delta.content:
                        streamed_text += delta.content
                        for kind, text in parser.feed(delta.content):
                            if text:
                                yield (
                                    ThinkingChunk(text)
                                    if kind == "thinking"
                                    else TextChunk(text)
                                )
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.id or f"call_{idx}",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx][
                                        "arguments"
                                    ] += tc.function.arguments
                finish_reason = (
                    chunk.choices[0].finish_reason if chunk.choices else None
                )
                if (
                    finish_reason in ("tool_calls", "stop")
                    and tool_calls_acc
                    and not flushed
                ):
                    for data in tool_calls_acc.values():
                        logger.debug(
                            "OpenAI raw tool call: id=%s name=%s arguments=%s",
                            data["id"],
                            data["name"],
                            data["arguments"],
                        )
                    tool_calls = [
                        ToolCall(
                            id=data["id"],
                            name=data["name"],
                            description="",
                            parameters=json.loads(data["arguments"] or "{}"),
                        )
                        for data in tool_calls_acc.values()
                    ]
                    yield ToolCallChunk(tool_calls)
                    flushed = True

            for kind, text in parser.flush():
                if text:
                    yield ThinkingChunk(text) if kind == "thinking" else TextChunk(text)

            # Fallback flush: some providers omit finish_reason entirely.
            if tool_calls_acc and not flushed:
                for data in tool_calls_acc.values():
                    logger.debug(
                        "OpenAI raw tool call (fallback flush): id=%s name=%s arguments=%s",
                        data["id"],
                        data["name"],
                        data["arguments"],
                    )
                tool_calls = [
                    ToolCall(
                        id=data["id"],
                        name=data["name"],
                        description="",
                        parameters=json.loads(data["arguments"] or "{}"),
                    )
                    for data in tool_calls_acc.values()
                ]
                yield ToolCallChunk(tool_calls)

            if chunk_usage is not None:
                usage = Usage(
                    prompt_tokens=chunk_usage.prompt_tokens or 0,
                    completion_tokens=chunk_usage.completion_tokens or 0,
                )
            else:
                usage = Usage(
                    prompt_tokens=estimate_tokens(json.dumps(formatted_messages)),
                    completion_tokens=estimate_tokens(streamed_text),
                    estimated=True,
                )
            yield UsageChunk(usage)

        except Exception as e:
            logger.exception(
                "Error in %s.stream_generate: %s", self.__class__.__name__, str(e)
            )
            raise

    @staticmethod
    def _parse_responses_output(
        response: OpenAIResponsesResponse,
        tool_calls: Optional[List[ToolCall]] = None,
    ) -> tuple[str, List[ToolCall]]:
        """Extract text content and function-call tool calls from a Responses
        API response's ``output`` items."""
        tool_calls = tool_calls if tool_calls is not None else []
        parts: List[str] = []
        for item in response.output:
            if getattr(item, "type", "") == "message":
                for part in getattr(item, "content", []) or []:
                    text = getattr(part, "text", None)
                    if text:
                        parts.append(text)
            elif getattr(item, "type", "") == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=getattr(item, "call_id", "") or "",
                        name=getattr(item, "name", ""),
                        description="",
                        parameters=json.loads(getattr(item, "arguments", None) or "{}"),
                    )
                )
        return "".join(parts), tool_calls

    async def _responses_stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Responses API streaming variant of ``stream_generate``.

        Iterates the typed ``ResponseStreamEvent`` SSE stream, feeding text
        deltas through the shared think parser (so `` reasoning`` blocks are
        still tagged as thinking) and accumulating ``function_call`` items keyed
        by ``item_id`` until the end of the stream, when a single
        ``ToolCallChunk`` is flushed. Re-raises on failure per the
        ``stream_generate`` contract.
        """
        input_items = _format_responses_messages(
            messages, image_style=self.vision_style
        )
        responses_tools = _format_responses_tools(tools) if tools else []

        try:
            response = await self.client.responses.create(
                model=self.model,
                input=input_items,
                tools=responses_tools,
                tool_choice="auto" if tools else "none",
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception as e:
            logger.debug(
                "Provider rejected responses stream_options, retrying without usage: %s",
                e,
            )
            response = await self.client.responses.create(
                model=self.model,
                input=input_items,
                tools=responses_tools,
                tool_choice="auto" if tools else "none",
                stream=True,
            )

        tool_calls_acc: Dict[str, dict] = {}
        parser = _StreamingThinkParser()
        streamed_text = ""
        usage = None
        async for event in response:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                streamed_text += delta
                for kind, text in parser.feed(delta):
                    if text:
                        yield (
                            ThinkingChunk(text)
                            if kind == "thinking"
                            else TextChunk(text)
                        )
            elif etype in (
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
            ):
                delta = getattr(event, "delta", "") or ""
                if delta:
                    yield ThinkingChunk(delta)
            elif etype == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", "")
                entry = tool_calls_acc.setdefault(
                    item_id, {"name": "", "arguments": ""}
                )
                entry["arguments"] += getattr(event, "delta", "") or ""
            elif etype in (
                "response.function_call_arguments.done",
                "response.output_item.done",
            ):
                self._fold_responses_tool_call(event, tool_calls_acc)
            elif etype == "response.completed":
                completed = getattr(event, "response", None)
                if (
                    completed is not None
                    and getattr(completed, "usage", None) is not None
                ):
                    r_usage = completed.usage
                    usage = Usage(
                        prompt_tokens=getattr(r_usage, "input_tokens", 0) or 0,
                        completion_tokens=getattr(r_usage, "output_tokens", 0) or 0,
                    )
            elif etype in ("error", "response.failed"):
                raise RuntimeError(
                    getattr(event, "message", "") or "responses stream failed"
                )

        for kind, text in parser.flush():
            if text:
                yield ThinkingChunk(text) if kind == "thinking" else TextChunk(text)

        if tool_calls_acc:
            tool_calls = [
                ToolCall(
                    id=data["call_id"],
                    name=data["name"],
                    description="",
                    parameters=json.loads(data["arguments"] or "{}"),
                )
                for data in tool_calls_acc.values()
            ]
            logger.debug(
                "OpenAI responses tool calls: %s",
                [(tc.name, tc.parameters) for tc in tool_calls],
            )
            yield ToolCallChunk(tool_calls)

        if usage is None:
            usage = Usage(
                prompt_tokens=estimate_tokens(json.dumps(input_items)),
                completion_tokens=estimate_tokens(streamed_text),
                estimated=True,
            )
        yield UsageChunk(usage)

    @staticmethod
    def _fold_responses_tool_call(event, tool_calls_acc: Dict[str, dict]) -> None:
        """Fold a completed function-call event/item into the accumulator."""
        item = None
        if getattr(event, "type", "") == "response.output_item.done":
            item = getattr(event, "item", None)
        if item is not None and getattr(item, "type", "") == "function_call":
            item_id = getattr(item, "id", "")
            entry = tool_calls_acc.setdefault(item_id, {"name": "", "arguments": ""})
            entry["call_id"] = getattr(item, "call_id", "") or item_id
            entry["name"] = getattr(item, "name", "") or entry["name"]
            entry["arguments"] = getattr(item, "arguments", "") or entry["arguments"]
        elif getattr(event, "type", "") == "response.function_call_arguments.done":
            item_id = getattr(event, "item_id", "")
            entry = tool_calls_acc.setdefault(item_id, {"name": "", "arguments": ""})
            entry["name"] = getattr(event, "name", "") or entry["name"]
            entry["arguments"] = getattr(event, "arguments", "") or entry["arguments"]
            if "call_id" not in entry:
                entry["call_id"] = item_id


__all__ = ["OpenAIBackend"]
