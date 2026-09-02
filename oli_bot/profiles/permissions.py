from __future__ import annotations

import fnmatch
from typing import Optional

from .schema import PermissionsManifest, ProfileManifest


class ProfilePermissionEnforcer:
    def __init__(
        self,
        manifest: ProfileManifest,
        base_enforcer: Optional[ProfilePermissionEnforcer] = None,
    ):
        self._manifest = manifest
        self._base_enforcer = base_enforcer

    @property
    def allow_patterns(self) -> list[str]:
        return self._manifest.permissions.allow_tools

    @property
    def deny_patterns(self) -> list[str]:
        return self._manifest.permissions.deny_tools

    def check_tool(self, tool_name: str) -> bool:
        child_allowed = self._check_single(tool_name)

        if self._base_enforcer is not None:
            base_allowed = self._base_enforcer.check_tool(tool_name)
            return child_allowed and base_allowed

        return child_allowed

    def _check_single(self, tool_name: str) -> bool:
        if any(
            fnmatch.fnmatch(tool_name, pat)
            for pat in self._manifest.permissions.deny_tools
        ):
            return False
        if any(
            fnmatch.fnmatch(tool_name, pat)
            for pat in self._manifest.permissions.allow_tools
        ):
            return True
        return False

    @staticmethod
    def merge(
        child: ProfileManifest,
        base_enforcer: Optional[ProfilePermissionEnforcer] = None,
    ) -> ProfilePermissionEnforcer:
        merged = PermissionsManifest(
            allow_tools=list(child.permissions.allow_tools),
            deny_tools=list(child.permissions.deny_tools),
        )
        merged_manifest = ProfileManifest(
            name=child.name,
            permissions=merged,
        )
        return ProfilePermissionEnforcer(merged_manifest, base_enforcer=base_enforcer)
