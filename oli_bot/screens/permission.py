from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class PermissionScreen(ModalScreen[str]):
    """Ask user for permission to perform a tool action."""

    CSS = """
    #permission-container {
        align: center middle;
        width: 60;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1;
    }
    #permission-title {
        text-style: bold;
        content-align: center middle;
        padding: 1 0;
    }
    #permission-desc {
        padding: 1 0;
        content-align: center middle;
    }
    #permission-buttons {
        height: 3;
        align: center middle;
    }
    Button {
        margin: 0 1;
    }
    """

    BINDINGS = [("escape", "deny", "Deny")]

    def __init__(self, description: str):
        super().__init__()
        self.description = description

    def compose(self) -> ComposeResult:
        with Container(id="permission-container"):
            yield Label("Permission Required", id="permission-title")
            yield Label(self.description, id="permission-desc")
            with Horizontal(id="permission-buttons"):
                yield Button("Allow once", id="allow-once", variant="primary")
                yield Button("Allow for session", id="allow-session", variant="primary")
                yield Button("Deny", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow-once":
            self.dismiss("once")
        elif event.button.id == "allow-session":
            self.dismiss("session")
        elif event.button.id == "deny":
            self.dismiss("deny")

    def action_deny(self) -> None:
        self.dismiss("deny")


__all__ = ["PermissionScreen"]
