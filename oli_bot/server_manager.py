"""Backend server configuration management."""

import json
import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from ollama import AsyncClient as OllamaAsyncClient

from .models import HostConfig

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.joinpath(Path.home(), ".config", "oli")
CONFIG_FILE = Path.joinpath(CONFIG_DIR, "hosts.json")


class ServerManager:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.servers: List[HostConfig] = []
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def add_server(self, name: str, url: str) -> bool:
        if any(s.name == name for s in self.servers):
            raise ValueError(f"Server '{name}' already exists")
        is_first = len(self.servers) == 0
        self.servers.append(HostConfig(name=name, url=url, active=is_first))
        self._save()
        return is_first

    def remove_server(self, name: str) -> HostConfig:
        for i, s in enumerate(self.servers):
            if s.name == name:
                removed = self.servers.pop(i)
                if removed.active and self.servers:
                    self.servers[0].active = True
                self._save()
                return removed
        raise ValueError(f"Server '{name}' not found")

    def set_default_model(self, name: str, model: str) -> None:
        for s in self.servers:
            if s.name == name:
                s.default_model = model
                self._save()
                return
        raise ValueError(f"Server '{name}' not found")

    def set_large_model(self, name: str, model: str) -> None:
        for s in self.servers:
            if s.name == name:
                s.large_model = model
                self._save()
                return
        raise ValueError(f"Server '{name}' not found")

    def set_small_model(self, name: str, model: str) -> None:
        for s in self.servers:
            if s.name == name:
                s.small_model = model
                self._save()
                return
        raise ValueError(f"Server '{name}' not found")

    def add_model(
        self,
        server_name: str,
        friendly_name: str,
        model_name: str,
        tier: Optional[str] = None,
    ) -> None:
        """Register a new model for a server with optional tier assignment.

        Args:
            server_name: Name of the server
            friendly_name: User-friendly name for the model
            model_name: Actual model name/identifier
            tier: Optional tier - "large", "small", or "default"

        Raises:
            ValueError: If server not found or model name already exists
        """
        for s in self.servers:
            if s.name == server_name:
                if friendly_name in s.registered_models:
                    raise ValueError(
                        f"Model '{friendly_name}' already registered for server '{server_name}'"
                    )
                s.registered_models[friendly_name] = model_name
                if tier == "large":
                    s.large_model = model_name
                elif tier == "small":
                    s.small_model = model_name
                elif tier == "default":
                    s.default_model = model_name
                self._save()
                return
        raise ValueError(f"Server '{server_name}' not found")

    def remove_model(self, server_name: str, friendly_name: str) -> str:
        """Remove a registered model from a server.

        Returns:
            The actual model name that was removed

        Raises:
            ValueError: If server or model not found
        """
        for s in self.servers:
            if s.name == server_name:
                if friendly_name not in s.registered_models:
                    raise ValueError(
                        f"Model '{friendly_name}' not found for server '{server_name}'"
                    )
                model_name = s.registered_models.pop(friendly_name)
                self._save()
                return model_name
        raise ValueError(f"Server '{server_name}' not found")

    def get_model(self, server_name: str, friendly_name: str) -> Optional[str]:
        """Get the actual model name for a registered model.

        Returns:
            The model name or None if not found
        """
        for s in self.servers:
            if s.name == server_name:
                return s.registered_models.get(friendly_name)
        return None

    def list_models(self, server_name: str) -> dict:
        """List all registered models for a server.

        Returns:
            Dict mapping friendly names to actual model names
        """
        for s in self.servers:
            if s.name == server_name:
                return dict(s.registered_models)
        raise ValueError(f"Server '{server_name}' not found")

    def switch_server(self, name: str) -> HostConfig:
        found = None
        for s in self.servers:
            s.active = False
            if s.name == name:
                s.active = True
                found = s
        if found is None:
            raise ValueError(f"Server '{name}' not found")
        self._save()
        return found

    def get_active(self) -> Optional[HostConfig]:
        for s in self.servers:
            if s.active:
                return s
        return None

    def list_servers(self) -> List[HostConfig]:
        return list(self.servers)

    @staticmethod
    async def validate_ollama_url(url: str) -> tuple[bool, str]:
        """NOTE: currently only used to validate ollama URLs"""
        try:
            client = OllamaAsyncClient(host=url)
            await client.list()
            return True, ""
        except Exception as e:
            return False, str(e)

    def seed_default(self, url: str) -> None:
        if self.servers:
            return
        hostname = urlparse(url).hostname or "default"
        self.servers.append(HostConfig(name=hostname, url=url, active=True))
        self._save()

    def _load(self) -> None:
        path = Path(self.config_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for entry in data.get("hosts", []):
                cfg = HostConfig(
                    **{
                        k: v
                        for k, v in entry.items()
                        if k in HostConfig.__dataclass_fields__
                    }
                )
                if cfg.default_model and not cfg.large_model:
                    cfg.large_model = cfg.default_model
                self.servers.append(cfg)
            if self.servers and not any(s.active for s in self.servers):
                self.servers[0].active = True
        except Exception as e:
            logger.warning("Failed to load Ollama server config: %s", e)

    def _save(self) -> None:
        data = {
            "hosts": [
                {
                    "name": s.name,
                    "url": s.url,
                    "active": s.active,
                    "default_model": s.default_model,
                    "large_model": s.large_model,
                    "small_model": s.small_model,
                    "registered_models": s.registered_models,
                }
                for s in self.servers
            ]
        }
        Path(self.config_path).write_text(json.dumps(data, indent=2))
