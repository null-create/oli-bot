from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from .confirm import ConfirmScreen
from .input_prompt import InputPrompt


class SessionListScreen(ModalScreen[tuple | None]):
    """Browse, switch, rename, and delete sessions."""

    CSS = """
    #session-container {
        align: center middle;
        width: 72;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }

    #session-title {
        padding: 1;
        text-style: bold;
        content-align: center middle;
    }

    #session-list {
        height: 1fr;
        margin: 0 1;
    }

    #session-footer {
        padding: 1;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("d", "delete_session", "Delete"),
        ("delete", "delete_session", "Delete"),
        ("r", "rename_session", "Rename"),
    ]

    def __init__(self, sessions: list[dict], current_session_id: str):
        super().__init__()
        self.sessions = sessions
        self.current_session_id = current_session_id

    def compose(self) -> ComposeResult:
        with Container(id="session-container"):
            yield Label("Sessions", id="session-title")
            with ListView(id="session-list"):
                for s in self.sessions:
                    marker = " *" if s["id"] == self.current_session_id else "  "
                    short_id = s["id"][:8]
                    updated = (
                        s["updated_at"][:16].replace("T", " ")
                        if s.get("updated_at")
                        else ""
                    )
                    yield ListItem(
                        Label(
                            f"{marker}[bold]{short_id}[/bold] — {s['name']} "
                            f"({s['msg_count']} msgs, {updated})"
                        )
                    )
            yield Label(
                "\u2191\u2193 navigate \u00b7 Enter switch \u00b7 R rename \u00b7 D delete \u00b7 Esc cancel",
                id="session-footer",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is not None:
            idx = event.list_view.index
            if idx is not None:
                self.dismiss(("switch", self.sessions[idx]["id"]))

    @work(exclusive=False)
    async def action_delete_session(self) -> None:
        idx = self.query_one(ListView).index
        if idx is None:
            return
        session = self.sessions[idx]
        result = await self.app.push_screen_wait(
            ConfirmScreen(
                "Delete Session",
                f"Delete session '{session['name']}'?\n{session['msg_count']} messages will be lost.",
            )
        )
        if result == "yes":
            self.dismiss(("delete", session["id"]))

    @work(exclusive=False)
    async def action_rename_session(self) -> None:
        idx = self.query_one(ListView).index
        if idx is None:
            return
        session = self.sessions[idx]
        current_name = session["name"]
        result = await self.app.push_screen_wait(
            InputPrompt("Rename session", current_name)
        )
        if result and result != current_name:
            self.dismiss(("rename", session["id"], result))

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["SessionListScreen"]
