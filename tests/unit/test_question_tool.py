"""The ``builtin__question`` tool: registration, callback plumbing, and result
formatting, plus the ``QuestionScreen`` modal's confirm/cancel flow.
"""

from __future__ import annotations

import pytest

from oli_bot.config import AppConfig
from oli_bot.tools.manager import BuiltinToolManager, PLAN_TOOLS, READ_ONLY_TOOLS
from oli_bot.tools.question import _resolve_recommended


def _manager() -> BuiltinToolManager:
    return BuiltinToolManager(config=AppConfig(_env_file=None, offline_mode=False))


def _spec(*questions: dict) -> dict:
    return {"questions": list(questions)}


# ---------- registration ------------------------------------------------------


def test_question_tool_registered_with_schema():
    m = _manager()
    names = {t["name"] for t in m.get_tool_definitions()}
    assert "builtin__question" in names

    info = next(t for t in m.get_tool_definitions() if t["name"] == "builtin__question")
    props = info["parameters"]["properties"]
    assert "questions" in props
    assert "questions" in info["parameters"]["required"]
    q = props["questions"]["items"]
    assert "question" in q["required"]


def test_question_available_in_ask_and_plan_modes():
    m = _manager()
    readonly = {t["name"] for t in m.get_readonly_tool_definitions()}
    plan = {t["name"] for t in m.get_plan_tool_definitions()}
    assert "builtin__question" in readonly
    assert "question" in READ_ONLY_TOOLS
    assert "question" in PLAN_TOOLS
    assert "builtin__question" in plan


# ---------- handler behavior --------------------------------------------------


@pytest.mark.asyncio
async def test_question_handler_returns_callback_result():
    m = _manager()
    received: list | None = None
    answers = "Question 1: A\nUser answer: Production"

    async def callback(questions):
        nonlocal received
        received = questions
        return answers

    m.set_question_callback(callback)
    result = await m.call_tool(
        "question",
        _spec({"question": "Which env?", "options": ["Staging", "Production"]}),
    )
    assert result == answers
    assert received == [
        {"question": "Which env?", "options": ["Staging", "Production"]}
    ]


@pytest.mark.asyncio
async def test_question_handler_awaits_sync_callback():
    m = _manager()
    m.set_question_callback(lambda questions: "User answer: yes")
    result = await m.call_tool("question", _spec({"question": "Proceed?"}))
    assert result == "User answer: yes"


@pytest.mark.asyncio
async def test_question_without_callback_returns_error():
    m = _manager()
    result = await m.call_tool("question", _spec({"question": "Ping?"}))
    assert result.startswith("Error:")
    assert "interactive user" in result


@pytest.mark.asyncio
async def test_question_callback_none_means_declined():
    m = _manager()
    m.set_question_callback(lambda questions: None)
    result = await m.call_tool("question", _spec({"question": "Ping?"}))
    assert result.startswith("Error:")
    assert "declined" in result


@pytest.mark.asyncio
async def test_question_with_no_questions_returns_error():
    m = _manager()
    m.set_question_callback(lambda questions: "n/a")
    result = await m.call_tool("question", _spec())
    assert result.startswith("Error:")
    assert "no questions" in result


# ---------- recommended resolution --------------------------------------------


def test_resolve_recommended_index_int():
    assert _resolve_recommended(2, ["a", "b", "c"]) == (1, None)


def test_resolve_recommended_digit_string():
    assert _resolve_recommended("3", ["a", "b", "c"]) == (2, None)


def test_resolve_recommended_matching_option_text():
    assert _resolve_recommended("Production", ["Staging", "Production"]) == (1, None)


def test_resolve_recommended_partial_option_text():
    assert _resolve_recommended("prod", ["staging", "production"]) == (1, None)


def test_resolve_recommended_freeform():
    assert _resolve_recommended("roll back first", ["a", "b"]) == (
        None,
        "roll back first",
    )


def test_resolve_recommended_out_of_range_falls_back():
    assert _resolve_recommended(9, ["a", "b"]) == (None, None)
    assert _resolve_recommended(True, ["a", "b"]) == (None, None)
    assert _resolve_recommended(None, ["a", "b"]) == (None, None)


# ---------- QuestionScreen modal ----------------------------------------------


from textual.app import App  # noqa: E402
from textual.widgets import Input, RadioButton, RadioSet  # noqa: E402

from oli_bot.screens import QuestionScreen  # noqa: E402


class _QuestionHost(App):
    def __init__(self, questions):
        super().__init__()
        self._questions = questions
        self.result = None

    def compose(self):
        return []

    def on_mount(self):
        self.push_screen(
            QuestionScreen(self._questions),
            callback=lambda result: setattr(self, "result", result),
        )


async def test_question_screen_preselects_recommended():
    app = _QuestionHost(
        [{"question": "Env?", "options": ["Staging", "Production"], "recommended": "2"}]
    )
    async with app.run_test():
        rs = app.screen.query_one("#q0-options", RadioSet)
        assert rs.index == 1


async def test_question_screen_confirm_with_custom_answer():
    app = _QuestionHost([{"question": "Env?", "options": ["Staging", "Production"]}])
    async with app.run_test() as pilot:
        app.screen.query_one("#q0-input", Input).value = "preprod"
        await pilot.click("#question-confirm")
        await pilot.pause()
    assert app.result is not None
    assert "Question 1: Env?" in app.result
    assert "User answer: preprod" in app.result


def _press(rs: RadioSet, index: int) -> None:
    buttons = list(rs.query(RadioButton))
    buttons[index].value = True
    rs.index = index


async def test_question_screen_confirm_with_option_selection():
    app = _QuestionHost([{"question": "Env?", "options": ["Staging", "Production"]}])
    async with app.run_test() as pilot:
        _press(app.screen.query_one("#q0-options", RadioSet), 1)
        await pilot.click("#question-confirm")
        await pilot.pause()
    assert app.result is not None
    assert "User answer: Production" in app.result
    assert "No answer provided" not in app.result


async def test_question_screen_custom_answer_wins_over_option():
    app = _QuestionHost([{"question": "Env?", "options": ["Staging", "Production"]}])
    async with app.run_test() as pilot:
        _press(app.screen.query_one("#q0-options", RadioSet), 1)
        app.screen.query_one("#q0-input", Input).value = "custom-env"
        await pilot.click("#question-confirm")
        await pilot.pause()
    assert app.result is not None
    assert "User answer: custom-env" in app.result


async def test_question_screen_cancel_returns_none():
    app = _QuestionHost([{"question": "Proceed?"}])
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None
