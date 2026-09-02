from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView


class WorkspaceListScreen(ModalScreen[Optional[str]]):
    CSS = """
    #workspace-container {
        align: center middle;
        width: 70;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }

    #workspace-title {
        padding: 1;
        text-style: bold;
        content-align: center middle;
    }

    #workspace-current {
        padding: 0 1 1 1;
        content-align: center middle;
    }

    #workspace-list {
        height: 1fr;
        margin: 0 1;
    }

    #workspace-footer {
        padding: 1;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current: Optional[Path], workspaces: list[Path]):
        super().__init__()
        self.current = current
        self.workspaces = workspaces

    def compose(self) -> ComposeResult:
        with Container(id="workspace-container"):
            yield Label("Workspace", id="workspace-title")
            current_label = str(self.current) if self.current else "[red]Not set[/red]"
            yield Label(
                f"Current: [bold]{current_label}[/bold]", id="workspace-current"
            )
            with ListView(id="workspace-list"):
                if self.current:
                    yield ListItem(Label(f"* {self.current}  (current)"))
                for w in self.workspaces:
                    if self.current and w.resolve() == self.current.resolve():
                        continue
                    yield ListItem(Label(str(w)))
            yield Label(
                "\u2191\u2193 navigate \u00b7 Enter select \u00b7 Esc cancel",
                id="workspace-footer",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is not None:
            selected = event.item.query_one(Label).content
            path_str = selected.removeprefix("* ").removesuffix("  (current)")
            self.dismiss(path_str)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["WorkspaceListScreen"]
