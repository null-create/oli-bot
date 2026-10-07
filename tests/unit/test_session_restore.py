"""Regression tests for scrollback restore after loading a session.

Two properties are covered:

1. Prior turns are actually visible — tool calls/results dominate tool-heavy
   sessions, and most assistant turns have empty content (tool-only rounds),
   so restoring only ``user``/``assistant`` text showed nothing but the last
   reply.
2. Session text is never handed to Rich as markup. ``read_file``/``grep``
   results routinely contain fragments like ``[/{color}]`` which raise
   ``MarkupError`` during layout and blank the TUI on ``--resume-last``.
"""

import pytest
from rich.console import Console
from rich.errors import MarkupError
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from oli_bot.chat import OliBot, _history_entries
from oli_bot.models import Message

# Source-code tool results routinely look like this and break Rich markup.
POISON = (
    '"""mod"""\ncolor = "#fff"\nrender = f"[{color}]x[/{color}]"  # markup trap\n'
)


def _tool_session() -> list[Message]:
    return [
        Message(role="system", content="system prompt"),
        Message(role="user", content="show me the files"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {
                        "name": "builtin__read_file",
                        "arguments": '{"file_path": "a.py"}',
                    },
                }
            ],
            timestamp="2026-10-07T10:00:00+00:00",
        ),
        Message(
            role="tool",
            content=POISON,
            tool_call_id="call_0",
            timestamp="2026-10-07T10:00:01+00:00",
        ),
        Message(role="assistant", content="Here is the file."),
    ]


def _render(body) -> str:
    console = Console(width=100, force_terminal=False, record=True)
    console.print(body)
    return console.export_text()


def test_restore_shows_tool_traffic_not_just_last_message():
    roles = [entry[0] for entry in _history_entries(_tool_session())]
    assert roles == ["You", "Tool Result", "Assistant"]


def test_painted_tool_result_never_carries_result_body():
    entries = _history_entries(_tool_session())
    role, plain, _body, _ts = next(e for e in entries if e[0] == "Tool Result")
    assert role == "Tool Result"
    assert plain == 'builtin__read_file {"file_path": "a.py"}'
    assert POISON not in plain
    assert len(plain) < 300


@pytest.mark.parametrize("entry_index", range(3))
def test_restore_bodies_render_without_markup_errors(entry_index):
    entries = _history_entries(_tool_session())
    _role, plain, body, _ts = entries[entry_index]
    try:
        _render(body)
    except MarkupError as exc:  # pragma: no cover - failure path
        pytest.fail(f"entry {entry_index} ({plain!r}) raised MarkupError: {exc}")


def test_failed_tool_results_are_marked_with_reason():
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "function": {"name": "builtin__run_command"}}],
        ),
        Message(role="tool", content="Error: command not found: rg", tool_call_id="c1"),
    ]
    entries = _history_entries(messages)
    assert [e[0] for e in entries] == ["Tool Result"]
    rendered = _render(entries[0][2])
    assert "✗" in rendered
    assert "Error: command not found: rg" in rendered


def test_tool_calls_without_results_still_render():
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "function": {"name": "builtin__glob"}}],
        ),
        Message(role="assistant", content="answer"),
    ]
    entries = _history_entries(messages)
    assert [e[0] for e in entries] == ["Tool Call", "Assistant"]
    assert entries[0][1].startswith("builtin__glob")
    _render(entries[0][2])


def test_parallel_tool_calls_keep_names_matched_to_ids():
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {"id": "c1", "function": {"name": "tool_one"}},
                {"id": "c2", "function": {"name": "tool_two"}},
            ],
        ),
        Message(role="tool", content="ok", tool_call_id="c2"),
        Message(role="tool", content="Error: boom", tool_call_id="c1"),
    ]
    entries = _history_entries(messages)
    assert [e[0] for e in entries] == ["Tool Result", "Tool Result"]
    assert entries[0][1].startswith("tool_two")
    assert entries[1][1].startswith("tool_one")
    assert "✗" in _render(entries[1][2])
    assert "✓" in _render(entries[0][2])


def test_system_and_empty_assistant_turns_are_skipped():
    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="   "),
        Message(role="assistant", content="hello"),
    ]
    assert [e[0] for e in _history_entries(messages)] == ["You", "Assistant"]


class _RestoreHost(App):
    """Minimal host binding OliBot's scrollback helpers to a chat log."""

    ROLE_COLORS = OliBot.ROLE_COLORS
    ROLE_ICONS = OliBot.ROLE_ICONS
    _role_color = OliBot._role_color
    _role_icon = OliBot._role_icon
    _format_timestamp = OliBot._format_timestamp
    _flat = OliBot._flat
    _add_message = OliBot._add_message
    _remove_welcome = OliBot._remove_welcome
    _restore_history = OliBot._restore_history

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-log")


async def test_restore_history_mounts_without_markup_error():
    app = _RestoreHost()
    app.messages = _tool_session()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._restore_history()
        await pilot.pause()
        widgets = list(app.query_one("#chat-log").children)
        assert len(widgets) == 3
        plains = [w.plain_text for w in widgets]
        assert plains[0] == "show me the files"
        assert plains[1] == 'builtin__read_file {"file_path": "a.py"}'
        assert plains[2] == "Here is the file."
        assert not any(POISON in p for p in plains)
        # Render every mounted body so a markup trap surfaces here.
        for widget in widgets:
            _render(widget.content)
