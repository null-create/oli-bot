from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from .manager import BuiltinToolManager


def register_tools(manager: BuiltinToolManager) -> None:
    manager.register_tool(
        name="glob",
        description="Find files matching a glob pattern, recursively. "
        "Use this to discover files by name or extension throughout the "
        "project (e.g., '**/*.py', '*.json', 'src/**/*.ts').",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The glob pattern to match (e.g., '**/*.py', '*.md', 'src/**/*.js').",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to current directory.",
                },
            },
            "required": ["pattern"],
        },
        handler=_glob_handler,
    )

    manager.register_tool(
        name="grep",
        description="Search file contents using a regular expression pattern. "
        "Returns matching lines with file paths and line numbers. "
        "Use this to find relevant code, references, imports, "
        "or any text across the project. "
        "Limit results with max_results to avoid overwhelming output.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to current directory.",
                },
                "include": {
                    "type": "string",
                    "description": "Optional glob pattern to filter files (e.g., '*.py', '*.{ts,tsx}').",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of matches to return. Defaults to 50.",
                    "default": 50,
                },
            },
            "required": ["pattern"],
        },
        handler=_grep_handler,
    )

    manager.register_tool(
        name="list_directory",
        description="List the contents of a directory with file metadata "
        "(type, modification time, size). "
        "Use this to explore project structure, see what files exist, "
        "or inspect directory layout.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list. Defaults to current directory.",
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Whether to show hidden files (dotfiles). Defaults to False.",
                    "default": False,
                },
            },
            "required": [],
        },
        handler=_list_directory_handler,
    )

    manager.register_tool(
        name="tree",
        description="Display directory structure as a tree. "
        "Shows the recursive layout of files and subdirectories. "
        "Use this to explore project structure, understand directory "
        "layouts, or get an overview of how files are organized.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to tree. Defaults to current directory.",
                    "default": ".",
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum depth of the tree. If not set, shows full depth.",
                    "minimum": 1,
                },
            },
            "required": [],
        },
        handler=_tree_handler,
    )


def _glob_handler(pattern, path="."):
    return asyncio.to_thread(_glob_sync, pattern, path)


def _glob_sync(pattern, path="."):
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return f"Error: Directory not found: {path}"
    try:
        matches = sorted(str(p.relative_to(root)) for p in root.rglob(pattern))
    except Exception as e:
        return f"Error during glob: {e}"
    if not matches:
        return "No files matched the pattern."
    if len(matches) > 200:
        matches = matches[:200]
        matches.append(f"... and {len(matches) - 200} more")
    return "\n".join(matches)


def _grep_handler(pattern, path=".", include=None, max_results=50):
    return asyncio.to_thread(_grep_sync, pattern, path, include, max_results)


def _grep_sync(pattern, path=".", include=None, max_results=50):
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return f"Error: Directory not found: {path}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"

    matches = []
    try:
        for p in root.rglob(include or "*"):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                logger.debug("Failed to read file for grep: %s", p)
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    line_out = line
                    if len(line_out) > 500:
                        line_out = line_out[:500] + "..."
                    matches.append(f"{p.relative_to(root)}:{i}:{line_out}")
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
    except Exception as e:
        return f"Error during grep: {e}"

    if not matches:
        return "No matches found."
    result = "\n".join(matches)
    if len(matches) >= max_results:
        result += f"\n... truncated at {max_results} results"
    return result


def _list_directory_handler(path=".", show_hidden=False):
    return asyncio.to_thread(_list_directory_sync, path, show_hidden)


def _list_directory_sync(path=".", show_hidden=False):
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return f"Error: Directory not found: {path}"
    try:
        entries = []
        for p in sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if not show_hidden and p.name.startswith("."):
                continue
            try:
                st = p.stat()
            except OSError:
                logger.debug("Failed to stat entry: %s", p)
                continue
            modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            if p.is_dir():
                kind = "dir"
                size = ""
            elif p.is_symlink():
                kind = "link"
                size = f"{st.st_size:,}"
            else:
                kind = "file"
                size = f"{st.st_size:,}"
            entries.append(f"{kind:6s} {modified}  {size:>10s}  {p.name}")
        if not entries:
            return f"{root}/ — (empty)"
        return f"{root}/ — {len(entries)} entries\n" + "\n".join(entries)
    except Exception as e:
        return f"Error listing directory: {e}"


async def _tree_handler(path=".", depth=None):
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        return f"Error: Directory not found: {path}"
    cmd = ["tree", "--charset=utf-8"]
    if depth is not None:
        cmd.extend(["-L", str(depth)])
    cmd.append(str(target))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return "Error: tree command not found. Install with: apt install tree"
    except OSError as e:
        return f"Error: Failed to run tree: {e}"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return "Error: tree command timed out."

    output = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
    if stderr_b:
        output += stderr_b.decode("utf-8", errors="replace")
    if not output.strip():
        return "(empty)"
    if len(output) > 100_000:
        output = output[:100_000] + "\n\n... [truncated at 100000 characters]"
    return output
