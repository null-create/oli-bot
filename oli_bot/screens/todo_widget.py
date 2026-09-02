from __future__ import annotations

from rich.text import Text as RichText
from textual.widgets import Static


class TodoWidget(Static):
    """Display a dynamic todo list with status breakdown.

    Shows items grouped by status (pending, in_progress, completed, cancelled)
    with color coding and priority badges. Updates in real time as the todo
    list changes.
    """

    CSS = """
    #todo-widget {
        width: 100%;
        height: 100%;
        overflow-y: auto;
        border: solid $primary;
        border-title-color: $primary;
        border-title-style: bold;
        padding: 0;
    }

    .todo-header {
        width: 100%;
        padding: 0 1;
        text-style: bold;
        color: $primary;
    }

    .todo-summary {
        width: 100%;
        padding: 0 1;
        color: $text-muted;
        height: auto;
    }

    .todo-section {
        width: 100%;
        padding: 1 0 0 0;
        height: auto;
    }

    .todo-section-title {
        width: 100%;
        padding: 0 1;
        text-style: bold;
        color: $primary;
        height: auto;
    }

    .todo-item {
        width: 100%;
        padding: 0 2;
        height: auto;
    }

    .todo-item-pending {
        color: $text-muted;
    }

    .todo-item-in_progress {
        color: $primary;
    }

    .todo-item-completed {
        color: $accent;
        text-style: dim;
    }

    .todo-item-cancelled {
        color: $text-muted;
        text-style: dim;
    }

    .todo-priority-high {
        color: #ff6b6b;
    }

    .todo-priority-medium {
        color: #ffd93d;
    }

    .todo-priority-low {
        color: #95e1d3;
    }
    """

    def __init__(self, todo_state=None):
        super().__init__()
        self.todo_state = todo_state
        self.id = "todo-widget"

    def render(self) -> RichText | str:
        """Render the current todo state."""
        if not self.todo_state or not self.todo_state.items:
            return "[dim]No todos yet[/dim]"

        lines = []

        summary = (
            f"[bold {PRIMARY_HEX}]Todos[/bold {PRIMARY_HEX}] "
            f"({self.todo_state.total_count} total)"
        )
        if self.todo_state.pending_count > 0:
            summary += f" · [dim]{self.todo_state.pending_count} pending[/dim]"
        if self.todo_state.in_progress_count > 0:
            summary += (
                f" · [bold {PRIMARY_HEX}]{self.todo_state.in_progress_count} "
                f"in progress[/bold {PRIMARY_HEX}]"
            )
        if self.todo_state.completed_count > 0:
            summary += (
                f" · [bold {ACCENT_HEX}]{self.todo_state.completed_count} "
                f"completed[/bold {ACCENT_HEX}]"
            )
        if self.todo_state.cancelled_count > 0:
            summary += f" · [dim]{self.todo_state.cancelled_count} cancelled[/dim]"

        lines.append(summary)
        lines.append("")

        status_groups = {
            "pending": (
                "\u25cb Pending",
                [i for i in self.todo_state.items if i.status == "pending"],
            ),
            "in_progress": (
                "\u25b6 In Progress",
                [i for i in self.todo_state.items if i.status == "in_progress"],
            ),
            "completed": (
                "\u2713 Completed",
                [i for i in self.todo_state.items if i.status == "completed"],
            ),
            "cancelled": (
                "\u2717 Cancelled",
                [i for i in self.todo_state.items if i.status == "cancelled"],
            ),
        }

        for status_key, (title, items) in status_groups.items():
            if not items:
                continue

            title_color = {
                "pending": MUTED_HEX,
                "in_progress": PRIMARY_HEX,
                "completed": ACCENT_HEX,
                "cancelled": MUTED_HEX,
            }.get(status_key, MUTED_HEX)

            lines.append(f"[{title_color}]{title}[/{title_color}]")

            for item in items:
                priority_icon = {
                    "high": "🔴",
                    "medium": "🟡",
                    "low": "🟢",
                }.get(item.priority, "·")

                item_color = {
                    "pending": "dim",
                    "in_progress": f"bold {PRIMARY_HEX}",
                    "completed": f"dim {ACCENT_HEX}",
                    "cancelled": "dim",
                }.get(status_key, "dim")

                content_line = (
                    f"  {priority_icon}  [{item_color}]{item.content}[/{item_color}]"
                )
                lines.append(content_line)

            lines.append("")

        return "\n".join(lines)

    def update_todos(self, todo_state) -> None:
        """Update the todo state and refresh the display."""
        self.todo_state = todo_state
        self.refresh()


__all__ = ["TodoWidget"]
