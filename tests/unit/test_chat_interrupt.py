"""Tests for the TUI Ctrl+I interrupt rollback logic.

The full Textual interaction is exercised manually; these tests pin the
rollback guard and the "is a response active?" check, which are the parts a
mistake would silently corrupt history with.
"""

from oli_bot.chat import OliBot
from oli_bot.models import Message


def _bot(messages, *, turn_id, user_index, user_text):
    bot = object.__new__(OliBot)
    bot.messages = messages
    bot._turn_id = turn_id
    bot._turn_user_index = user_index
    bot._turn_user_text = user_text
    calls: list[str] = []
    bot._refresh_context_tokens = lambda: calls.append("refresh")
    bot._save_session = lambda: calls.append("save")
    return bot, calls


def test_rollback_removes_interrupted_turn_and_persists():
    bot, calls = _bot(
        [
            Message(role="system", content="sys"),
            Message(role="user", content="keep"),
            Message(role="assistant", content="ok"),
            Message(role="user", content="interrupt me"),
        ],
        turn_id=3,
        user_index=3,
        user_text="interrupt me",
    )
    assert bot._rollback_interrupted_turn(3) is True
    assert [m.content for m in bot.messages] == ["sys", "keep", "ok"]
    assert calls == ["refresh", "save"]


def test_rollback_ignores_stale_turn_id():
    """A newer turn started before the old worker noticed the cancel."""
    bot, calls = _bot(
        [Message(role="user", content="new prompt")],
        turn_id=4,
        user_index=0,
        user_text="new prompt",
    )
    assert bot._rollback_interrupted_turn(3) is False
    assert [m.content for m in bot.messages] == ["new prompt"]
    assert calls == []


def test_rollback_ignores_replaced_message_at_same_index():
    bot, calls = _bot(
        [Message(role="user", content="different")],
        turn_id=1,
        user_index=0,
        user_text="original",
    )
    assert bot._rollback_interrupted_turn(1) is False
    assert [m.content for m in bot.messages] == ["different"]
    assert calls == []


def test_rollback_ignores_out_of_range_index():
    bot, calls = _bot([], turn_id=1, user_index=5, user_text="gone")
    assert bot._rollback_interrupted_turn(1) is False
    assert calls == []


def test_response_active_reads_worker_and_agent_flag():
    bot = object.__new__(OliBot)
    bot._response_worker = None
    bot.agent = type("_Agent", (), {"_generating": False})()
    assert bot._response_active() is False

    bot._response_worker = type("_Worker", (), {"is_finished": False})()
    assert bot._response_active() is True

    bot._response_worker = type("_Worker", (), {"is_finished": True})()
    assert bot._response_active() is False

    bot.agent._generating = True
    assert bot._response_active() is True
