"""Tests for the builtin view_image tool and the manager's attachment queue."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from oli_bot.config import AppConfig
from oli_bot.models import ImageAttachment
from oli_bot.sessions import Session
from oli_bot.tools.manager import BuiltinToolManager


def _png_bytes(width: int = 32, height: int = 32, color=(255, 0, 0)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int = 32, height: int = 32) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (0, 128, 255)).save(buf, format="JPEG")
    return buf.getvalue()


def _manager(tmp_path: Path, offline: bool = False) -> BuiltinToolManager:
    config = AppConfig(offline_mode=offline)
    session = Session(workspace=tmp_path)
    return BuiltinToolManager(config=config, session=session)


# ---------- happy path -------------------------------------------------------


@pytest.mark.asyncio
async def test_view_image_local_png_attaches_and_returns_summary(tmp_path: Path):
    img_path = tmp_path / "hello.png"
    img_path.write_bytes(_png_bytes(64, 48))

    m = _manager(tmp_path)
    result = await m.call_tool("view_image", {"file_path": str(img_path)})

    assert "Loaded image" in result
    assert "PNG" in result
    assert "64x48" in result

    attachments, caption = m.drain_attachments()
    assert len(attachments) == 1
    att = attachments[0]
    assert isinstance(att, ImageAttachment)
    assert att.media_type == "image/png"
    assert att.width == 64
    assert att.height == 48
    assert att.data.startswith(b"\x89PNG")
    assert caption == ""


@pytest.mark.asyncio
async def test_view_image_jpeg_is_recognized(tmp_path: Path):
    img_path = tmp_path / "photo.jpg"
    img_path.write_bytes(_jpeg_bytes(80, 60))

    m = _manager(tmp_path)
    result = await m.call_tool("view_image", {"file_path": str(img_path)})

    assert "Loaded image" in result
    assert "JPEG" in result

    attachments, _ = m.drain_attachments()
    assert attachments[0].media_type == "image/jpeg"


# ---------- downscaling ------------------------------------------------------


@pytest.mark.asyncio
async def test_view_image_downscales_when_over_max_edge(tmp_path: Path):
    img_path = tmp_path / "big.png"
    img_path.write_bytes(_png_bytes(3000, 1500))

    m = _manager(tmp_path)
    result = await m.call_tool(
        "view_image", {"file_path": str(img_path), "max_edge_px": 512}
    )

    assert "Loaded image" in result
    attachments, _ = m.drain_attachments()
    att = attachments[0]
    assert max(att.width, att.height) == 512
    # Aspect ratio preserved (2:1)
    assert att.width == 512 and att.height == 256


@pytest.mark.asyncio
async def test_view_image_does_not_upscale_small_image(tmp_path: Path):
    img_path = tmp_path / "small.png"
    img_path.write_bytes(_png_bytes(100, 50))

    m = _manager(tmp_path)
    await m.call_tool("view_image", {"file_path": str(img_path), "max_edge_px": 4096})

    attachments, _ = m.drain_attachments()
    assert attachments[0].width == 100
    assert attachments[0].height == 50


# ---------- caption / question ----------------------------------------------


@pytest.mark.asyncio
async def test_view_image_question_becomes_pending_caption(tmp_path: Path):
    img_path = tmp_path / "q.png"
    img_path.write_bytes(_png_bytes())

    m = _manager(tmp_path)
    await m.call_tool(
        "view_image",
        {"file_path": str(img_path), "question": "What's in the whiteboard?"},
    )
    _, caption = m.drain_attachments()
    assert caption == "What's in the whiteboard?"


# ---------- error paths ------------------------------------------------------


@pytest.mark.asyncio
async def test_view_image_missing_file(tmp_path: Path):
    m = _manager(tmp_path)
    result = await m.call_tool(
        "view_image", {"file_path": str(tmp_path / "does_not_exist.png")}
    )
    assert result.startswith("Error: File not found")
    atts, _ = m.drain_attachments()
    assert atts == []


@pytest.mark.asyncio
async def test_view_image_non_image_file_rejected(tmp_path: Path):
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hello", encoding="utf-8")

    m = _manager(tmp_path)
    result = await m.call_tool("view_image", {"file_path": str(bogus)})
    assert result.startswith("Error")
    atts, _ = m.drain_attachments()
    assert atts == []


@pytest.mark.asyncio
async def test_view_image_oversize_file_rejected(tmp_path: Path, monkeypatch):
    from oli_bot.tools import files as files_mod

    monkeypatch.setattr(files_mod, "_VIEW_IMAGE_MAX_BYTES", 128)

    img_path = tmp_path / "huge.png"
    img_path.write_bytes(_png_bytes(200, 200))

    m = _manager(tmp_path)
    result = await m.call_tool("view_image", {"file_path": str(img_path)})
    assert "too large" in result


# ---------- URL branch: SSRF + offline gating --------------------------------


@pytest.mark.asyncio
async def test_view_image_url_blocked_when_offline(tmp_path: Path):
    m = _manager(tmp_path, offline=True)
    result = await m.call_tool(
        "view_image", {"file_path": "https://example.com/pic.png"}
    )
    assert "offline mode" in result
    atts, _ = m.drain_attachments()
    assert atts == []


@pytest.mark.asyncio
async def test_view_image_url_ssrf_blocks_loopback(tmp_path: Path):
    m = _manager(tmp_path, offline=False)
    result = await m.call_tool("view_image", {"file_path": "http://127.0.0.1/pic.png"})
    assert "SSRF" in result or "non-public" in result.lower()
    atts, _ = m.drain_attachments()
    assert atts == []


@pytest.mark.asyncio
async def test_view_image_url_ssrf_blocks_link_local_metadata(tmp_path: Path):
    m = _manager(tmp_path, offline=False)
    result = await m.call_tool(
        "view_image",
        {"file_path": "http://169.254.169.254/latest/meta-data/"},
    )
    assert "SSRF" in result or "non-public" in result.lower()
    atts, _ = m.drain_attachments()
    assert atts == []


@pytest.mark.asyncio
async def test_view_image_scheme_rejected(tmp_path: Path):
    m = _manager(tmp_path, offline=False)
    result = await m.call_tool("view_image", {"file_path": "file:///etc/passwd"})
    # file:// looks like a filesystem path once the URL check fails; the
    # handler should either say the file was not found or reject the scheme.
    # In practice it falls through to the path branch since the URL check
    # only matches http(s)://.
    assert result.startswith("Error")
    atts, _ = m.drain_attachments()
    assert atts == []


# ---------- attachment queue semantics ---------------------------------------


@pytest.mark.asyncio
async def test_drain_attachments_returns_empty_when_no_view_image(tmp_path: Path):
    m = _manager(tmp_path)

    async def handler():
        return "ok"

    m.register_tool("noop", "d", {"type": "object", "properties": {}}, handler)
    await m.call_tool("noop", {})

    atts, cap = m.drain_attachments()
    assert atts == []
    assert cap == ""


@pytest.mark.asyncio
async def test_call_tool_clears_queue_from_previous_call(tmp_path: Path):
    img_path = tmp_path / "a.png"
    img_path.write_bytes(_png_bytes())

    m = _manager(tmp_path)
    await m.call_tool("view_image", {"file_path": str(img_path)})
    # do NOT drain — simulate a caller that forgot

    async def handler():
        return "ok"

    m.register_tool("noop", "d", {"type": "object", "properties": {}}, handler)
    await m.call_tool("noop", {})

    atts, cap = m.drain_attachments()
    assert atts == []
    assert cap == ""
