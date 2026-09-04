from __future__ import annotations

from textual.widgets import Static

PRIMARY_HEX = "#2ecc71"
ACCENT_HEX = "#a9dfbf"
MUTED_HEX = "#6b7d74"


class TodoWidget(Static):
    """Display a dynamic todo list with status breakdown.

    Shows items grouped by status (pending, in_progress, completed, cancelled)
    with colour coding and priority badges.  Updates in real-time as the agent
    calls ``builtin__todowrite`` via :meth:`update_todos`.
    """

    DEFAULT_CSS = """
    TodoWidget {
        width: 100%;
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.todo_state = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_markup(self) -> str:
        """Return Rich markup string for the current todo state."""
        if not self.todo_state or not self.todo_state.items:
            return f"[{MUTED_HEX}]No tasks yet[/{MUTED_HEX}]"

        lines: list[str] = []

        # --- summary bar ---
        summary_parts: list[str] = []
        if self.todo_state.pending_count:
            summary_parts.append(
                f"[{MUTED_HEX}]{self.todo_state.pending_count} pending[/{MUTED_HEX}]"
            )
        if self.todo_state.in_progress_count:
            summary_parts.append(
                f"[bold {PRIMARY_HEX}]{self.todo_state.in_progress_count} active[/bold {PRIMARY_HEX}]"
            )
        if self.todo_state.completed_count:
            summary_parts.append(
                f"[{ACCENT_HEX}]{self.todo_state.completed_count} done[/{ACCENT_HEX}]"
            )
        if self.todo_state.cancelled_count:
            summary_parts.append(
                f"[{MUTED_HEX}]{self.todo_state.cancelled_count} cancelled[/{MUTED_HEX}]"
            )
        if summary_parts:
            lines.append("  ".join(summary_parts))
            lines.append("")

        # --- grouped sections ---
        sections = [
            (
                "in_progress",
                f"[bold {PRIMARY_HEX}]▶ In Progress[/bold {PRIMARY_HEX}]",
                [i for i in self.todo_state.items if i.status == "in_progress"],
            ),
            (
                "pending",
                f"[{MUTED_HEX}]○ Pending[/{MUTED_HEX}]",
                [i for i in self.todo_state.items if i.status == "pending"],
            ),
            (
                "completed",
                f"[{ACCENT_HEX}]✓ Completed[/{ACCENT_HEX}]",
                [i for i in self.todo_state.items if i.status == "completed"],
            ),
            (
                "cancelled",
                f"[{MUTED_HEX}]✗ Cancelled[/{MUTED_HEX}]",
                [i for i in self.todo_state.items if i.status == "cancelled"],
            ),
        ]

        for status_key, title, items in sections:
            if not items:
                continue
            lines.append(title)
            for item in items:
                priority_dot = {
                    "high": "[#ff6b6b]●[/#ff6b6b]",
                    "medium": "[#ffd93d]●[/#ffd93d]",
                    "low": "[#95e1d3]●[/#95e1d3]",
                }.get(item.priority, "·")

                item_style = {
                    "in_progress": f"bold {PRIMARY_HEX}",
                    "pending": MUTED_HEX,
                    "completed": f"dim {ACCENT_HEX}",
                    "cancelled": "dim",
                }.get(status_key, MUTED_HEX)

                lines.append(
                    f"  {priority_dot} [{item_style}]{item.content}[/{item_style}]"
                )
            lines.append("")

        return "\n".join(lines).rstrip()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_todos(self, todo_state) -> None:
        """Replace the current todo state and refresh the widget display."""
        self.todo_state = todo_state
        self.update(self._build_markup())


__all__ = ["TodoWidget"]
