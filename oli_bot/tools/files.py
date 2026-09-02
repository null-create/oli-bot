from __future__ import annotations

import asyncio
import io
import logging
import random
from pathlib import Path


from .web import _check_ssrf, _FETCH_USER_AGENTS
from .manager import BuiltinToolManager
from ..models import ImageAttachment

import httpx
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

_VIEW_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_VIEW_IMAGE_DEFAULT_MAX_EDGE = 1568
_VIEW_IMAGE_FETCH_TIMEOUT = 15

_PIL_FORMAT_TO_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
}


def register_tools(manager: BuiltinToolManager) -> None:
    manager.register_tool(
        name="read_file",
        description="Read the contents of a file from the local filesystem. "
        "Use this to examine source code, configuration files, logs, or any other text file.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read. Relative paths are resolved from the current working directory.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Optional character offset to start reading from. Must be zero or positive.",
                    "minimum": 0,
                },
                "length": {
                    "type": "integer",
                    "description": "Optional maximum number of characters to read from the offset.",
                    "minimum": 1,
                },
            },
            "required": ["file_path"],
        },
        handler=_read_file_handler,
    )

    manager.register_tool(
        name="write_file",
        description="Write content to a file. "
        "Creates parent directories if they don't exist. "
        "Use this to generate code, create configuration files, "
        "save notes, or modify project files. "
        "Maximum content length is 100,000 characters.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write. Relative paths are resolved from the current working directory.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["file_path", "content"],
        },
        handler=_write_file_handler,
    )

    manager.register_tool(
        name="edit_file",
        description="Perform a surgical find-and-replace edit on a file. "
        "Replaces the first occurrence of old_string with new_string. "
        "If old_string is found multiple times, the call will fail and "
        "ask you to provide more surrounding context for a unique match. "
        "Use this to make targeted modifications to existing code or "
        "configuration files without rewriting the entire file.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to replace. Must be unique in the file — include surrounding lines for disambiguation if needed.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text.",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        handler=_edit_file_handler,
    )

    manager.register_tool(
        name="view_image",
        description=(
            "Load an image file (or http(s) URL) and attach it for visual analysis. "
            "The image is passed to the model as vision input on backends that "
            "support it (Ollama vision models, OpenAI GPT-4o, etc.); text-only "
            "backends receive a description of the attachment instead. "
            "Use this to inspect screenshots, diagrams, photos, or any image "
            "the user wants analyzed. Supports PNG, JPEG, GIF, BMP, WEBP."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Absolute path to the image file, or an http(s) URL. "
                        "Relative paths are resolved from the current working directory."
                    ),
                },
                "max_edge_px": {
                    "type": "integer",
                    "description": (
                        "Optional maximum edge length in pixels. Larger images are "
                        "downscaled to reduce token cost. Defaults to 1568."
                    ),
                    "minimum": 64,
                },
                "question": {
                    "type": "string",
                    "description": (
                        "Optional question or instruction that becomes the caption "
                        "on the follow-up user message carrying the image. "
                        "Improves results on vision models."
                    ),
                },
            },
            "required": ["file_path"],
        },
        handler=lambda file_path, max_edge_px=None, question="": _view_image_handler(
            file_path, manager, max_edge_px=max_edge_px, question=question
        ),
    )


def _read_file_handler(file_path: str, offset: int = None, length: int = None) -> str:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return f"Error: File not found: {file_path}"
    if not path.is_file():
        return f"Error: Not a file: {file_path}"
    if offset is not None:
        if not isinstance(offset, int) or isinstance(offset, bool):
            return "Error: offset must be an integer."
        if offset < 0:
            return "Error: offset must be zero or positive."
    if length is not None:
        if not isinstance(length, int) or isinstance(length, bool):
            return "Error: length must be an integer."
        if length <= 0:
            return "Error: length must be a positive integer."
    try:
        content = path.read_text(encoding="utf-8")
        if offset is not None or length is not None:
            start = offset or 0
            if start >= len(content):
                return ""
            end = start + length if length is not None else None
            return content[start:end]
        return content
    except UnicodeDecodeError:
        return f"Error: File is not valid UTF-8 text: {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"


def _write_file_handler(file_path: str, content: str) -> str:
    path = Path(file_path).expanduser().resolve()
    if len(content) > 100_000:
        return (
            f"Error: Content exceeds maximum length of 100,000 characters "
            f"({len(content)} given)."
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def _edit_file_handler(file_path: str, old_string: str, new_string: str) -> str:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return f"Error: File not found: {file_path}"
    if not path.is_file():
        return f"Error: Not a file: {file_path}"
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"
    if old_string not in content:
        return f"Error: old_string not found in {file_path}"
    count = content.count(old_string)
    if count > 1:
        return (
            f"Error: Found {count} matches for old_string in {file_path}. "
            f"Provide more surrounding context in old_string to uniquely "
            f"identify the match."
        )
    new_content = content.replace(old_string, new_string)
    try:
        path.write_text(new_content, encoding="utf-8")
        return f"Successfully applied edit to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def _view_image_handler(
    file_path: str,
    manager: BuiltinToolManager,
    max_edge_px: int | None = None,
    question: str = "",
) -> str:
    if max_edge_px is None or not isinstance(max_edge_px, int) or max_edge_px < 64:
        max_edge_px = _VIEW_IMAGE_DEFAULT_MAX_EDGE

    is_url = isinstance(file_path, str) and file_path.lower().startswith(
        ("http://", "https://")
    )

    if is_url:
        if manager._config.offline_mode:
            return (
                "Error: Network access blocked by offline mode. "
                "Use /config to disable offline mode, or restart without --offline."
            )
        raw, source_desc, fetch_err = await _fetch_image_bytes(file_path)
        if fetch_err:
            return fetch_err
    else:
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return f"Error: File not found: {file_path}"
        if not path.is_file():
            return f"Error: Not a file: {file_path}"
        try:
            size = path.stat().st_size
        except OSError as e:
            return f"Error: Could not stat file: {e}"
        if size > _VIEW_IMAGE_MAX_BYTES:
            return (
                f"Error: Image is too large ({size} bytes); "
                f"limit is {_VIEW_IMAGE_MAX_BYTES} bytes."
            )
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except Exception as e:
            return f"Error reading image: {e}"
        source_desc = str(path)

    try:
        data, media_type, width, height, orig_fmt = await asyncio.to_thread(
            _decode_and_downscale, raw, max_edge_px
        )
    except UnidentifiedImageError:
        return f"Error: Not a recognizable image format: {source_desc}"
    except Exception as e:
        logger.exception("view_image decoding failed for %s", source_desc)
        return f"Error decoding image: {e}"

    manager.attach_image(
        ImageAttachment(
            data=data,
            media_type=media_type,
            source_description=source_desc,
            width=width,
            height=height,
        )
    )
    if question:
        manager.set_pending_caption(question)

    size_kb = len(data) / 1024
    return (
        f"Loaded image {source_desc} ({orig_fmt}, {width}x{height}, "
        f"{size_kb:.1f} KB after processing). Attached for analysis."
    )


def _decode_and_downscale(raw: bytes, max_edge_px: int):
    img = Image.open(io.BytesIO(raw))
    img.load()
    orig_fmt = (img.format or "").upper() or "UNKNOWN"
    if orig_fmt not in _PIL_FORMAT_TO_MIME:
        raise ValueError(f"Unsupported image format: {orig_fmt}")

    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")

    max_side = max(img.size)
    if max_side > max_edge_px:
        scale = max_edge_px / max_side
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    out_fmt = "PNG" if orig_fmt in ("PNG", "GIF", "BMP", "TIFF") else "JPEG"
    if out_fmt == "JPEG" and img.mode == "RGBA":
        img = img.convert("RGB")

    buf = io.BytesIO()
    if out_fmt == "JPEG":
        img.save(buf, format="JPEG", quality=85, optimize=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    media_type = _PIL_FORMAT_TO_MIME[out_fmt]
    return data, media_type, img.width, img.height, orig_fmt


async def _fetch_image_bytes(url: str) -> tuple[bytes, str, str | None]:
    """Fetch an image over HTTP(S) through the SSRF-safe guard.

    Returns ``(data, source_desc, error)``. On failure ``error`` is the
    caller-safe error string and ``data`` is empty.
    """
    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return b"", url, ssrf_err

    headers = {
        "User-Agent": random.choice(_FETCH_USER_AGENTS),
        "Accept": "image/*",
    }
    try:
        async with httpx.AsyncClient(
            timeout=_VIEW_IMAGE_FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return b"", url, f"Error: HTTP {e.response.status_code} — {url}"
    except httpx.RequestError as e:
        return b"", url, f"Error: Fetch failed — {url} ({e})"
    except Exception as e:
        return b"", url, f"Error: Failed to fetch image — {e}"

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if content_type and not content_type.lower().startswith("image/"):
        return (
            b"",
            url,
            (f"Error: URL did not return an image (Content-Type: {content_type})."),
        )
    if len(response.content) > _VIEW_IMAGE_MAX_BYTES:
        return (
            b"",
            url,
            (
                f"Error: Image is too large ({len(response.content)} bytes); "
                f"limit is {_VIEW_IMAGE_MAX_BYTES} bytes."
            ),
        )
    return response.content, url, None
