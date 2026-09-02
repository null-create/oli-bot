"""ProfilePermissionEnforcer layered enforcement matrix.

Covers: allow-list, deny-list, wildcard globbing, deny-overrides-allow at a
single layer, and child-vs-base intersection where BOTH layers must allow.
"""

from oli_bot.profiles.permissions import ProfilePermissionEnforcer
from oli_bot.profiles.schema import PermissionsManifest, ProfileManifest


def _manifest(name: str, allow=None, deny=None) -> ProfileManifest:
    return ProfileManifest(
        name=name,
        permissions=PermissionsManifest(
            allow_tools=list(allow or []),
            deny_tools=list(deny or []),
        ),
    )


# ---------- single-layer enforcement ----------------------------------------


def test_allow_missing_from_list_returns_false():
    enf = ProfilePermissionEnforcer(_manifest("p", allow=["builtin__read_file"]))
    assert enf.check_tool("builtin__read_file") is True
    assert enf.check_tool("builtin__write_file") is False


def test_deny_takes_precedence_over_allow_at_same_layer():
    """Regression: deny must win against an equally specific allow entry."""
    enf = ProfilePermissionEnforcer(
        _manifest(
            "p",
            allow=["builtin__write_file"],
            deny=["builtin__write_file"],
        )
    )
    assert enf.check_tool("builtin__write_file") is False


def test_wildcard_allow_admits_builtins():
    enf = ProfilePermissionEnforcer(_manifest("p", allow=["builtin__*"]))
    assert enf.check_tool("builtin__read_file") is True
    assert enf.check_tool("builtin__git") is True
    # No allow entry matches non-builtin tools.
    assert enf.check_tool("mcp__some_server__foo") is False


def test_wildcard_deny_blocks_specific_tool_even_when_allowed():
    """`builtin__*` allow list must not override a `builtin__git` deny."""
    enf = ProfilePermissionEnforcer(
        _manifest(
            "p",
            allow=["builtin__*"],
            deny=["builtin__git"],
        )
    )
    assert enf.check_tool("builtin__read_file") is True
    assert enf.check_tool("builtin__git") is False


def test_empty_manifest_denies_everything():
    enf = ProfilePermissionEnforcer(_manifest("p"))
    assert enf.check_tool("builtin__read_file") is False
    assert enf.check_tool("anything") is False


# ---------- layered (base + child) enforcement -------------------------------


def test_base_and_child_must_both_allow():
    base = ProfilePermissionEnforcer(_manifest("base", allow=["builtin__*"]))
    child = ProfilePermissionEnforcer(
        _manifest("child", allow=["builtin__read_file"]),
        base_enforcer=base,
    )
    # child only allows read_file → write_file is denied even though base allows it
    assert child.check_tool("builtin__read_file") is True
    assert child.check_tool("builtin__write_file") is False


def test_base_deny_blocks_child_allow():
    """Layered enforcement is the intersection of both allows, so a base
    deny must override a child allow — child cannot escalate past its base."""
    base = ProfilePermissionEnforcer(
        _manifest("base", allow=["builtin__*"], deny=["builtin__write_file"])
    )
    child = ProfilePermissionEnforcer(
        _manifest("child", allow=["builtin__*"]),
        base_enforcer=base,
    )
    assert child.check_tool("builtin__read_file") is True
    assert child.check_tool("builtin__write_file") is False


def test_child_deny_blocks_base_allow():
    """Child deny must also win — even if the base is permissive."""
    base = ProfilePermissionEnforcer(_manifest("base", allow=["builtin__*"]))
    child = ProfilePermissionEnforcer(
        _manifest("child", allow=["builtin__*"], deny=["builtin__git"]),
        base_enforcer=base,
    )
    assert child.check_tool("builtin__read_file") is True
    assert child.check_tool("builtin__git") is False


def test_merge_produces_layered_enforcer():
    base = ProfilePermissionEnforcer(_manifest("base", allow=["builtin__*"]))
    child_manifest = _manifest(
        "child", allow=["builtin__read_file"], deny=["builtin__git"]
    )
    merged = ProfilePermissionEnforcer.merge(child_manifest, base_enforcer=base)
    # merge() must produce an enforcer that respects both layers
    assert merged.check_tool("builtin__read_file") is True
    # Not in child's allow list — layered check requires both
    assert merged.check_tool("builtin__write_file") is False
    # Deny at child layer must win
    assert merged.check_tool("builtin__git") is False
