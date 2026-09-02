#!/usr/bin/env python3
"""
Manual verification tests for /model add command feature.
This tests key functionality without pytest.
"""

import json
import tempfile
from pathlib import Path
from oli_bot.models import HostConfig
from oli_bot.server_manager import ServerManager


def test_hostconfig_has_registered_models():
    """Verify HostConfig includes registered_models field."""
    host = HostConfig(name="test", url="http://localhost:11434")
    assert hasattr(
        host, "registered_models"
    ), "HostConfig missing registered_models field"
    assert isinstance(host.registered_models, dict), "registered_models should be dict"
    assert len(host.registered_models) == 0, "registered_models should start empty"
    print("✓ HostConfig has registered_models field")


def test_add_model_basic():
    """Test adding a basic model registration."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        manager = ServerManager(config_path)
        manager.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        manager._save()

        manager.add_model("test-server", "gpt4", "gpt-4-turbo")
        models = manager.list_models("test-server")

        assert "gpt4" in models, "Model not found in list"
        assert models["gpt4"] == "gpt-4-turbo", "Model name mismatch"
        print("✓ Add model basic works")
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_add_model_with_tier():
    """Test adding a model with tier assignment."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        manager = ServerManager(config_path)
        manager.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        manager._save()

        manager.add_model("test-server", "large-model", "mistral", tier="large")
        server = manager.get_active()

        assert server.large_model == "mistral", "Large model not set correctly"
        print("✓ Add model with tier works")
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_persistence():
    """Test that registered models persist to hosts.json."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        # Create and populate
        mgr1 = ServerManager(config_path)
        mgr1.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        mgr1.add_model("test-server", "gpt4", "gpt-4-turbo")
        mgr1.add_model("test-server", "phi", "phi-2", tier="small")

        # Reload in new instance
        mgr2 = ServerManager(config_path)
        models = mgr2.list_models("test-server")

        assert "gpt4" in models, "Model not persisted"
        assert models["gpt4"] == "gpt-4-turbo", "Model value not persisted"
        assert models["phi"] == "phi-2", "Tier model not persisted"

        server = mgr2.get_active()
        assert server.small_model == "phi-2", "Tier assignment not persisted"
        print("✓ Persistence to disk works")
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_backward_compatibility():
    """Test that old hosts.json without registered_models still works."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        # Create old-style config without registered_models
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
        Path(config_path).write_text(json.dumps(old_data))

        # Load should work without errors
        manager = ServerManager(config_path)
        assert len(manager.servers) == 1, "Failed to load old config"

        server = manager.get_active()
        assert server.name == "localhost", "Server name mismatch"
        assert server.large_model == "mistral", "Large model not loaded"
        assert server.small_model == "phi", "Small model not loaded"
        assert server.registered_models == {}, "Should default to empty dict"
        print("✓ Backward compatibility with old configs works")
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_duplicate_error():
    """Test that duplicate model names raise error."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        manager = ServerManager(config_path)
        manager.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        manager._save()

        manager.add_model("test-server", "gpt4", "gpt-4-turbo")

        try:
            manager.add_model("test-server", "gpt4", "gpt-4-32k")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "already registered" in str(e), "Wrong error message"
            print("✓ Duplicate error handling works")
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_remove_model():
    """Test removing a registered model."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        manager = ServerManager(config_path)
        manager.servers.append(
            HostConfig(name="test-server", url="http://localhost:11434", active=True)
        )
        manager._save()

        manager.add_model("test-server", "gpt4", "gpt-4-turbo")
        removed = manager.remove_model("test-server", "gpt4")

        assert removed == "gpt-4-turbo", "Remove didn't return model name"
        models = manager.list_models("test-server")
        assert "gpt4" not in models, "Model still in list after removal"
        print("✓ Remove model works")
    finally:
        Path(config_path).unlink(missing_ok=True)


def test_multiple_servers():
    """Test that models are isolated per server."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name

    try:
        manager = ServerManager(config_path)
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

        assert "gpt4" in models1, "Server1 model not found"
        assert "gpt4" not in models2, "Server1 model leaked to server2"
        assert "claude" in models2, "Server2 model not found"
        assert "claude" not in models1, "Server2 model leaked to server1"
        print("✓ Multiple server isolation works")
    finally:
        Path(config_path).unlink(missing_ok=True)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Testing /model add command feature")
    print("=" * 60 + "\n")

    try:
        test_hostconfig_has_registered_models()
        test_add_model_basic()
        test_add_model_with_tier()
        test_persistence()
        test_backward_compatibility()
        test_duplicate_error()
        test_remove_model()
        test_multiple_servers()

        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60 + "\n")
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n")
        import traceback

        traceback.print_exc()
        exit(1)
