"""Regression tests for ConversationStore round-trip and save fallback."""

from pathlib import Path

import pytest

from oli_bot.models import ImageAttachment, Message
from oli_bot.sessions import ConversationStore


def test_message_roundtrip_preserves_tool_call_id(tmp_path):
    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "system")
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="calling", tool_calls=[{"id": "c1"}]),
        Message(role="tool", content="ok", tool_call_id="c1"),
    ]
    store.save_session("srv", sid, msgs, "m", "p")
    data = store.load_session("srv", sid)
    assert data is not None
    tool_msgs = [m for m in data["messages"] if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"


def test_save_returns_new_id_when_file_missing(tmp_path):
    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "")
    # Simulate corruption: nuke the file
    (tmp_path / "srv" / f"{sid}.json").unlink()
    new_sid = store.save_session(
        "srv", sid, [Message(role="user", content="x")], "m", "p"
    )
    assert new_sid != sid
    # A subsequent save under the returned id must NOT create yet another id
    stable_sid = store.save_session(
        "srv", new_sid, [Message(role="user", content="y")], "m", "p"
    )
    assert stable_sid == new_sid


def test_save_returns_same_id_on_happy_path(tmp_path):
    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "")
    returned = store.save_session(
        "srv", sid, [Message(role="user", content="x")], "m", "p"
    )
    assert returned == sid


def test_sanitize_tool_history_repairs_orphan_from_disk(tmp_path):
    """Regression: a session saved by an older build with an orphan assistant
    tool_use must be self-healed on load (via the sanitize call at the load
    sites in [chat.py](chat.py)). This test exercises the sanitizer directly
    to keep the assertion hermetic vs the TUI wiring."""
    from oli_bot.agent import sanitize_tool_history

    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "")
    poisoned = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "tooluse_orphan",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        ),
        # No matching role=tool message; this is the poisoned state.
        Message(
            role="assistant", content="Error: Error code: 400 - toolConfig missing"
        ),
        Message(role="user", content="continue"),
    ]
    store.save_session("srv", sid, poisoned, "m", "p")
    data = store.load_session("srv", sid)
    assert data is not None

    loaded = [
        Message(
            **{
                k: v
                for k, v in m.items()
                if k
                in {
                    "role",
                    "content",
                    "tool_calls",
                    "name",
                    "timestamp",
                    "tool_call_id",
                }
            }
        )
        for m in data["messages"]
    ]
    healed = sanitize_tool_history(loaded)
    # The orphan assistant(tool_use) is dropped; the fake error assistant and
    # the follow-up user turn survive.
    assert not any(m.tool_calls for m in healed)
    assert [m.role for m in healed] == ["user", "assistant", "user"]


def test_message_roundtrip_drops_image_attachments(tmp_path):
    """Image bytes must NOT be persisted to session JSON (would bloat disk
    and add no value on resume — the model already consumed them)."""
    import json

    store = ConversationStore(sessions_dir=tmp_path)
    sid = store.create_session("srv", "m", "p", "")
    msgs = [
        Message(
            role="user",
            content="look",
            images=[
                ImageAttachment(
                    data=b"\x89PNGfake",
                    media_type="image/png",
                    source_description="/tmp/foo.png",
                )
            ],
        )
    ]
    store.save_session("srv", sid, msgs, "m", "p")

    raw = (tmp_path / "srv" / f"{sid}.json").read_text(encoding="utf-8")
    assert "images" not in raw
    assert "PNGfake" not in raw

    data = store.load_session("srv", sid)
    assert data is not None
    loaded = [
        Message(
            **{
                k: v
                for k, v in m.items()
                if k
                in {
                    "role",
                    "content",
                    "tool_calls",
                    "name",
                    "timestamp",
                    "tool_call_id",
                }
            }
        )
        for m in data["messages"]
    ]
    non_system = [m for m in loaded if m.role != "system"]
    assert non_system[0].content == "look"
    assert non_system[0].images is None
