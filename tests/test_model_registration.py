"""
Tests for the /model add command feature.
Tests model registration, persistence, and backward compatibility.
"""

import json
import tempfile
from pathlib import Path
import pytest

from oli_bot.models import HostConfig
from oli_bot.server_manager import ServerManager


class TestModelRegistration:
    """Test model registration functionality."""

    @pytest.fixture
    def temp_config(self):
        """Create a temporary config file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            config_path = f.name
        yield config_path
        Path(config_path).unlink(missing_ok=True)

    @pytest.fixture
    def manager(self, temp_config):
        """Create a ServerManager instance with temp config."""
        mgr = ServerManager(temp_config)
        mgr.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        mgr._save()
        return mgr

    def test_add_model_basic(self, manager):
        """Test adding a basic model registration."""
        manager.add_model("test-server", "gpt4", "gpt-4-turbo")
        models = manager.list_models("test-server")
        assert models["gpt4"] == "gpt-4-turbo"

    def test_add_model_with_tier_large(self, manager):
        """Test adding a model as large tier."""
        manager.add_model("test-server", "powerful", "mistral-large", tier="large")
        models = manager.list_models("test-server")
        assert models["powerful"] == "mistral-large"

        server = manager.get_active()
        assert server.large_model == "mistral-large"

    def test_add_model_with_tier_small(self, manager):
        """Test adding a model as small tier."""
        manager.add_model("test-server", "fast", "phi", tier="small")
        models = manager.list_models("test-server")
        assert models["fast"] == "phi"

        server = manager.get_active()
        assert server.small_model == "phi"

    def test_add_model_with_tier_default(self, manager):
        """Test adding a model as default tier."""
        manager.add_model("test-server", "default-chat", "neural-chat", tier="default")
        models = manager.list_models("test-server")
        assert models["default-chat"] == "neural-chat"

        server = manager.get_active()
        assert server.default_model == "neural-chat"

    def test_duplicate_model_name_raises_error(self, manager):
        """Test that registering duplicate model names raises error."""
        manager.add_model("test-server", "gpt4", "gpt-4-turbo")
        with pytest.raises(ValueError, match="already registered"):
            manager.add_model("test-server", "gpt4", "gpt-4-32k")

    def test_nonexistent_server_raises_error(self, manager):
        """Test that adding model to nonexistent server raises error."""
        with pytest.raises(ValueError, match="not found"):
            manager.add_model("fake-server", "model", "actual-model")

    def test_remove_model(self, manager):
        """Test removing a registered model."""
        manager.add_model("test-server", "gpt4", "gpt-4-turbo")
        removed = manager.remove_model("test-server", "gpt4")

        assert removed == "gpt-4-turbo"
        models = manager.list_models("test-server")
        assert "gpt4" not in models

    def test_get_model(self, manager):
        """Test retrieving a registered model."""
        manager.add_model("test-server", "gpt4", "gpt-4-turbo")
        model = manager.get_model("test-server", "gpt4")
        assert model == "gpt-4-turbo"

        # Non-existent returns None
        assert manager.get_model("test-server", "fake") is None

    def test_persistence_to_disk(self, temp_config):
        """Test that registered models persist to hosts.json."""
        mgr1 = ServerManager(temp_config)
        mgr1.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        mgr1.add_model("test-server", "gpt4", "gpt-4-turbo")
        mgr1.add_model("test-server", "phi", "phi-2", tier="small")

        # Load in new manager instance
        mgr2 = ServerManager(temp_config)
        models = mgr2.list_models("test-server")

        assert models["gpt4"] == "gpt-4-turbo"
        assert models["phi"] == "phi-2"
        assert mgr2.get_active().small_model == "phi-2"

    def test_backward_compatibility_old_config(self, temp_config):
        """Test that old hosts.json without registered_models still works."""
        # Create old-style config
        old_data = {
            "hosts": [
                {
                    "name": "localhost",
                    "url": "http://localhost:11434",
                    "active": True,
                    "default_model": "neural-chat",
                    "large_model": "mistral",
                    "small_model": "phi",
                }
            ]
        }
        Path(temp_config).write_text(json.dumps(old_data))

        # Load should work without errors
        manager = ServerManager(temp_config)
        assert len(manager.servers) == 1

        server = manager.get_active()
        assert server.name == "localhost"
        assert server.large_model == "mistral"
        assert server.small_model == "phi"
        # Should default to empty dict
        assert server.registered_models == {}

    def test_multiple_servers_isolated_models(self, temp_config):
        """Test that models are isolated per server."""
        manager = ServerManager(temp_config)
        manager.servers.append(
            HostConfig(name="server1", url="http://host1:11434", active=True)
        )
        manager.servers.append(
            HostConfig(name="server2", url="http://host2:11434", active=False)
        )
        manager._save()

        manager.add_model("server1", "gpt4", "gpt-4-turbo")
        manager.add_model("server2", "claude", "claude-3-opus")

        models1 = manager.list_models("server1")
        models2 = manager.list_models("server2")

        assert "gpt4" in models1
        assert "gpt4" not in models2
        assert "claude" in models2
        assert "claude" not in models1

    def test_list_models_empty(self, manager):
        """Test listing models when none are registered."""
        models = manager.list_models("test-server")
        assert models == {}

    def test_registered_models_new_field_default(self, manager):
        """Test that new HostConfig instances have registered_models field."""
        new_host = HostConfig(name="new", url="http://new:11434")
        assert hasattr(new_host, "registered_models")
        assert new_host.registered_models == {}


class TestDataStructure:
    """Test data structure changes."""

    def test_hostconfig_has_registered_models_field(self):
        """Test HostConfig includes registered_models field."""
        host = HostConfig(name="test", url="http://localhost:11434")
        assert hasattr(host, "registered_models")
        assert isinstance(host.registered_models, dict)
        assert len(host.registered_models) == 0

    def test_hostconfig_with_models_dict(self):
        """Test initializing HostConfig with registered_models."""
        models = {"gpt4": "gpt-4-turbo", "phi": "phi-2"}
        host = HostConfig(
            name="test", url="http://localhost:11434", registered_models=models
        )
        assert host.registered_models == models


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
