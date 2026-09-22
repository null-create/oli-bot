"""Network tool registration + gating matrix for the new keyless search tools.

Covers: availability in the READ_ONLY_TOOLS / NETWORK_TOOLS sets, offline-mode
gating before the handler runs, and SSRF rejection on the user-supplied URL
paths (extract_article and the fixed-endpoint helpers).
"""

from __future__ import annotations

import httpx
import pytest

from oli_bot.config import AppConfig
from oli_bot.tools.manager import BuiltinToolManager, NETWORK_TOOLS, READ_ONLY_TOOLS
from oli_bot.tools.web import _check_ssrf, _MAX_REDIRECTS, _ssrf_safe_request

NEW_SEARCH_TOOLS = {
    "search_stackoverflow",
    "search_open_library",
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
    assert _check_ssrf("https://openlibrary.org/search.json") is None


# --------------------------------------------------------------------------- #
# SSRF redirect-chain protection (every hop is re-validated)                  #
# --------------------------------------------------------------------------- #


def _redirect_client(resp_fn):
    """A client whose transport returns ``resp_fn(request)`` for every request.

    httpx's synchronous ``handle_async_request`` is caught here; ``resp_fn``
    is called in the same synchronous context.
    """

    class StubTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return resp_fn(request)

    return httpx.AsyncClient(transport=StubTransport())


@pytest.mark.asyncio
async def test_ssrf_redirect_into_linklocal_is_blocked():
    # A public URL that 302s to the cloud-metadata namespace. With
    # follow_redirects=True the old code would happily land there; the manual
    # hop re-validation must refuse to follow.
    def resp(request):
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
            request=request,
        )

    async with _redirect_client(resp) as client:
        response, err = await _ssrf_safe_request(
            client, "get", "https://example.com/start"
        )
    assert response is None
    assert err is not None
    assert "SSRF" in err


@pytest.mark.asyncio
async def test_ssrf_redirect_into_loopback_is_blocked():
    def resp(request):
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1:64999/admin"}, request=request
        )

    async with _redirect_client(resp) as client:
        response, err = await _ssrf_safe_request(
            client, "get", "https://example.com/start"
        )
    assert response is None
    assert err is not None
    assert "SSRF" in err


@pytest.mark.asyncio
async def test_ssrf_redirect_budget_is_bounded():
    hops = {"n": 0}

    def resp(request):
        hops["n"] += 1
        return httpx.Response(302, headers={"location": "/loop"}, request=request)

    async with _redirect_client(resp) as client:
        response, err = await _ssrf_safe_request(
            client, "get", "https://example.com/start"
        )
    assert response is None
    assert "Too many redirects" in err
    assert hops["n"] == _MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_ssrf_safe_relative_redirect_is_still_followed():
    # Legitimate same-host relative redirects must keep working.
    def resp(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/finish"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    async with _redirect_client(resp) as client:
        response, err = await _ssrf_safe_request(
            client, "get", "https://example.com/start"
        )
    assert err is None
    assert response is not None
    assert response.status_code == 200
