from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion as OpenAIResponse

from ..models import (
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
)

from .base import MAX_TOKENS, TEMPERATURE, ModelBackend, StreamEvent
from .messages import _format_messages, _format_tools, _validate_message_content_blocks
from .streaming import _StreamingThinkParser

logger = logging.getLogger(__name__)


class OpenAIBackend(ModelBackend):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        vision_style: str = "openai",
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        # "openai" keeps the standard image_url payload; "bedrock" emits
        # Bedrock-native image blocks
        self.vision_style = vision_style
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def set_base_url(self, url: str) -> None:
        self.base_url = url
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=url)

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
            response: OpenAIResponse = await self.client.chat.completions.create(
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
            return ModelResponse(
                content=response.choices[0].message.content,
                tool_calls=tool_calls,
                finish_reason="stop",
            )
        except Exception as e:
            logger.exception(
                "Error in %s.generate: %s", self.__class__.__name__, str(e)
            )
            return ModelResponse(
                content=(
                    response.choices[0].message.content
                    if "response" in locals() and response.choices
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
            formatted_messages = _format_messages(
                messages, stringify_arguments=True, image_style=self.vision_style
            )
            _validate_message_content_blocks(formatted_messages)

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
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta:
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield ThinkingChunk(reasoning)
                    if delta.content:
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

        except Exception as e:
            logger.exception(
                "Error in %s.stream_generate: %s", self.__class__.__name__, str(e)
            )
            raise


__all__ = ["OpenAIBackend"]
