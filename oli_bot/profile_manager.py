import os
from pathlib import Path
from typing import List
import logging

from .models import ProfileData
from .profiles.schema import (
    ProfileManifest,
    auto_generate_manifest,
    load_profile_manifest,
    dump_profile_manifest,
)
from .profiles.permissions import ProfilePermissionEnforcer

logger = logging.getLogger(__name__)


class ProfileManager:
    """Manages Agent profiles with manifest support, layering, and permission enforcement."""

    def __init__(self, profiles_dir: str = None):
        if not profiles_dir:
            self.profiles_dir: Path = Path.joinpath(
                Path(os.path.abspath(os.path.dirname(__file__))), "profiles"
            )
        else:
            self.profiles_dir = Path(profiles_dir)

    def list_profiles(self) -> List[str]:
        if not self.profiles_dir.is_dir():
            return []
        ignore_dirs = ["__pycache__"]
        return sorted(
            d.name
            for d in self.profiles_dir.iterdir()
            if d.is_dir() and d.name not in ignore_dirs
        )

    def load_profile(self, name: str, _visited: set[str] | None = None) -> ProfileData:
        profile_dir = Path.joinpath(self.profiles_dir, name)
        if not profile_dir.is_dir():
            raise ValueError(f"Profile directory '{name}' not found at {profile_dir}")

        _visited = _visited or set()
        if name in _visited:
            raise ValueError(f"Circular profile base reference detected for '{name}'")
        _visited.add(name)

        manifest = self._ensure_manifest(name)

        base_data = None
        if manifest.base is not None:
            base_data = self.load_profile(manifest.base, _visited=_visited)

        content = self._load_profile_content(name, base_data)

        if base_data is not None:
            enforcer = ProfilePermissionEnforcer.merge(
                manifest, base_enforcer=base_data.permission_enforcer
            )
        else:
            enforcer = ProfilePermissionEnforcer(manifest)

        return ProfileData(
            system_prompt=content,
            manifest=manifest,
            permission_enforcer=enforcer,
        )

    def _load_profile_content(
        self, name: str, base_data: ProfileData | None = None
    ) -> str:
        content = ""
        if base_data is not None:
            content = base_data.system_prompt

        agents_path = Path.joinpath(self.profiles_dir, name, "AGENTS.md")
        if agents_path.is_file():
            if content:
                content += "\n\n"
            content += agents_path.read_text(encoding="utf-8")

        skills_path = Path.joinpath(self.profiles_dir, name, "SKILLS.md")
        if skills_path.is_file():
            content += "\n\n## Skills / Additional Guidance\n\n"
            content += skills_path.read_text(encoding="utf-8")

        return content

    def _ensure_manifest(self, name: str) -> ProfileManifest:
        manifest_path = Path.joinpath(self.profiles_dir, name, "profile.json")
        if manifest_path.is_file():
            return load_profile_manifest(manifest_path)

        is_default = name == "default"
        manifest = auto_generate_manifest(name, is_default=is_default)
        dump_profile_manifest(manifest, manifest_path)
        logger.info("Auto-generated profile.json for profile '%s'", name)
        return manifest

    def profile_exists(self, name: str) -> bool:
        return (self.profiles_dir / name / "AGENTS.md").is_file()

    def create_profile(self, name: str, content: str) -> Path:
        profile_dir = Path.joinpath(self.profiles_dir, name)
        profile_dir.mkdir(parents=True, exist_ok=False)
        agends_path = Path.joinpath(profile_dir, "AGENTS.md")
        agends_path.write_text(content, encoding="utf-8")
        manifest = auto_generate_manifest(name)
        dump_profile_manifest(manifest, Path.joinpath(profile_dir, "profile.json"))
        return agends_path
