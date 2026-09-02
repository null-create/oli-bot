from __future__ import annotations

import base64 as _b64
import json
import logging
from typing import Any, Dict, List

from ..models import ImageAttachment, Message

logger = logging.getLogger(__name__)


def _format_messages(
    messages: List[Message],
    stringify_arguments: bool = False,
    image_style: str = "none",
) -> List[Dict[str, Any]]:
    """Serialize Messages for a chat-completions API.

    ``stringify_arguments`` controls the shape of
    ``tool_calls[].function.arguments``: Ollama's chat API accepts a dict
    (the default), while OpenAI-compatible APIs (including strict Bedrock
    proxies) require a JSON-encoded string. Pass ``True`` from any
    backend that speaks the OpenAI wire format.

    ``image_style`` controls how ``Message.images`` attachments are rendered:
      - ``"none"`` (default): drop image bytes and append a bracketed text
        note to ``content`` so text-only backends know an image was intended.
      - ``"ollama"``: populate the native ``images: [base64]`` field.
      - ``"openai"``: rewrite user-role ``content`` into a parts array with
        ``image_url`` data-URI entries.
      - ``"bedrock"``: rewrite user-role ``content`` into a parts array with
        Bedrock-native ``{"image": {"format", "source": {"bytes"}}}`` blocks,
        for OpenAI-compatible proxies that pass content through to Bedrock.
    """
    formatted = []
    for m in messages:
        content = m.content
        images = m.images or None
        if images and image_style == "none":
            content = _append_image_placeholder_text(content, images)

        msg: Dict[str, Any] = {"role": m.role, "content": content}

        if images and image_style == "ollama":
            msg["images"] = [_b64.b64encode(att.data).decode("ascii") for att in images]
        elif images and image_style == "openai" and m.role == "user":
            parts: List[Dict[str, Any]] = []
            if content and content.strip():
                parts.append({"type": "text", "text": content.strip()})
            for att in images:
                if not att.data:
                    logger.warning(
                        "Skipping image attachment with empty data in message"
                    )
                    continue
                b64 = _b64.b64encode(att.data).decode("ascii")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{att.media_type};base64,{b64}"},
                    }
                )
            if parts:
                msg["content"] = parts
            elif content:
                msg["content"] = content
            else:
                msg["content"] = ""
        elif images and image_style == "bedrock" and m.role == "user":
            parts = _bedrock_content_parts(content, images)
            if parts:
                msg["content"] = parts
            elif content:
                msg["content"] = content
            else:
                msg["content"] = ""

        if m.tool_calls:
            tool_calls = []
            for tc in m.tool_calls:
                tc = dict(tc)
                func = dict(tc.get("function", {}))
                args = func.get("arguments")
                if stringify_arguments:
                    if isinstance(args, (dict, list)):
                        func["arguments"] = json.dumps(args)
                    elif args is None:
                        func["arguments"] = "{}"
                else:
                    if isinstance(args, str):
                        try:
                            func["arguments"] = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            func["arguments"] = {}
                tc["function"] = func
                tool_calls.append(tc)
            msg["tool_calls"] = tool_calls
        if m.name:
            msg["name"] = m.name
        if m.tool_call_id:
            msg["tool_call_id"] = m.tool_call_id
        formatted.append(msg)
    return formatted


def _validate_message_content_blocks(messages: List[Dict[str, Any]]) -> None:
    """Validate that all content blocks in messages have required structure.

    Accepts both OpenAI-style blocks (``{"type": "text"|"image_url", ...}``)
    and Bedrock-native blocks (``{"text": ...}`` / ``{"image": {...}}``).
    Raises ValueError if any content block is malformed. This catches issues
    early before sending to the API.
    """
    for msg_idx, msg in enumerate(messages):
        content = msg.get("content")
        if not content:
            continue

        if isinstance(content, list):
            if not content:
                raise ValueError(
                    f"Message {msg_idx} has empty content array; must have at least one valid block"
                )
            for part_idx, part in enumerate(content):
                if not isinstance(part, dict):
                    raise ValueError(
                        f"Message {msg_idx}.content[{part_idx}] is not a dict: {type(part)}"
                    )
                part_type = part.get("type")
                if part_type:
                    if part_type == "text" and not part.get("text"):
                        raise ValueError(
                            f"Message {msg_idx}.content[{part_idx}] is text type but 'text' is empty or missing"
                        )
                    if part_type == "image_url" and not part.get("image_url"):
                        raise ValueError(
                            f"Message {msg_idx}.content[{part_idx}] is image_url type but 'image_url' is missing"
                        )
                    continue
                bedrock_keys = {
                    "text",
                    "image",
                    "toolUse",
                    "toolResult",
                    "document",
                    "video",
                    "cachePoint",
                    "reasoningContent",
                    "citationsContent",
                    "searchResult",
                }
                if not (bedrock_keys & set(part.keys())):
                    raise ValueError(
                        f"Message {msg_idx}.content[{part_idx}] missing 'type' key "
                        f"and no Bedrock-native block key found"
                    )
                if "text" in part and not part["text"]:
                    raise ValueError(
                        f"Message {msg_idx}.content[{part_idx}] has empty 'text'"
                    )
                if "image" in part:
                    img = part["image"]
                    if not isinstance(img, dict) or not img.get("source", {}).get(
                        "bytes"
                    ):
                        raise ValueError(
                            f"Message {msg_idx}.content[{part_idx}] has malformed 'image' block"
                        )


def _bedrock_content_parts(
    content: str, images: List[ImageAttachment]
) -> List[Dict[str, Any]]:
    """Build a Bedrock-native content parts array for a user message.

    Skips attachments with empty data or an unsupported media type (Bedrock
    only accepts png/jpeg/gif/webp); logs a warning per skip.
    """
    _BEDROCK_MEDIA_TYPE_TO_FORMAT = {
        "image/png": "png",
        "image/jpeg": "jpeg",
        "image/jpg": "jpeg",
        "image/gif": "gif",
        "image/webp": "webp",
    }

    parts: List[Dict[str, Any]] = []
    if content and content.strip():
        parts.append({"text": content.strip()})
    for att in images:
        if not att.data:
            logger.warning("Skipping image attachment with empty data in message")
            continue
        fmt = _BEDROCK_MEDIA_TYPE_TO_FORMAT.get(att.media_type)
        if not fmt:
            logger.warning(
                "Skipping image attachment with unsupported media_type %s for bedrock vision_style",
                att.media_type,
            )
            continue
        b64 = _b64.b64encode(att.data).decode("ascii")
        parts.append({"image": {"format": fmt, "source": {"bytes": b64}}})
    return parts


def _append_image_placeholder_text(content: str, images: List[ImageAttachment]) -> str:
    notes = []
    for att in images:
        dims = f", {att.width}x{att.height}" if att.width and att.height else ""
        src = f" from {att.source_description}" if att.source_description else ""
        notes.append(
            f"[Image attached: {att.media_type}{dims}{src} "
            f"— this backend does not support vision]"
        )
    joined = "\n".join(notes)
    return f"{content}\n\n{joined}" if content else joined


def _strip_none_values(obj: Any) -> Any:
    """Recursively remove None values from JSON schemas"""
    if isinstance(obj, dict):
        return {k: _strip_none_values(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_strip_none_values(item) for item in obj if item is not None]
    return obj


def _format_tools(tools: list[dict]) -> List[Dict[str, Any]]:
    """Convert flat tool format to Ollama's nested function format and strip None values"""
    formatted = []
    for t in tools:
        func = t.get("function", t)
        params = func.get("parameters", {})
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": _strip_none_values(params) if params else {},
                },
            }
        )
    return formatted


__all__ = [
    "_format_messages",
    "_validate_message_content_blocks",
    "_bedrock_content_parts",
    "_append_image_placeholder_text",
    "_strip_none_values",
    "_format_tools",
]
