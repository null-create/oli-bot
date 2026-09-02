"""Network tool registration + gating matrix for the new keyless search tools.

Covers: availability in the READ_ONLY_TOOLS / NETWORK_TOOLS sets, offline-mode
gating before the handler runs, and SSRF rejection on the user-supplied URL
paths (extract_article and the fixed-endpoint helpers).
"""

from __future__ import annotations

import pytest

from oli_bot.config import AppConfig
from oli_bot.tools.manager import BuiltinToolManager, NETWORK_TOOLS, READ_ONLY_TOOLS
from oli_bot.tools.web import _check_ssrf

NEW_SEARCH_TOOLS = {
    "search_stackoverflow",
    "search_open_library",
    "top_hacker_news_stories",
    "extract_article",
}


def test_new_search_tools_are_network_gated():
    assert NEW_SEARCH_TOOLS <= NETWORK_TOOLS


def test_new_search_tools_are_read_only():
    assert NEW_SEARCH_TOOLS <= READ_ONLY_TOOLS


def test_all_new_tools_are_registered():
    m = BuiltinToolManager()
    names = {t["name"] for t in m.get_tool_definitions()}
    assert {f"builtin__{n}" for n in NEW_SEARCH_TOOLS} <= names


@pytest.mark.parametrize("tool", sorted(NEW_SEARCH_TOOLS))
@pytest.mark.asyncio
async def test_offline_mode_blocks_each_new_network_tool(tool):
    ran = {"n": 0}

    async def handler(**kwargs):
        ran["n"] += 1
        return "results"

    cfg = AppConfig(_env_file=None, offline_mode=True)
    m = BuiltinToolManager(config=cfg)
    m._tools.clear()
    m.register_tool(tool, "d", {"type": "object", "properties": {}}, handler)

    args = {"query": "x"}
    if tool in {"extract_article"}:
        args = {"url": "https://example.com/a"}
    elif tool in {"top_hacker_news_stories"}:
        args = {}

    result = await m.call_tool(tool, args)
    assert "offline" in result.lower()
    assert ran["n"] == 0


def test_extract_article_rejects_non_http_url():
    assert "http" in _check_ssrf("ftp://example.com/file")
    assert "http" in _check_ssrf("file:///etc/passwd")


def test_extract_article_rejects_loopback_url():
    err = _check_ssrf("http://127.0.0.1:8000/secret")
    assert err is not None
    assert "SSRF" in err


def test_fixed_search_endpoints_are_ssrf_clean():
    # Maintainer-defined public endpoints must not be refused by the guard.
    for url in (
        "https://openlibrary.org/search.json",
        "https://hacker-news.firebaseio.com/v0/topstories.json",
    ):
        assert _check_ssrf(url) is None, url
