"""Tests for runtime base_url swap on network backends."""

from __future__ import annotations

from oli_bot.backends import HuggingFaceBackend, OllamaBackend, OpenAIBackend


def test_openai_backend_set_base_url_updates_field_and_client():
    backend = OpenAIBackend(
        api_key="sk-test",
        base_url="https://old.example.com/v1",
        model="gpt-4o",
    )
    assert backend.base_url == "https://old.example.com/v1"
    old_client = backend.client
    assert str(old_client.base_url).startswith("https://old.example.com/v1")

    backend.set_base_url("https://new.example.com/route")

    assert backend.base_url == "https://new.example.com/route"
    assert backend.client is not old_client
    assert str(backend.client.base_url).startswith("https://new.example.com/route")
    assert backend.api_key == "sk-test"
    assert backend.model == "gpt-4o"


def test_ollama_backend_set_base_url_updates_field_and_client():
    backend = OllamaBackend(model="llama3", base_url="http://localhost:11434")
    assert backend.base_url == "http://localhost:11434"
    old_client = backend.client

    backend.set_base_url("http://ollama.internal:11434")

    assert backend.base_url == "http://ollama.internal:11434"
    assert backend.client is not old_client
    assert backend.model == "llama3"


def test_huggingface_backend_set_base_url_updates_field_and_client():
    backend = HuggingFaceBackend(
        model="mistral-7b",
        api_key="hf-test",
        base_url="http://tgi.local:8080",
    )
    old_client = backend.client
    assert backend.api_key == "hf-test"

    backend.set_base_url("http://tgi.new:8080")

    assert backend.base_url == "http://tgi.new:8080"
    assert backend.client is not old_client
    assert backend.api_key == "hf-test"
    assert backend.model == "mistral-7b"
