from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView


class ModelPicker(ModalScreen[str | None]):
    CSS = """
    #model-picker-container {
        align: center middle;
        width: 50;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }

    #model-picker-title {
        padding: 1;
        text-style: bold;
        content-align: center middle;
    }

    #model-list {
        height: 1fr;
        margin: 0 1;
    }

    #model-picker-footer {
        padding: 1;
        color: $text-muted;
        content-align: center middle;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, models: list[str], current: str):
        super().__init__()
        self.models = models
        self.current = current

    def compose(self) -> ComposeResult:
        with Container(id="model-picker-container"):
            yield Label("Select a model", id="model-picker-title")
            with ListView(id="model-list"):
                for name in self.models:
                    yield ListItem(Label(f"  {name}"))
            yield Label(
                "\u2191\u2193 navigate \u00b7 Enter select \u00b7 Esc cancel",
                id="model-picker-footer",
            )

    def on_mount(self) -> None:
        list_view = self.query_one(ListView)
        for i, name in enumerate(self.models):
            if name == self.current:
                list_view.index = i
                break

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is not None:
            idx = event.list_view.index
            self.dismiss(self.models[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["ModelPicker"]
