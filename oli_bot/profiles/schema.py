from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class PermissionsManifest(BaseModel):
    allow_tools: list[str] = Field(default=["builtin__*"])
    deny_tools: list[str] = Field(default=[])


class ProfileManifest(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    name: str
    version: str = Field(default="0.1.0")
    description: str = Field(default="")
    default_model_tier: str = Field(default="large")
    required_tools: list[str] = Field(default=[])
    base: Optional[str] = Field(default=None)
    permissions: PermissionsManifest = Field(default_factory=PermissionsManifest)


def load_profile_manifest(path: Path) -> ProfileManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ProfileManifest(**data)


def dump_profile_manifest(manifest: ProfileManifest, path: Path) -> None:
    path.write_text(
        json.dumps(manifest.model_dump(), indent=2, default=str),
        encoding="utf-8",
    )


def auto_generate_manifest(name: str, is_default: bool = False) -> ProfileManifest:
    return ProfileManifest(
        schema_version=1,
        name=name,
        version="0.1.0",
        description="",
        default_model_tier="large",
        base=None if is_default else "default",
        permissions=PermissionsManifest(
            allow_tools=["builtin__*"],
            deny_tools=[],
        ),
    )
