from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, AsyncIterator, Dict, List, Optional

from ..models import (
    Message,
    ModelResponse,
    TextChunk,
    ThinkingChunk,
    ToolCall,
    ToolCallChunk,
)

from .base import MAX_TOKENS, TEMPERATURE, ModelBackend, StreamEvent
from .messages import _append_image_placeholder_text
from .streaming import _StreamingThinkParser

logger = logging.getLogger(__name__)


class TransformersBackend(ModelBackend):
    """Local inference backend using the HuggingFace transformers library.

    Loads the model lazily on first generation call. Supports GPU and CPU
    via the ``device`` parameter (``"auto"`` selects CUDA if available).
    Tool calling relies on the tokenizer's native chat-template ``tools=``
    support for rendering tool definitions into the prompt, and parses
    ``<tool_call>{...}</tool_call>`` JSON blocks from the output using a
    streaming JSON decoder (robust to nested objects/arrays in arguments).
    """

    _TOOL_CALL_OPEN_RE = re.compile(r"<tool_call>\s*")
    _TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)

    def __init__(
        self,
        model: str = "",
        device: str = "auto",
        dtype: str = "auto",
        is_multi_model: bool = False,
    ):
        self.model = model
        self._is_multi_model = is_multi_model
        self._device = device
        self._dtype = dtype
        self._loaded = False
        self._model = None
        self._tokenizer = None
        self._processor = None
        self._eos_token_ids: Optional[List[int]] = None

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                AutoProcessor,
                AutoModelForMultimodalLM,
            )
            import torch
        except ImportError as e:
            raise ImportError(
                "The 'transformers' and 'torch' packages are required for the "
                "transformers backend. Install them with: pip install transformers torch"
            ) from e

        model_id = self.model
        if not model_id:
            raise ValueError("No model specified for the transformers backend")

        logger.info(
            "Loading model %s (device=%s, dtype=%s)",
            model_id,
            self._device,
            self._dtype,
        )
        # "auto" is passed straight through so from_pretrained uses the
        # checkpoint's native stored dtype instead of us guessing fp16.
        if self._dtype == "auto" or self._dtype == "balanced":
            resolved_dtype = self._dtype
        elif self._dtype == "float16":
            resolved_dtype = torch.float16
        elif self._dtype == "bfloat16":
            resolved_dtype = torch.bfloat16
        elif self._dtype == "float32":
            resolved_dtype = torch.float32
        else:
            raise ValueError(f"Unsupported datatype: {self._dtype}")

        if self._is_multi_model:
            self._processor = AutoProcessor.from_pretrained(model_id)
            self._model = AutoModelForMultimodalLM.from_pretrained(
                model_id,
                device_map=self._device if self._device != "auto" else "auto",
                dtype=resolved_dtype if resolved_dtype != "auto" else None,
            )
            self._tokenizer = getattr(self._processor, "tokenizer", None)
        else:
            self._tokenizer = AutoTokenizer.from_pretrained(model_id)
            self._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                device_map=self._device if self._device != "auto" else "auto",
                dtype=resolved_dtype,
            )

        if self._tokenizer is not None and self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._eos_token_ids = self._resolve_eos_token_ids()

        self._loaded = True
        logger.info("Model %s loaded successfully", model_id)

    def _resolve_eos_token_ids(self) -> List[int]:
        ids = set()
        if self._tokenizer is None:
            return None
        base_eos = self._tokenizer.eos_token_id
        if base_eos is not None:
            if isinstance(base_eos, list):
                ids.update(base_eos)
            else:
                ids.add(base_eos)

        gen_config_eos = getattr(self._model.generation_config, "eos_token_id", None)
        if gen_config_eos is not None:
            if isinstance(gen_config_eos, list):
                ids.update(gen_config_eos)
            else:
                ids.add(gen_config_eos)

        for special in ("<|im_end|>", "<|eot_id|>", "<|end|>"):
            tok_id = self._tokenizer.convert_tokens_to_ids(special)
            if tok_id is not None and tok_id != self._tokenizer.unk_token_id:
                ids.add(tok_id)

        return list(ids) if ids else None

    def _encodable(self) -> Any:
        return self._tokenizer if self._tokenizer is not None else self._processor

    def _format_messages_for_template(
        self, messages: List[Message]
    ) -> List[Dict[str, str]]:
        formatted = []
        has_system = any(m.role == "system" for m in messages)
        if not has_system:
            formatted.append({"role": "system", "content": ""})

        for m in messages:
            content = m.content
            if m.images:
                content = _append_image_placeholder_text(content, m.images)
            if m.role == "system":
                formatted.append({"role": "system", "content": content})
            elif m.role == "user":
                formatted.append({"role": "user", "content": content})
            elif m.role == "assistant":
                if m.tool_calls:
                    parts = [content or ""]
                    for tc in m.tool_calls:
                        func = tc.get("function", tc)
                        parts.append(
                            "<tool_call>\n"
                            + json.dumps(
                                {
                                    "name": func.get("name", ""),
                                    "arguments": func.get("arguments", {}),
                                }
                            )
                            + "\n</tool_call>"
                        )
                    formatted.append(
                        {"role": "assistant", "content": "\n".join(parts).strip()}
                    )
                else:
                    formatted.append({"role": "assistant", "content": content})
            elif m.role == "tool":
                formatted.append(
                    {
                        "role": "user",
                        "content": f"Tool result for {m.name or 'unknown'}:\n{content}",
                    }
                )

        return formatted

    def _parse_tool_calls(self, text: str) -> List[ToolCall]:
        tool_calls = []
        decoder = json.JSONDecoder()
        for m in self._TOOL_CALL_OPEN_RE.finditer(text):
            start = m.end()
            try:
                data, _ = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tool call JSON at pos %d", start)
                continue
            tool_calls.append(
                ToolCall(
                    id=f"call_{len(tool_calls)}",
                    name=data.get("name", ""),
                    description="",
                    parameters=data.get("arguments", {}),
                )
            )
        return tool_calls

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        self._ensure_loaded()
        logger.debug(
            "Generating response with %s: model=%s, messages=%s, tools=%s",
            self.__class__.__name__,
            model or self.model,
            messages,
            tools if tools else [],
        )
        try:
            import torch

            formatted = self._format_messages_for_template(messages)
            encodable = self._encodable()
            encoded = encodable.apply_chat_template(
                formatted,
                tools=tools,
                return_tensors="pt",
                add_generation_prompt=True,
                return_dict=True,
            ).to(self._model.device)

            with torch.no_grad():
                output_ids = self._model.generate(
                    input_ids=encoded["input_ids"],
                    attention_mask=encoded["attention_mask"],
                    max_new_tokens=max_tokens,
                    temperature=temperature if temperature > 0 else None,
                    do_sample=temperature > 0,
                    top_p=1.0 if temperature <= 0 else None,
                    eos_token_id=self._eos_token_ids,
                    pad_token_id=encodable.pad_token_id,
                )

            new_tokens = output_ids[0][encoded["input_ids"].shape[1] :]
            text = encodable.decode(new_tokens, skip_special_tokens=True)

            tool_calls = self._parse_tool_calls(text) if tools else None
            clean_text = self._TOOL_CALL_BLOCK_RE.sub("", text).strip()

            return ModelResponse(
                content=clean_text,
                tool_calls=tool_calls or None,
                finish_reason="stop",
            )
        except Exception as e:
            logger.exception(
                "Error in %s.generate: %s", self.__class__.__name__, str(e)
            )
            return ModelResponse(
                content="", tool_calls=None, finish_reason="error", error=str(e)
            )

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[StreamEvent]:
        self._ensure_loaded()
        try:
            from transformers import TextIteratorStreamer

            formatted = self._format_messages_for_template(messages)
            encodable = self._encodable()
            encoded = encodable.apply_chat_template(
                formatted,
                tools=tools,
                return_tensors="pt",
                add_generation_prompt=True,
                return_dict=True,
            ).to(self._model.device)

            streamer = TextIteratorStreamer(
                encodable, skip_prompt=True, skip_special_tokens=True
            )

            gen_kwargs = {
                "input_ids": encoded["input_ids"],
                "attention_mask": encoded["attention_mask"],
                "max_new_tokens": MAX_TOKENS,
                "temperature": TEMPERATURE if TEMPERATURE > 0 else None,
                "do_sample": TEMPERATURE > 0,
                "top_p": 1.0 if TEMPERATURE <= 0 else None,
                "eos_token_id": self._eos_token_ids,
                "pad_token_id": encodable.pad_token_id,
                "streamer": streamer,
            }

            thread = threading.Thread(target=self._model.generate, kwargs=gen_kwargs)
            thread.start()

            accumulated = ""
            parser = _StreamingThinkParser()
            for text_chunk in streamer:
                accumulated += text_chunk
                for kind, text in parser.feed(text_chunk):
                    if text:
                        yield (
                            ThinkingChunk(text)
                            if kind == "thinking"
                            else TextChunk(text)
                        )

            for kind, text in parser.flush():
                if text:
                    yield ThinkingChunk(text) if kind == "thinking" else TextChunk(text)

            thread.join()

            if tools:
                if accumulated:
                    logger.debug("Transformers raw tool call text: %s", accumulated)
                tool_calls = self._parse_tool_calls(accumulated)
                if tool_calls:
                    yield ToolCallChunk(tool_calls)

        except Exception as e:
            logger.exception(
                "Error in %s.stream_generate: %s", self.__class__.__name__, str(e)
            )
            raise


__all__ = ["TransformersBackend"]
