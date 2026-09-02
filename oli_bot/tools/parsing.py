from __future__ import annotations

import difflib
import filecmp
from pathlib import Path

from .manager import BuiltinToolManager


def register_tools(manager: BuiltinToolManager) -> None:
    manager.register_tool(
        name="compare",
        description="Compare two files or directories and summarize the differences.",
        parameters={
            "type": "object",
            "properties": {
                "target_a": {
                    "type": "string",
                    "description": "First file or directory path.",
                },
                "target_b": {
                    "type": "string",
                    "description": "Second file or directory path.",
                },
                "mode": {
                    "type": "string",
                    "description": "Compare mode: file or directory.",
                    "enum": ["file", "directory"],
                    "default": "file",
                },
                "ignore_whitespace": {
                    "type": "boolean",
                    "description": "Ignore whitespace differences in file comparisons.",
                    "default": False,
                },
            },
            "required": ["target_a", "target_b"],
        },
        handler=_compare_handler,
    )


def _compare_handler(target_a, target_b, mode="file", ignore_whitespace=False):
    path_a = Path(target_a).expanduser().resolve()
    path_b = Path(target_b).expanduser().resolve()
    if not path_a.exists():
        return f"Error: Path does not exist: {target_a}"
    if not path_b.exists():
        return f"Error: Path does not exist: {target_b}"

    if mode == "file":
        if not path_a.is_file() or not path_b.is_file():
            return "Error: File compare mode requires both targets to be files."
        try:
            text_a = path_a.read_text(encoding="utf-8", errors="replace").splitlines()
            text_b = path_b.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            return f"Error reading files: {e}"
        if ignore_whitespace:
            text_a = [line.strip() for line in text_a]
            text_b = [line.strip() for line in text_b]
        diff = difflib.unified_diff(
            text_a,
            text_b,
            fromfile=str(path_a),
            tofile=str(path_b),
            lineterm="",
        )
        output = "\n".join(diff)
        return output or "Files are identical."

    if mode == "directory":
        if not path_a.is_dir() or not path_b.is_dir():
            return (
                "Error: Directory compare mode requires both targets to be directories."
            )
        cmp = filecmp.dircmp(path_a, path_b)
        parts = [
            f"Common files: {cmp.common_files}",
            f"Only in {path_a}: {cmp.left_only}",
            f"Only in {path_b}: {cmp.right_only}",
            f"Common subdirectories: {cmp.common_dirs}",
        ]
        if cmp.diff_files:
            parts.append(f"Differing files: {cmp.diff_files}")
        if cmp.funny_files:
            parts.append(f"Funny files: {cmp.funny_files}")
        return "\n".join(parts)

    return f"Error: Unsupported compare mode: {mode}"
