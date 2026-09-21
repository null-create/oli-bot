"""Permission-gating matrix for Session."""

from pathlib import Path

import pytest

from oli_bot.sessions import (
    SCOPE_READ_OUTSIDE,
    SCOPE_UPLOAD,
    SCOPE_WORKSPACE_SENSITIVE,
    SCOPE_WRITE,
    Session,
)


def test_write_tools_require_permission(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission(
            "write_file", {"file_path": str(tmp_path / "a.txt"), "content": "x"}
        )
        == SCOPE_WRITE
    )
    assert (
        s.needs_permission(
            "edit_file",
            {
                "file_path": str(tmp_path / "a.txt"),
                "old_string": "a",
                "new_string": "b",
            },
        )
        == SCOPE_WRITE
    )


def test_read_inside_workspace_no_permission(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission("read_file", {"file_path": str(tmp_path / "a.txt")}) is None
    )


def test_read_outside_workspace_requires_permission(tmp_path):
    # Nest the workspace so we can point at a sibling path that is
    # guaranteed to resolve outside the workspace tree (avoids the
    # macOS /tmp -> /private/tmp symlink pitfall the previous version
    # of this test hedged around).
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sibling = tmp_path / "not_ws" / "a.txt"
    sibling.parent.mkdir()

    s = Session(workspace=workspace)
    assert (
        s.needs_permission("read_file", {"file_path": str(sibling)})
        == SCOPE_READ_OUTSIDE
    )


def test_read_without_workspace_requires_permission(tmp_path):
    s = Session(workspace=None)
    assert (
        s.needs_permission("read_file", {"file_path": str(tmp_path / "any.txt")})
        == SCOPE_READ_OUTSIDE
    )


def test_sensitive_file_in_workspace_requires_permission(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission("read_file", {"file_path": str(tmp_path / ".env")})
        == SCOPE_WORKSPACE_SENSITIVE
    )
    assert (
        s.needs_permission("read_file", {"file_path": str(tmp_path / "id_rsa.pem")})
        == SCOPE_WORKSPACE_SENSITIVE
    )


def test_glob_for_secrets_requires_permission(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission("glob", {"pattern": "**/*.pem", "path": str(tmp_path)})
        == SCOPE_WORKSPACE_SENSITIVE
    )
    assert (
        s.needs_permission(
            "grep", {"pattern": "AWS_SECRET", "path": str(tmp_path), "include": ".env*"}
        )
        == SCOPE_WORKSPACE_SENSITIVE
    )


def test_upload_requires_permission(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission(
            "upload_file",
            {"file_path": str(tmp_path / "a.txt"), "url": "https://ex.com/"},
        )
        == SCOPE_UPLOAD
    )


def test_session_grants_persist_within_scope(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission(
            "write_file", {"file_path": str(tmp_path / "a.txt"), "content": "x"}
        )
        == SCOPE_WRITE
    )
    s.grant(SCOPE_WRITE, session=True)
    assert (
        s.needs_permission(
            "write_file", {"file_path": str(tmp_path / "b.txt"), "content": "y"}
        )
        is None
    )


def test_view_image_inside_workspace_no_permission(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission("view_image", {"file_path": str(tmp_path / "a.png")}) is None
    )


def test_view_image_outside_workspace_requires_permission(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sibling = tmp_path / "not_ws" / "a.png"
    sibling.parent.mkdir()
    s = Session(workspace=workspace)
    assert (
        s.needs_permission("view_image", {"file_path": str(sibling)})
        == SCOPE_READ_OUTSIDE
    )


def test_view_image_url_skips_filesystem_gating(tmp_path):
    s = Session(workspace=tmp_path)
    assert (
        s.needs_permission("view_image", {"file_path": "https://example.com/pic.png"})
        is None
    )
    assert (
        s.needs_permission("view_image", {"file_path": "http://example.com/pic.png"})
        is None
    )
