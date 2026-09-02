from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class InputPrompt(ModalScreen[str | None]):
    """Reusable text input modal."""

    CSS = """
    #input-prompt-container {
        align: center middle;
        width: 50;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1;
    }
    #input-prompt-title {
        text-style: bold;
        content-align: center middle;
        padding: 0 0 1 0;
    }
    #input-prompt-field {
        margin: 0 0 1 0;
    }
    #input-prompt-buttons {
        height: 3;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self._title = title
        self._value = value

    def compose(self) -> ComposeResult:
        with Container(id="input-prompt-container"):
            yield Label(self._title, id="input-prompt-title")
            yield Input(
                value=self._value, id="input-prompt-field", classes="input-prompt-field"
            )
            with Horizontal(id="input-prompt-buttons"):
                yield Button("OK", variant="primary", id="input-prompt-ok")
                yield Button("Cancel", id="input-prompt-cancel")

    def on_mount(self) -> None:
        self.query_one("#input-prompt-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "input-prompt-ok":
            self.dismiss(self.query_one("#input-prompt-field", Input).value.strip())
        elif event.button.id == "input-prompt-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["InputPrompt"]
