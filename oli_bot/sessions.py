"""Conversation session persistence and permission management."""

import os
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .backends import Message

logger = logging.getLogger(__name__)

# --- Permission session ---

SCOPE_WRITE = "write"
SCOPE_READ_OUTSIDE = "read_outside"
SCOPE_UPLOAD = "upload"
SCOPE_WORKSPACE_SENSITIVE = "workspace_sensitive"

# --- Sensitive paths and files ---

_SENSITIVE_SYSTEM_DIRS = frozenset(
    {
        "/",
        "/etc",
        "/bin",
        "/sbin",
        "/usr",
        "/var",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
        "/root",
        "/home",
    }
)

_SENSITIVE_PATH_COMPONENTS = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".config",
        ".gnupg",
        "secrets",
        "credentials",
        "keys",
        "tokens",
        ".kube",
        ".docker",
        ".gitconfig",
    }
)


_SENSITIVE_FILE_NAMES: frozenset = frozenset(
    {
        ".env",
        ".gitconfig",
        ".netrc",
        ".npmrc",
        ".dockerconfigjson",
        ".hgrc",
    }
)

_SENSITIVE_FILE_EXTENSIONS: frozenset = frozenset(
    {
        ".pem",
        ".key",
        ".cert",
        ".crt",
        ".keystore",
        ".pkcs12",
        ".pfx",
        ".ovpn",
    }
)


@dataclass
class Session:
    workspace: Optional[Path] = None
    _session_grants: set[str] = field(default_factory=set)

    def needs_permission(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Optional[str]:
        path = self._extract_path(tool_name, arguments)
        if path is None:
            return None
        if tool_name in ("write_file", "edit_file", "download_file"):
            if SCOPE_WRITE not in self._session_grants:
                return SCOPE_WRITE
        elif tool_name in (
            "read_file",
            "view_image",
            "glob",
            "grep",
            "list_directory",
            "tree",
            "run_command",
            "git",
        ):
            if self.workspace is None:
                if SCOPE_READ_OUTSIDE not in self._session_grants:
                    return SCOPE_READ_OUTSIDE
            else:
                try:
                    path.relative_to(self.workspace)
                except ValueError:
                    if SCOPE_READ_OUTSIDE not in self._session_grants:
                        return SCOPE_READ_OUTSIDE
                else:
                    if tool_name == "read_file" and _is_sensitive_read_path(path):
                        if SCOPE_WORKSPACE_SENSITIVE not in self._session_grants:
                            return SCOPE_WORKSPACE_SENSITIVE
                    elif tool_name in (
                        "glob",
                        "grep",
                    ) and _looks_like_sensitive_pattern(arguments):
                        if SCOPE_WORKSPACE_SENSITIVE not in self._session_grants:
                            return SCOPE_WORKSPACE_SENSITIVE
        elif tool_name == "upload_file":
            if SCOPE_UPLOAD not in self._session_grants:
                return SCOPE_UPLOAD
        return None

    def grant(self, scope: str, session: bool = False) -> None:
        if session:
            self._session_grants.add(scope)

    def describe(self, tool_name: str, scope: str, arguments: Dict[str, Any]) -> str:
        path = self._extract_path(tool_name, arguments) or Path("?")
        if tool_name == "run_command":
            cmd = arguments.get("command", "?")
            return f"Run command: {cmd} (in {path})"
        if tool_name == "git":
            sub = arguments.get("subcommand", "?")
            return f"Run git {sub} in {path}"
        if tool_name == "download_file":
            url = arguments.get("url", "?")
            return f"Download from {url} to {path}"
        if tool_name == "upload_file":
            url = arguments.get("url", "?")
            return f"Upload {path} to {url}"
        if scope == SCOPE_WRITE:
            return f"Write to {path}"
        if scope == SCOPE_WORKSPACE_SENSITIVE:
            return f"Read sensitive file: {path}"
        suffix = " (outside workspace)" if self.workspace else " (no workspace set)"
        return f"Read from {path}{suffix}"

    @staticmethod
    def _extract_path(tool_name: str, arguments: Dict[str, Any]) -> Optional[Path]:
        path_str = None
        if tool_name in (
            "read_file",
            "view_image",
            "write_file",
            "edit_file",
            "download_file",
            "upload_file",
        ):
            path_str = arguments.get("file_path")
        elif tool_name in ("glob", "grep", "list_directory", "tree"):
            path_str = arguments.get("path", ".")
        elif tool_name == "run_command":
            path_str = arguments.get("workdir", ".")
        elif tool_name == "git":
            path_str = arguments.get("path", ".")
        if path_str:
            if tool_name == "view_image" and str(path_str).lower().startswith(
                ("http://", "https://")
            ):
                return None
            return Path(path_str).expanduser().resolve()
        return None


def _is_sensitive_read_path(path: Path) -> bool:
    """Check if reading a file at this path should require permission due to
    the file name, extension, keywords in the name, or directory location."""
    resolved = path.resolve()
    name = resolved.name

    if name == ".env" or name.startswith(".env."):
        return True
    if name in _SENSITIVE_FILE_NAMES:
        return True
    if any(name.endswith(ext) for ext in _SENSITIVE_FILE_EXTENSIONS):
        return True

    lower = name.lower()
    for keyword in ("secret", "credential", "password", "token"):
        if keyword in lower:
            return True

    parent_parts = resolved.parent.parts
    if any(part in _SENSITIVE_PATH_COMPONENTS for part in parent_parts):
        return True

    return False


def _looks_like_sensitive_pattern(arguments: Dict[str, Any]) -> bool:
    """Return True if a glob/grep call's ``pattern`` or ``include`` field
    matches keywords commonly associated with secrets, so the user is asked
    before a bulk read enumerates them.
    """
    candidates = [
        arguments.get("pattern", ""),
        arguments.get("include", ""),
    ]
    for value in candidates:
        if not isinstance(value, str) or not value:
            continue
        lower = value.lower()
        if any(
            kw in lower
            for kw in (
                ".env",
                "secret",
                "credential",
                "password",
                "token",
                ".pem",
                ".key",
                ".pfx",
            )
        ):
            return True
    return False


def is_sensitive_path(path: Path) -> bool:
    """Check if a resolved path is potentially sensitive for use as a workspace."""
    resolved = path.resolve()
    resolved_str = str(resolved)

    if resolved_str in _SENSITIVE_SYSTEM_DIRS:
        return True

    if resolved == Path.home():
        return True

    parts = resolved.parts
    if any(part in _SENSITIVE_PATH_COMPONENTS for part in parts):
        return True

    return False


# --- Conversation store ---


def _sanitize_name(name: str) -> str:
    return (
        "".join(c if c.isalnum() or c in "_-" else "_" for c in name).strip("_")
        or "unnamed"
    )


def _message_to_dict(msg: Message) -> dict:
    d = {"role": msg.role, "content": msg.content}
    if msg.tool_calls is not None:
        d["tool_calls"] = msg.tool_calls
    if msg.name is not None:
        d["name"] = msg.name
    if msg.timestamp is not None:
        d["timestamp"] = msg.timestamp
    if msg.tool_call_id is not None:
        d["tool_call_id"] = msg.tool_call_id
    if msg.images:
        logger.debug(
            "Dropping %d image attachment(s) from persisted message (role=%s)",
            len(msg.images),
            msg.role,
        )
    return d


def _message_from_dict(d: dict) -> Message:
    return Message(
        role=d["role"],
        content=d.get("content", ""),
        tool_calls=d.get("tool_calls"),
        name=d.get("name"),
        timestamp=d.get("timestamp"),
        tool_call_id=d.get("tool_call_id"),
    )


class ConversationStore:
    """Persist and manage conversation sessions as JSON files."""

    def __init__(self, sessions_dir: str | None = None):
        if sessions_dir is None:
            sessions_dir = str(Path.home() / ".config" / "oli" / "sessions")
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _server_dir(self, server: str) -> Path:
        return self.sessions_dir / _sanitize_name(server)

    def _session_path(self, server: str, session_id: str) -> Path:
        return self._server_dir(server) / f"{session_id}.json"

    def create_session(
        self, server: str, model: str, profile: str, system_prompt: str
    ) -> str:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        name = f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        data = {
            "id": session_id,
            "name": name,
            "created_at": now,
            "updated_at": now,
            "server": server,
            "model": model or "",
            "profile": profile or "",
            "messages": [],
        }
        if system_prompt:
            data["messages"].append({"role": "system", "content": system_prompt})
        server_dir = self._server_dir(server)
        server_dir.mkdir(parents=True, exist_ok=True)
        self._session_path(server, session_id).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        return session_id

    def save_session(
        self,
        server: str,
        session_id: str,
        messages: List[Message],
        model: str,
        profile: str,
    ) -> str:
        """Persist a session; return the (possibly new) session id.

        If the target file is missing or corrupt, a fresh session is created
        under a new UUID and its id is returned so the caller can update its
        state and avoid creating a new file on every subsequent save.
        """
        path = self._session_path(server, session_id)
        created_new = False
        if not os.path.exists(path):
            logger.warning(
                "Session %s not found at %s, creating new session", session_id, path
            )
            session_id = self.create_session(server, model, profile, "")
            path = self._session_path(server, session_id)
            created_new = True
            data = {
                "id": session_id,
                "name": f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "server": server,
                "model": model or "",
                "profile": profile or "",
            }
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load session %s: %s", session_id, e)
                session_id = self.create_session(server, model, profile, "")
                path = self._session_path(server, session_id)
                created_new = True
                data = {
                    "id": session_id,
                    "name": f"Session {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "server": server,
                    "model": model or "",
                    "profile": profile or "",
                }
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["model"] = model or data.get("model", "")
        data["profile"] = profile or data.get("profile", "")
        data["messages"] = [_message_to_dict(m) for m in messages]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if created_new:
            logger.info("Session recreated under new id: %s", session_id)
        return session_id

    def load_session(self, server: str, session_id: str) -> Optional[dict]:
        path = self._session_path(server, session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load session %s: %s", session_id, e)
            return None

    def list_sessions(self, server: str) -> List[dict]:
        server_dir = self._server_dir(server)
        if not server_dir.is_dir():
            return []
        sessions = []
        for f in sorted(
            server_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    sessions.append(
                        {
                            "id": data.get("id", f.stem),
                            "name": data.get("name", f.stem),
                            "created_at": data.get("created_at", ""),
                            "updated_at": data.get("updated_at", ""),
                            "msg_count": len(data.get("messages", [])),
                        }
                    )
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Failed to read session %s: %s", f.name, e)
        return sessions

    def delete_session(self, server: str, session_id: str) -> bool:
        path = self._session_path(server, session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def rename_session(self, server: str, session_id: str, name: str) -> bool:
        path = self._session_path(server, session_id)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["name"] = name
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to rename session %s: %s", session_id, e)
            return False

    def purge_server_sessions(self, server: str) -> int:
        server_dir = self._server_dir(server)
        if not server_dir.is_dir():
            return 0
        count = 0
        for f in list(server_dir.iterdir()):
            if f.suffix == ".json":
                f.unlink()
                count += 1
        try:
            server_dir.rmdir()
        except OSError:
            logger.debug("Failed to remove sessions directory: %s", server_dir)
        return count

    def purge_all_sessions(self) -> int:
        count = 0
        for server_dir in self.sessions_dir.iterdir():
            if not server_dir.is_dir():
                continue
            for f in list(server_dir.iterdir()):
                if f.suffix == ".json":
                    f.unlink()
                    count += 1
            try:
                server_dir.rmdir()
            except OSError:
                logger.debug("Failed to remove sessions directory: %s", server_dir)
        return count

    def get_last_session(self, server: str) -> Optional[str]:
        sessions = self.list_sessions(server)
        if sessions:
            return sessions[0]["id"]
        return None


# --- Workspace manager ---


class WorkspaceManager:
    """Persist and recall recently used workspaces."""

    MAX_WORKSPACES = 20

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path.home() / ".local" / "share" / "oli"
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._data_dir / "workspaces.json"
        self._workspaces: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._workspaces = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._workspaces = data.get("workspaces", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load workspaces: %s", e)
            self._workspaces = []

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps({"workspaces": self._workspaces}, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to save workspaces: %s", e)

    def add_workspace(self, path: Path) -> None:
        resolved = str(path.resolve())
        self._workspaces = [resolved] + [w for w in self._workspaces if w != resolved]
        self._workspaces = self._workspaces[: self.MAX_WORKSPACES]
        self._save()

    def list_workspaces(self) -> list[Path]:
        return [Path(w) for w in self._workspaces]

    def remove_workspace(self, path: Path) -> None:
        resolved = str(path.resolve())
        self._workspaces = [w for w in self._workspaces if w != resolved]
        self._save()
