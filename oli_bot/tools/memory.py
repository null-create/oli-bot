from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from .manager import BuiltinToolManager

# Populated by _dispatch_tasks in chat.py for each concurrent sub-agent task.
# asyncio.gather copies the current context into each spawned Task, so each
# sub-agent sees its own isolated value without any locking needed.
_current_sub_run: ContextVar = ContextVar("_current_sub_run", default=None)


def register_tools(manager: BuiltinToolManager) -> None:
    manager.register_tool(
        name="think",
        description="Use this tool to reason step-by-step internally before responding. "
        "Write out your chain-of-thought, analysis, or planning in the "
        "thought parameter. The content is stored in conversation history "
        "but is not displayed to the user. "
        "Use this for complex problem-solving, debugging analysis, "
        "or planning multi-step operations.",
        parameters={
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Your internal reasoning, analysis, or planning notes.",
                }
            },
            "required": ["thought"],
        },
        handler=_think_handler,
    )

    manager.register_tool(
        name="todowrite",
        description="Create and maintain a structured task list for the current coding session. "
        "Tracks progress, organizes multi-step work, and surfaces status to the user. "
        "Use this to plan out multi-step tasks, track what's in progress, "
        "and ensure nothing is forgotten. "
        "Call with the full updated todo list each time you want to create or update it.",
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Brief description of the task",
                            },
                            "status": {
                                "type": "string",
                                "description": "Current status of the task: pending, in_progress, completed, cancelled",
                            },
                            "priority": {
                                "type": "string",
                                "description": "Priority level of the task: high, medium, low",
                            },
                        },
                        "required": ["content", "status", "priority"],
                    },
                    "description": "The updated todo list",
                },
            },
            "required": ["todos"],
        },
        handler=lambda todos: _todowrite_handler(todos, manager),
    )

    manager.register_tool(
        name="notebook",
        description="Agent's working memory stored as Markdown files under a notes/ directory. "
        "Use pages to store session notes, plans, and important information "
        "as Markdown documents. "
        "get — retrieve a specific page or list all pages; "
        "set — write Markdown content to a page; "
        "delete — remove a page; "
        "list — show all available pages. "
        "Pages named 'plan-<name>' are treated as saved plans: if one already "
        "exists, set automatically writes to 'plan-<name>-2', '-3', etc. "
        "instead of overwriting it.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Operation to perform: get, set, delete, or list.",
                    "enum": ["get", "set", "delete", "list"],
                },
                "page": {
                    "type": "string",
                    "description": "Page name (without .md extension). Required for get (single page), set, and delete.",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content to write. Required for set action.",
                },
            },
            "required": ["action"],
        },
        handler=_notebook_handler,
    )


def _sanitize_page_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def _think_handler(thought: dict) -> dict:
    return thought


def _todowrite_handler(
    todos: list[dict], manager: Optional[BuiltinToolManager] = None
) -> str:
    if not todos:
        return "Todo list is empty."

    sub_run = _current_sub_run.get()

    if sub_run is not None:
        # ── Sub-agent path ──────────────────────────────────────────────
        # Store todos on the run object so the tree UI can reflect them.
        sub_run.todos = todos
        if manager is not None and manager._sub_todo_change_callback is not None:
            try:
                manager._sub_todo_change_callback(sub_run, todos)
            except Exception:
                pass  # never let a UI callback crash the tool
    else:
        # ── Root-agent path ─────────────────────────────────────────────
        # Update the manager's internal todo list so the TUI widget can access it.
        if manager is not None:
            manager._todos = todos
            if manager._todo_change_callback is not None:
                try:
                    manager._todo_change_callback(todos)
                except Exception:
                    pass  # never let a UI callback crash the tool

    by_status: dict[str, int] = {}
    for t in todos:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1

    parts = [f"Todos ({len(todos)} total)"]
    status_counts = ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))
    parts.append(status_counts)
    parts.append("")
    for i, t in enumerate(todos, 1):
        icon = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
            "cancelled": "[-]",
        }.get(t["status"], "[?]")
        parts.append(
            f"{i}. {icon} {t['content']} ({t['priority']} priority, {t['status']})"
        )
    return "\n".join(parts)


def _notebook_handler(action: str, page: str = None, content: str = None) -> str:
    notes_dir = Path("notes").resolve()
    if action == "list":
        if not notes_dir.is_dir():
            return "No pages found."
        pages = sorted(notes_dir.glob("*.md"))
        if not pages:
            return "No pages found."
        lines = [f"Pages ({len(pages)}):"]
        for p in pages:
            lines.append(f"  {p.stem}")
        return "\n".join(lines)

    if not page:
        return "Error: page is required for get, set, and delete actions."

    page = page.strip()
    if not page:
        return "Error: page name cannot be empty."

    safe_name = _sanitize_page_name(page)
    note_path = notes_dir / f"{safe_name}.md"

    if action == "get":
        if not note_path.exists():
            return f"Page '{page}' not found. Available pages: {', '.join(p.stem for p in sorted(notes_dir.glob('*.md'))) if notes_dir.is_dir() else 'none'}"
        try:
            return note_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading page '{page}': {e}"

    elif action == "set":
        if content is None:
            return "Error: content is required for set action."
        if safe_name.startswith("plan-") and note_path.exists():
            suffix = 2
            while (notes_dir / f"{safe_name}-{suffix}.md").exists():
                suffix += 1
            safe_name = f"{safe_name}-{suffix}"
            note_path = notes_dir / f"{safe_name}.md"
        try:
            notes_dir.mkdir(parents=True, exist_ok=True)
            note_path.write_text(content, encoding="utf-8")
            return (
                f"Page '{safe_name}' saved to {note_path} ({len(content)} characters)."
            )
        except Exception as e:
            return f"Error writing page '{page}': {e}"

    elif action == "delete":
        if not note_path.exists():
            return f"Page '{page}' not found."
        try:
            note_path.unlink()
            return f"Page '{page}' deleted."
        except Exception as e:
            return f"Error deleting page '{page}': {e}"

    return f"Error: Unknown action '{action}'."
