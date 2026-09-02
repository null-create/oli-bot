from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from ..server_manager import HostConfig


class ServerListScreen(ModalScreen[Optional[str]]):
    CSS = """
    #servers-container {
        align: center middle;
        width: 60;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }

    #servers-title {
        padding: 1;
        text-style: bold;
        content-align: center middle;
    }

    #servers-list {
        height: 1fr;
        margin: 0 1;
    }

    #servers-footer {
        padding: 1;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, servers: list[HostConfig]):
        super().__init__()
        self.servers = servers

    def compose(self) -> ComposeResult:
        with Container(id="servers-container"):
            yield Label("Ollama servers", id="servers-title")
            with ListView(id="servers-list"):
                for s in self.servers:
                    prefix = "* " if s.active else "  "
                    model = f"  ({s.default_model})" if s.default_model else ""
                    yield ListItem(Label(f"{prefix}{s.name} — {s.url}{model}"))
            yield Label(
                "\u2191\u2193 navigate \u00b7 Enter switch \u00b7 Esc cancel",
                id="servers-footer",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is not None:
            idx = event.list_view.index
            self.dismiss(self.servers[idx].name)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["ServerListScreen"]
