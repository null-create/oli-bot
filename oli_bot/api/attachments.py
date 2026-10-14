"""Ingest browser file uploads delivered over the WebSocket chat frame.

Attachment bytes arrive base64-encoded in the ``attachments`` array of a
``/v1/chat`` turn. Image payloads become ``ImageAttachment`` vision inputs;
every file is also written to disk under an uploads directory so the agent can
re-open it later with ``read_file`` / ``view_image``.
"""

from __future__ import annotations

import base64
import binascii
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..config import AppConfig
from ..models import ImageAttachment

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 48 * 1024 * 1024

_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _uploads_root(config: AppConfig) -> Path:
    logs = Path(config.logs_dir).expanduser()
    base = logs if logs.is_absolute() else Path.cwd() / logs
    return base.resolve().parent / "uploads"


def _safe_segment(value: str) -> str:
    cleaned = "".join(c if c in _SAFE_NAME_CHARS else "_" for c in value).strip("._")
    return cleaned or "session"


def _safe_filename(name: str) -> str:
    base = Path(str(name)).name
    cleaned = "".join(c if c in _SAFE_NAME_CHARS else "_" for c in base).strip("._")
    return cleaned or "upload"


def _decode(att: Dict[str, Any]) -> bytes:
    data = att.get("data")
    if not isinstance(data, str) or not data:
        raise ValueError("attachment is missing base64 'data'")
    try:
        return base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"invalid base64 data: {e}") from e


def save_attachments(
    config: AppConfig,
    session_id: str,
    attachments: List[Any],
) -> Tuple[List[ImageAttachment], List[Tuple[str, str, Path]]]:
    """Persist each attachment and return the vision inputs plus saved paths.

    Returns ``(images, saved)`` where ``images`` are ``ImageAttachment``
    instances for ``image/*`` payloads and ``saved`` is a list of
    ``(display_name, media_type, path)`` tuples for the note appended to the
    user message. Raises ``ValueError`` for malformed or oversized payloads.
    """
    if not attachments:
        return [], []

    folder = _uploads_root(config) / _safe_segment(session_id or uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=True)

    images: List[ImageAttachment] = []
    saved: List[Tuple[str, str, Path]] = []
    total = 0
    for att in attachments:
        if not isinstance(att, dict):
            raise ValueError("attachment must be an object")
        media_type = str(att.get("media_type") or "application/octet-stream")
        name = _safe_filename(str(att.get("name") or "upload"))
        raw = _decode(att)
        if not raw:
            raise ValueError(f"attachment '{name}' is empty")
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(
                f"attachment '{name}' exceeds the "
                f"{MAX_FILE_BYTES // (1024 * 1024)} MB limit"
            )
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("attachments exceed the total upload limit")

        path = folder / f"{uuid.uuid4().hex[:8]}-{name}"
        try:
            path.write_bytes(raw)
        except OSError as e:
            raise ValueError(f"failed to save attachment '{name}': {e}") from e
        saved.append((name, media_type, path))
        if media_type.startswith("image/"):
            images.append(
                ImageAttachment(
                    data=raw,
                    media_type=media_type,
                    source_description=str(path),
                )
            )
    return images, saved


def attachment_note(
    saved: List[Tuple[str, str, Path]],
    images: List[ImageAttachment],
) -> str:
    """Build the bracketed note describing saved files appended to the message."""
    if not saved:
        return ""
    has_vision = bool(images)
    lines = ["[Attached files]"]
    for name, media_type, path in saved:
        if media_type.startswith("image/") and has_vision:
            lines.append(
                f"- {name} ({media_type}) saved to {path} — attached for visual analysis."
            )
        else:
            lines.append(
                f"- {name} ({media_type}) saved to {path} — use read_file to inspect."
            )
    return "\n".join(lines)


__all__ = [
    "save_attachments",
    "attachment_note",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
]
