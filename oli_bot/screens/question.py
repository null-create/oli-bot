from __future__ import annotations

from typing import List, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet

from ..tools.question import _resolve_recommended


class QuestionScreen(ModalScreen[str | None]):
    """Present questions posed by the agent and collect the user's answers.

    Each question is rendered as a numbered block with its suggested options
    (the recommended one pre-selected when the model marked it) and a "Write
    your own answer" field. A final Confirm button resolves every question's
    answer (custom text wins over a selected option) and dismisses with a
    text summary that becomes the ``question`` tool result. Esc cancels.
    """

    CSS = """
    #question-container {
        width: 78;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1;
        margin: 1 2;
    }
    #question-title {
        text-style: bold;
        content-align: center middle;
        padding: 0 0 1 0;
    }
    #question-scroll {
        height: 1fr;
        border: round #6b7d74;
        padding: 1 2;
    }
    .question-block {
        margin: 0 0 1 0;
    }
    .question-text {
        text-style: bold;
    }
    .question-meta {
        color: $text-muted;
    }
    RadioSet {
        margin: 0 0 1 0;
        border: round #6b7d74;
    }
    RadioSet:focus {
        border: round $primary;
    }
    .question-own-input {
        margin: 1 0 1 0;
    }
    #question-buttons {
        height: 3;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, questions: List[dict]):
        super().__init__()
        self._questions = list(questions)

    def compose(self) -> ComposeResult:
        with Container(id="question-container"):
            yield Label("Question", id="question-title")
            with VerticalScroll(id="question-scroll"):
                for i, q in enumerate(self._questions):
                    with Container(classes="question-block"):
                        yield Label(
                            f"{i + 1}. {q.get('question', '')}",
                            classes="question-text",
                        )
                        if q.get("options"):
                            idx, freeform = _resolve_recommended(
                                q.get("recommended"), q.get("options")
                            )
                            if freeform:
                                yield Label(
                                    f"[Recommended action: {freeform}]",
                                    classes="question-meta",
                                )
                            yield RadioSet(
                                *[
                                    RadioButton(
                                        str(opt),
                                        id=f"q{i}-opt{j}",
                                    )
                                    for j, opt in enumerate(q["options"])
                                ],
                                id=f"q{i}-options",
                            )
                        yield Label("Write your own answer:", classes="question-meta")
                        yield Input(
                            placeholder="Type your own answer",
                            id=f"q{i}-input",
                            classes="question-own-input",
                        )
            with Horizontal(id="question-buttons"):
                yield Button("Confirm", variant="primary", id="question-confirm")
                yield Button("Cancel", id="question-cancel")

    def on_mount(self) -> None:
        for i, q in enumerate(self._questions):
            if not q.get("options"):
                continue
            rs = self.query_one(f"#q{i}-options", RadioSet)
            idx, _ = _resolve_recommended(q.get("recommended"), q.get("options"))
            buttons = list(rs.query(RadioButton))
            if idx is not None and 0 <= idx < len(buttons):
                buttons[idx].value = True
                rs.index = idx
        first_input = self.query_one("#q0-input", Input)
        if first_input is not None:
            first_input.focus()

    def _answer(self, i: int) -> str:
        q = self._questions[i]
        custom = self.query_one(f"#q{i}-input", Input).value.strip()
        if custom:
            return custom
        if q.get("options"):
            rs = self.query_one(f"#q{i}-options", RadioSet)
            idx = rs.pressed_index
            if idx is not None and 0 <= idx < len(rs.children):
                return str(rs.children[idx].label)
        return "No answer provided"

    def _format_result(self) -> str:
        parts: list[str] = []
        for i, q in enumerate(self._questions):
            parts.append(f"Question {i + 1}: {q.get('question', '')}")
            parts.append(f"User answer: {self._answer(i)}")
            parts.append("")
        return "\n".join(parts).rstrip()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "question-confirm":
            self.dismiss(self._format_result())
        elif event.button.id == "question-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["QuestionScreen"]
