"""Tests for `_format_messages` image serialization across backends."""

from __future__ import annotations

import base64

import pytest

from oli_bot.backends import _format_messages, _validate_message_content_blocks
from oli_bot.models import ImageAttachment, Message

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-payload"


def _msg_with_image(role: str = "user", content: str = "look at this") -> Message:
    return Message(
        role=role,
        content=content,
        images=[
            ImageAttachment(
                data=_PNG_BYTES,
                media_type="image/png",
                source_description="/tmp/foo.png",
                width=64,
                height=48,
            )
        ],
    )


# ---------- ollama -----------------------------------------------------------


def test_ollama_style_populates_images_field_and_keeps_content():
    msg = _msg_with_image()
    out = _format_messages([msg], image_style="ollama")
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"] == "look at this"
    assert "images" in out[0]
    encoded = out[0]["images"][0]
    assert base64.b64decode(encoded) == _PNG_BYTES


def test_ollama_style_ignores_messages_without_images():
    msg = Message(role="user", content="plain")
    out = _format_messages([msg], image_style="ollama")
    assert "images" not in out[0]


# ---------- openai -----------------------------------------------------------


def test_openai_style_rewrites_user_content_as_parts_array():
    msg = _msg_with_image()
    out = _format_messages([msg], stringify_arguments=True, image_style="openai")
    parts = out[0]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "look at this"}
    assert parts[1]["type"] == "image_url"
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == _PNG_BYTES


def test_openai_style_leaves_non_user_role_alone():
    # OpenAI only accepts array content on user messages; tool/assistant stay strings.
    msg = Message(role="tool", content="tool result text", tool_call_id="c1")
    msg.images = [ImageAttachment(data=_PNG_BYTES, media_type="image/png")]
    out = _format_messages([msg], stringify_arguments=True, image_style="openai")
    assert out[0]["content"] == "tool result text"


def test_openai_style_empty_content_still_emits_image_only():
    msg = _msg_with_image(content="")
    out = _format_messages([msg], stringify_arguments=True, image_style="openai")
    parts = out[0]["content"]
    assert isinstance(parts, list)
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"


# ---------- text-only fallback ----------------------------------------------


def test_none_style_appends_bracketed_placeholder_note():
    msg = _msg_with_image()
    out = _format_messages([msg], image_style="none")
    content = out[0]["content"]
    assert content.startswith("look at this")
    assert "[Image attached" in content
    assert "image/png" in content
    assert "64x48" in content
    assert "/tmp/foo.png" in content
    assert "does not support vision" in content
    assert "images" not in out[0]


def test_none_style_with_empty_content_uses_just_the_note():
    msg = _msg_with_image(content="")
    out = _format_messages([msg], image_style="none")
    assert out[0]["content"].startswith("[Image attached")


# ---------- backward compatibility -------------------------------------------


def test_message_without_images_unaffected_across_styles():
    msg = Message(role="user", content="hi")
    for style in ("none", "ollama", "openai"):
        out = _format_messages([msg], image_style=style)
        assert out[0] == {"role": "user", "content": "hi"}


def test_default_image_style_is_none():
    # Ensures existing callers that don't pass image_style get text-fallback
    # (drop image bytes, append a placeholder), never leaking base64 blobs.
    msg = _msg_with_image()
    out = _format_messages([msg])
    assert "images" not in out[0]
    assert isinstance(out[0]["content"], str)
    assert "[Image attached" in out[0]["content"]


# ---------- bedrock ---------------------------------------------------------


def _msg_with_image_media(media_type: str, content: str = "look") -> Message:
    return Message(
        role="user",
        content=content,
        images=[ImageAttachment(data=_PNG_BYTES, media_type=media_type)],
    )


def test_bedrock_style_emits_native_image_block_on_user_message():
    msg = _msg_with_image(content="what is this")
    out = _format_messages([msg], stringify_arguments=True, image_style="bedrock")
    parts = out[0]["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"text": "what is this"}
    assert parts[1]["image"]["format"] == "png"
    assert base64.b64decode(parts[1]["image"]["source"]["bytes"]) == _PNG_BYTES
    # Validator must accept Bedrock-native blocks without complaint.
    _validate_message_content_blocks(out)


def test_bedrock_style_maps_supported_media_types():
    for mime, expected in (
        ("image/png", "png"),
        ("image/jpeg", "jpeg"),
        ("image/jpg", "jpeg"),
        ("image/gif", "gif"),
        ("image/webp", "webp"),
    ):
        msg = _msg_with_image_media(mime)
        out = _format_messages([msg], stringify_arguments=True, image_style="bedrock")
        parts = out[0]["content"]
        image_blocks = [p for p in parts if "image" in p]
        assert len(image_blocks) == 1
        assert image_blocks[0]["image"]["format"] == expected


def test_bedrock_style_skips_unsupported_media_type():
    msg = _msg_with_image_media("image/svg+xml", content="caption")
    out = _format_messages([msg], stringify_arguments=True, image_style="bedrock")
    # Unsupported media type is dropped; only the caption text block remains.
    assert out[0]["content"] == [{"text": "caption"}]


def test_bedrock_style_omits_empty_caption_text_block():
    msg = _msg_with_image(content="")
    out = _format_messages([msg], stringify_arguments=True, image_style="bedrock")
    parts = out[0]["content"]
    assert isinstance(parts, list)
    assert len(parts) == 1
    assert "image" in parts[0]


def test_bedrock_style_leaves_non_user_role_alone():
    msg = Message(role="tool", content="tool result text", tool_call_id="c1")
    msg.images = [ImageAttachment(data=_PNG_BYTES, media_type="image/png")]
    out = _format_messages([msg], stringify_arguments=True, image_style="bedrock")
    assert out[0]["content"] == "tool result text"


def test_validator_rejects_unknown_block_without_type_or_bedrock_key():

    bad = [
        {
            "role": "user",
            "content": [{"image_url": {"url": "data:image/png;base64,AAAA"}}],
        }
    ]
    with pytest.raises(ValueError, match="missing 'type' key"):
        _validate_message_content_blocks(bad)
