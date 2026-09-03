from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Optional

from .manager import BuiltinToolManager


# Filesystem read + navigation (no writers here — see DENIED_ARGS for `find -delete` etc.).
_ALLOWED_FILESYSTEM_READ: frozenset[str] = frozenset(
    {
        "ls",
        "find",
        "locate",
        "tree",
        "pwd",
        "realpath",
        "readlink",
        "df",
        "du",
        "stat",
        "file",
        "basename",
        "dirname",
    }
)

_ALLOWED_TEXT_SEARCH: frozenset[str] = frozenset(
    {
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "ack",
    }
)

# Read-only VCS inspection. Mutating subcommands are blocked via DENIED_ARGS below.
_ALLOWED_VCS: frozenset[str] = frozenset({"git"})

# Text/data manipulation. In-place edit flags are blocked in DENIED_ARGS.
_ALLOWED_TEXT_PROCESSING: frozenset[str] = frozenset(
    {
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "cut",
        "tr",
        "fold",
        "nl",
        "column",
        "paste",
        "join",
        "sed",
        "gsed",
        "awk",
        "gawk",
        "nawk",
        "xargs",
        "tee",
        "jq",
        "yq",
        "diff",
        "cmp",
        "comm",
        "strings",
        "hexdump",
        "od",
        "xxd",
    }
)

_ALLOWED_ENCODING_COMPRESSION: frozenset[str] = frozenset(
    {
        "md5sum",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "shasum",
        "b2sum",
        "base64",
        "zcat",
        "gzcat",
        "bzcat",
        "xzcat",
        "zgrep",
        "zless",
    }
)

_ALLOWED_UTILITIES: frozenset[str] = frozenset(
    {
        "echo",
        "printf",
        "seq",
        "date",
        "cal",
        "expr",
        "which",
        "getconf",
        "whatis",
        "apropos",
        "tput",
        "stty",
        "tty",
    }
)

_ALLOWED_SYSTEM_INFO: frozenset[str] = frozenset(
    {
        "ps",
        "top",
        "htop",
        "pgrep",
        "uname",
        "hostname",
        "whoami",
        "id",
        "groups",
        "who",
        "w",
        "uptime",
        "free",
        "lsblk",
        "lscpu",
        "lsmem",
        "nproc",
        "arch",
        "ss",
        "netstat",
    }
)

# Interpreters and package managers. Sandboxing is advisory for these binaries —
# `python -c '…'`, `node -e '…'`, `pytest` fixtures etc. all execute arbitrary code by design.
_ALLOWED_RUNTIME_COMMANDS: frozenset[str] = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "node",
        "npm",
        "npx",
        "ruby",
        "go",
        "cargo",
        "rustc",
        "deno",
        "bun",
        "perl",
        "lua",
        "pip",
        "pip3",
        "uv",
        "poetry",
        "pipx",
    }
)

# ``xargs`` flags that consume a value argument (so we skip past it when
# scanning for the inner command).
_XARGS_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-n", "-P", "-I", "-i", "-L", "-s", "-E", "-d", "-a", "-J", "-R", "-S"}
)


ALLOWED_COMMANDS: frozenset[str] = (
    _ALLOWED_FILESYSTEM_READ
    | _ALLOWED_TEXT_SEARCH
    | _ALLOWED_TEXT_PROCESSING
    | _ALLOWED_ENCODING_COMPRESSION
    | _ALLOWED_UTILITIES
    | _ALLOWED_SYSTEM_INFO
    | _ALLOWED_RUNTIME_COMMANDS
    | _ALLOWED_VCS
)

# Per-command argument denylist. Any occurrence of a denied argument in the
# tokenized command triggers a rejection. Keys are the command basename; values
# are the substrings/tokens that must not appear anywhere in that command's
# argv. This blocks known escape hatches inside otherwise-allowlisted binaries.
DENIED_ARGS: dict[str, tuple[str, ...]] = {
    "find": (
        "-exec",
        "-execdir",
        "-delete",
        "-fprint",
        "-fprintf",
        "-fls",
        "-ok",
        "-okdir",
    ),
    "grep": ("--include-from",),  # not exhaustive; grep is largely safe
    # In-place edit modes turn read-only text tools into arbitrary file writers.
    "sed": ("-i", "--in-place"),
    "gsed": ("-i", "--in-place"),
    # -f loads an unaudited script file; -i enables in-place edit (gawk).
    "awk": ("-i", "--in-place", "-f", "--file"),
    "gawk": ("-i", "--in-place", "-f", "--file"),
    "nawk": ("-i", "--in-place", "-f", "--file"),
    # Mutating/state-changing subcommands; status/diff/log/show/blame/grep/etc. remain usable.
    "git": (
        "push",
        "pull",
        "fetch",
        "commit",
        "reset",
        "checkout",
        "restore",
        "clean",
        "rebase",
        "merge",
        "cherry-pick",
        "revert",
        "add",
        "rm",
        "mv",
        "stash",
        "tag",
        "branch",
        "remote",
        "config",
        "submodule",
        "apply",
        "am",
        "gc",
        "reflog",
        "worktree",
        "init",
        "clone",
    ),
}

# Commands whose in-place edit or scripting flags have a prefix form we must
# also reject (e.g. ``sed -i.bak``, ``awk --in-place=.bak``).
DENIED_ARG_PREFIXES: dict[str, tuple[str, ...]] = {
    "sed": ("-i", "--in-place="),
    "gsed": ("-i", "--in-place="),
    "awk": ("--in-place=",),
    "gawk": ("--in-place=",),
    "nawk": ("--in-place=",),
}

# Characters that must never appear anywhere in the raw command string, quoted
# or not, because they can smuggle newlines/whitespace past the segment split.
_FORBIDDEN_RAW_CHARS: frozenset[str] = frozenset(
    {
        "\r",
        "\x0b",
        "\x0c",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\u0085",
        "\u2028",
        "\u2029",
    }
)

# Redirect targets that never touch the filesystem, so they bypass workspace scoping.
_FD_DUP_RE = re.compile(r"^&(?:\d+|-)$")
_ALLOWED_DEVICE_TARGETS: frozenset[str] = frozenset(
    {"/dev/null", "/dev/stderr", "/dev/stdout"}
)


def register_tools(manager: BuiltinToolManager) -> None:
    async def run_command_handler(command, timeout=30, workdir=None):
        workspace = manager._session.workspace if manager._session else None
        return await _run_command_handler(
            command, timeout=timeout, workdir=workdir, workspace=workspace
        )

    manager.register_tool(
        name="run_command",
        description="Run a shell command and return its output. "
        "Use this for text processing (grep, sed, awk, jq), listing files (ls, find), "
        "reading files (cat, head, tail), process info (ps), disk usage (df, du), "
        "running tests / scripts via language runtimes, and other CLI tasks. "
        "Allowlist highlights: grep/rg/ag, sed, awk, xargs, jq/yq, tee, find, ls, cat, "
        "head, tail, wc, sort, uniq, cut, tr, ps, df, du, diff, file, stat, sha*sum, "
        "base64, echo, pwd. "
        "Language runtimes: python/python3/pytest, node/npm/npx, ruby, go, cargo/rustc, "
        "deno, bun, perl, lua, plus pip/pip3/uv/poetry/pipx — e.g. `python -m pytest tests/`, "
        "`pytest -q`, `node script.js`. "
        "Pipes (|) and logical operators (&&, ||) are allowed and encouraged: "
        "e.g. `grep -rn TODO src/ | head -20`, `find . -name '*.py' | xargs grep -l TODO`, "
        "`cat data.json | jq '.items[].name'`. "
        "When using `xargs -I` placeholders, quote them: `xargs -I '{}' cat '{}'`. "
        "Use the `workdir` parameter to change directory — `cd` is not persistent since "
        "each command runs in a fresh subshell. "
        "Output redirects (>, >>) to real files are allowed **only** when an active "
        "workspace is set and the target resolves inside it (`/workspace set` first). "
        "Stderr redirects `2>&1` / `2>&-` (fd duplication/close) and writes to "
        "`/dev/null`, `/dev/stderr`, `/dev/stdout` are always allowed. "
        "`git` is allowed for read-only inspection (status, diff, log, show, blame, "
        "grep, etc.) — mutating subcommands (push, pull, fetch, commit, reset, checkout, "
        "restore, clean, rebase, merge, cherry-pick, revert, add, rm, mv, stash, tag, "
        "branch, remote, config, submodule, apply, am, gc, reflog, worktree, init, clone) "
        "are blocked. "
        "Blocked: input redirects (<), subshells `()`, backticks/`$()`, sed/awk in-place "
        "edit (`-i`), awk `-f` script files, `xargs` invoking a non-allowlisted command, "
        "and `find -exec`/`-delete` (use `xargs` with an allowlisted command instead).",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute (e.g., 'ls -la', 'grep -rn \"TODO\" src/').",
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution time in seconds. Defaults to 30.",
                    "default": 30,
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for the command. Defaults to current directory.",
                },
            },
            "required": ["command"],
        },
        handler=run_command_handler,
    )


def _segments(command: str) -> list[str]:
    """Split on |, ||, |&, &&, ; — but only when outside of quotes."""
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]

        # Honour backslash escapes outside single quotes (same logic as
        # _has_unquoted_dangerous_chars so the two functions stay in sync).
        if c == "\\" and not in_single and i + 1 < len(command):
            current.append(c)
            current.append(command[i + 1])
            i += 2
            continue

        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double

        if not in_single and not in_double:
            # Check two-char separators before single-char ones.
            two = command[i : i + 2]
            if two in ("|&", "||", "&&"):
                segments.append("".join(current).strip())
                current = []
                i += 2
                continue
            if c in (";", "|"):
                segments.append("".join(current).strip())
                current = []
                i += 1
                continue

        current.append(c)
        i += 1

    if current:
        segments.append("".join(current).strip())
    return [s for s in segments if s]


def _has_unquoted_dangerous_chars(segment: str) -> Optional[str]:
    i = 0
    in_single = False
    in_double = False

    while i < len(segment):
        c = segment[i]

        if c == "\\" and not in_single:
            # A backslash followed by a newline is a shell line-continuation
            # that would let the caller sneak an unallowlisted second command
            # through. Reject anything of that form; otherwise skip the
            # escaped char as before.
            if i + 1 < len(segment) and segment[i + 1] in ("\n", "\r"):
                return "Error: Line-continuations (\\ + newline) are not allowed."
            i += 2
            continue

        if c == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue

        if c == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue

        if in_single or in_double:
            i += 1
            continue

        if c in ("$", "`", "{", "}", "!"):
            return "Error: Shell metacharacters ($, `, {}, !) are not allowed."

        if c in ("(", ")"):
            return "Error: Subshell operators () are not allowed."

        # ``>`` and ``>>`` are now conditionally allowed: the redirect target is
        # extracted and validated against the active workspace in
        # ``_check_redirect_targets``. Bare ``<`` (input redirect from a file)
        # remains disallowed since it does not add expressive power over cat|.
        if c == "<":
            return (
                "Error: Input redirects (<) are not allowed. Pipe from `cat` instead."
            )

        if c == "\n":
            return "Error: Literal newlines are not allowed."

        i += 1

    if in_single or in_double:
        return "Error: Unmatched quote."

    return None


def _tokenize_with_redirects(segment: str) -> list[str]:
    """Tokenize a shell segment, splitting ``>`` and ``>>`` as their own tokens."""
    lexer = shlex.shlex(segment, posix=True, punctuation_chars=">")
    lexer.whitespace_split = True
    return list(lexer)


def _check_command_argv(tokens: list[str]) -> Optional[str]:
    """Validate the argv of a single command (no redirects) against the allowlist.

    Applied recursively to ``xargs``' inner command so ``xargs rm`` still fails.
    """
    if not tokens:
        return "Error: Empty command segment."

    first = tokens[0].lstrip("./")
    base = Path(first).name
    if base not in ALLOWED_COMMANDS:
        return f"Error: Command '{base}' is not in the allowlist."

    denied = DENIED_ARGS.get(base)
    if denied:
        for arg in tokens[1:]:
            if arg in denied:
                return (
                    f"Error: Argument '{arg}' is not allowed for '{base}' "
                    f"(escape-hatch protection)."
                )

    denied_prefixes = DENIED_ARG_PREFIXES.get(base)
    if denied_prefixes:
        for arg in tokens[1:]:
            for prefix in denied_prefixes:
                if arg.startswith(prefix):
                    return (
                        f"Error: Argument '{arg}' is not allowed for '{base}' "
                        f"(escape-hatch protection)."
                    )

    if base == "xargs":
        inner = _extract_xargs_inner_command(tokens[1:])
        if inner is not None:
            err = _check_command_argv(inner)
            if err is not None:
                return (
                    f"Error: xargs cannot invoke '{inner[0]}': {err[len('Error: '):]}"
                )
    return None


def _extract_xargs_inner_command(args: list[str]) -> Optional[list[str]]:
    """Return the argv that xargs would exec, or None if it defaults to echo."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a in _XARGS_VALUE_FLAGS:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            i += 1
            continue
        break
    if i >= len(args):
        return None
    return args[i:]


def _check_redirect_targets(
    tokens: list[str], workspace: Optional[Path]
) -> Optional[str]:
    """Validate that every ``>``/``>>`` in ``tokens`` writes inside ``workspace``."""
    for idx, tok in enumerate(tokens):
        if tok not in (">", ">>"):
            continue
        if idx + 1 >= len(tokens):
            return "Error: Missing redirect target after '{}'.".format(tok)
        target = tokens[idx + 1]
        if target in (">", ">>", "|"):
            return "Error: Invalid redirect target '{}'.".format(target)
        # fd duplication/close (`2>&1`, `2>&-`) never touches the filesystem.
        if _FD_DUP_RE.match(target):
            continue
        # Device targets discard or re-emit to the caller's own std streams.
        if target in _ALLOWED_DEVICE_TARGETS:
            continue
        if workspace is None:
            return (
                "Error: Output redirects (>, >>) require an active workspace. "
                "Use `/workspace set <path>` first, or pipe through `tee` inside "
                "the workspace."
            )
        try:
            resolved = Path(target).expanduser().resolve()
            resolved.relative_to(workspace.resolve())
        except (ValueError, OSError):
            return (
                f"Error: Redirect target '{target}' is outside the active "
                f"workspace ({workspace})."
            )
    return None


def _is_command_allowed(
    command: str, workspace: Optional[Path] = None
) -> Optional[str]:
    stripped = command.strip()
    if not stripped:
        return "Error: Empty command."

    for ch in _FORBIDDEN_RAW_CHARS:
        if ch in stripped:
            return (
                "Error: Command contains a forbidden whitespace or control "
                "character (CR, VT, FF, NEL, LS, PS)."
            )

    segments = _segments(stripped)
    if not segments:
        return "Error: Empty command."

    for seg in segments:
        error = _has_unquoted_dangerous_chars(seg)
        if error:
            return error

        try:
            tokens = _tokenize_with_redirects(seg)
        except ValueError as e:
            return f"Error: Invalid shell syntax: {e}"

        if not tokens:
            return "Error: Empty command segment."

        redirect_error = _check_redirect_targets(tokens, workspace)
        if redirect_error:
            return redirect_error

        # Strip redirect operator+target pairs before validating the command.
        argv: list[str] = []
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in (">", ">>"):
                skip_next = True
                continue
            argv.append(tok)

        err = _check_command_argv(argv)
        if err is not None:
            return err

    return None


async def _run_command_handler(command, timeout=30, workdir=None, workspace=None):
    not_allowed = _is_command_allowed(command, workspace=workspace)
    if not_allowed:
        return not_allowed

    cwd = Path(workdir).expanduser().resolve() if workdir else None
    if cwd and not cwd.is_dir():
        return f"Error: Working directory does not exist: {workdir}"

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as e:
        return f"Error: Command not found: {e}"
    except OSError as e:
        return f"Error: Failed to execute command: {e}"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return f"Error: Command timed out after {timeout} seconds."

    stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
    stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""

    output = f"Exit code: {proc.returncode}\n"
    if stdout:
        output += stdout
    if stderr:
        output += stderr

    if len(output) > 100_000:
        output = output[:100_000] + "\n\n... [truncated at 100000 characters]"
    return output
