from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmScreen(ModalScreen[str]):
    """Generic confirmation modal with Yes/No buttons."""

    CSS = """
    #confirm-container {
        align: center middle;
        width: 50;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1;
    }
    #confirm-title {
        text-style: bold;
        content-align: center middle;
        padding: 1 0;
    }
    #confirm-message {
        padding: 1 0;
        content-align: center middle;
    }
    #confirm-buttons {
        height: 3;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "no", "No")]

    def __init__(self, title: str, message: str):
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Label(self._title, id="confirm-title")
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="primary", id="confirm-yes")
                yield Button("No", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes":
            self.dismiss("yes")
        elif event.button.id == "confirm-no":
            self.dismiss("no")

    def action_no(self) -> None:
        self.dismiss("no")


__all__ = ["ConfirmScreen"]
