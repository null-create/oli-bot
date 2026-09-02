from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any, Dict, List

import httpx
import arxiv
import wikipedia
from ddgs import DDGS
from bs4 import BeautifulSoup
from newspaper import Article
from stackapi import StackAPI

from .manager import BuiltinToolManager

logger = logging.getLogger(__name__)

_FETCH_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
_FETCH_TIMEOUT = 10
_FETCH_MAX_CHARS = 100_000
_FETCH_TEXTUAL_TYPES = (
    "text/html",
    "text/plain",
    "text/xml",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
)
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def register_tools(manager: BuiltinToolManager) -> None:
    manager.register_tool(
        name="websearch",
        description="Search the web using DuckDuckGo. "
        "Returns a list of results with titles, URLs, and snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return. Defaults to 5.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler=_websearch_handler,
    )

    manager.register_tool(
        name="fetch",
        description="Fetch and extract web page content in Markdown format. "
        "Use this to read articles, documentation, blog posts, "
        "or any other web content.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the web page to fetch.",
                },
                "include_links": {
                    "type": "boolean",
                    "description": "Whether to include links found on the page.",
                    "default": False,
                },
                "include_images": {
                    "type": "boolean",
                    "description": "Whether to include image URLs found on the page.",
                    "default": False,
                },
                "clean_text": {
                    "type": "boolean",
                    "description": "Whether to clean and format the extracted text.",
                    "default": True,
                },
            },
            "required": ["url"],
        },
        handler=_fetch_handler,
    )

    manager.register_tool(
        name="download_file",
        description="Download a file from a URL and save it to the local filesystem. "
        "Use this to fetch remote files, images, archives, or any other content "
        "from the web and store them locally.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the file to download.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path where the downloaded file should be saved. "
                    "Parent directories will be created if they don't exist.",
                },
            },
            "required": ["url", "file_path"],
        },
        handler=_download_file_handler,
    )

    manager.register_tool(
        name="upload_file",
        description="Upload a local file to an external server using HTTP PUT or POST. "
        "Use this to save generated content, upload results, or transfer files to "
        "remote services.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to upload the file to.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the local file to upload.",
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method to use: PUT or POST. Defaults to PUT.",
                    "enum": ["PUT", "POST"],
                    "default": "PUT",
                },
                "field_name": {
                    "type": "string",
                    "description": "Form field name for POST uploads. Defaults to 'file'.",
                    "default": "file",
                },
            },
            "required": ["url", "file_path"],
        },
        handler=_upload_file_handler,
    )

    manager.register_tool(
        name="search_wikipedia",
        description="Search Wikipedia for articles related to a query. "
        "Returns a list of articles with titles, URLs, and snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return. Defaults to 10.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        handler=_search_wikipedia_handler,
    )

    manager.register_tool(
        name="search_github",
        description="Search GitHub repositories for a query. "
        "Returns a list of repositories with titles, URLs, descriptions, stars, and languages.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return. Defaults to 10.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        handler=_search_github_handler,
    )

    manager.register_tool(
        name="search_arxiv",
        description="Search arXiv for academic papers related to a query. "
        "Returns a list of papers with titles, URLs, authors, and summaries.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return. Defaults to 10.",
                    "default": 10,
                },
                "sort_by": {
                    "type": "string",
                    "description": "Sort criterion: 'relevance', 'lastUpdatedDate', or 'submittedDate'. Defaults to 'relevance'.",
                    "enum": ["relevance", "lastUpdatedDate", "submittedDate"],
                    "default": "relevance",
                },
            },
            "required": ["query"],
        },
        handler=_search_arxiv_handler,
    )

    manager.register_tool(
        name="search_stackoverflow",
        description="Search Stack Overflow for programming questions. "
        "Returns a list of questions with titles, URLs, and scores. "
        "Optionally filter by an exact tag.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return. Defaults to 5.",
                    "default": 5,
                },
                "tag": {
                    "type": "string",
                    "description": "Optional exact Stack Overflow tag to filter by "
                    "(e.g. 'python', 'javascript').",
                },
            },
            "required": ["query"],
        },
        handler=_search_stackoverflow_handler,
    )

    manager.register_tool(
        name="search_open_library",
        description="Search the Open Library catalog for books. "
        "Returns a list of books with titles, authors, first publication year, and URLs.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return. Defaults to 5.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler=_search_open_library_handler,
    )

    manager.register_tool(
        name="top_hacker_news_stories",
        description="Fetch the top stories currently on Hacker News. "
        "Returns a list of stories with titles and URLs.",
        parameters={
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of stories to return. Defaults to 5.",
                    "default": 5,
                },
            },
            "required": [],
        },
        handler=_top_hacker_news_handler,
    )

    manager.register_tool(
        name="extract_article",
        description="Extract the full text of an article from a URL using newspaper4k. "
        "Returns the title, authors, publish date, and a text preview.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the article to extract.",
                },
            },
            "required": ["url"],
        },
        handler=_extract_article_handler,
    )


def _check_ssrf(url: str) -> str | None:
    """Reject URLs whose scheme is not http(s) or whose resolved host is
    loopback, link-local (incl. cloud metadata endpoints), private/RFC1918,
    reserved, multicast, or otherwise unsafe. Returns an error string on
    rejection, or None if the URL is safe to fetch.
    """
    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return f"Error: Only http(s) URLs are allowed (got: {parsed.scheme or 'none'})."
    host = parsed.hostname
    if not host:
        return f"Error: URL has no host: {url}"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        return f"Error: DNS lookup failed for {host}: {e}"
    for family, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return (
                f"Error: Refusing to reach non-public address {ip_str} for {host} "
                f"(SSRF protection)."
            )
    return None


def _websearch_handler(query, max_results=50):
    return asyncio.to_thread(_websearch_sync, query, max_results)


def _websearch_sync(query: str, max_results: int = 50) -> str:
    results = []
    try:
        with DDGS() as ddgs:
            search_results = ddgs.text(
                query=query,
                region="all",
                max_results=max_results,
            )
            for result in search_results:
                title = result.get("title", "")
                href = result.get("href", "")
                body = result.get("body", "")
                results.append(f"{title}\n{href}\n{body}\n---\n")

    except Exception as e:
        logger.exception("Web search failed")
        return f"Error: Web search failed: {e}"
    if not results:
        return "No results found."
    return "\n".join(results)


async def _fetch_handler(
    url, include_links=False, include_images=False, clean_text=True
):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"Error: Invalid URL: {url}"

    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return ssrf_err

    headers = {"User-Agent": random.choice(_FETCH_USER_AGENTS)}
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} — {url}"
    except httpx.RequestError as e:
        return f"Error: Request failed — {url} ({e})"
    except Exception as e:
        return f"Error: Failed to fetch URL — {e}"

    content_type = (
        response.headers.get("content-type", "").lower().split(";")[0].strip()
    )
    if not content_type.startswith(_FETCH_TEXTUAL_TYPES):
        return (
            f"> Source: {url}\n\n"
            f"**Content-Type:** {content_type}\n\n"
            f"[SKIPPED] Non-text content type '{content_type}' — "
            f"cannot extract readable text."
        )

    if b"\x00" in response.content[:8192]:
        return (
            f"> Source: {url}\n\n"
            f"**Content-Type:** {content_type}\n\n"
            f"[SKIPPED] Response contains binary data despite Content-Type "
            f"'{content_type}' — cannot extract readable text."
        )

    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        text_content = re.sub(r"<[^>]+>", " ", response.text)
        text_content = re.sub(r"[ \t]+", " ", text_content)
        text_content = "\n".join(
            line.strip() for line in text_content.splitlines() if line.strip()
        )
        return f"> Source: {url}\n\n{text_content}"

    for tag in soup(["script", "style"]):
        tag.decompose()

    text_content = soup.get_text()

    if clean_text:
        lines = (line.strip() for line in text_content.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text_content = "\n".join(chunk for chunk in chunks if chunk)

    if len(text_content) > _FETCH_MAX_CHARS:
        text_content = text_content[:_FETCH_MAX_CHARS] + "\n[... truncated]"

    title_tag = soup.find("title")
    page_title = title_tag.get_text().strip() if title_tag else url
    result_parts = [f"# {page_title}", f"> Source: {url}", "", text_content]

    if include_links:
        links = []
        for link in soup.find_all("a", href=True):
            text = link.get_text().strip()
            absolute = urljoin(url, link["href"])
            links.append(f"- [{text}]({absolute})" if text else f"- {absolute}")
        if links:
            result_parts.append("")
            result_parts.append("## Links")
            result_parts.extend(links)

    if include_images:
        images = []
        for img in soup.find_all("img", src=True):
            alt = img.get("alt", "")
            absolute = urljoin(url, img["src"])
            images.append(f"- ![{alt}]({absolute})")
        if images:
            result_parts.append("")
            result_parts.append("## Images")
            result_parts.extend(images)

    return "\n".join(result_parts)


async def _download_file_handler(url, file_path):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"Error: Invalid URL: {url}"

    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return ssrf_err

    headers = {"User-Agent": random.choice(_FETCH_USER_AGENTS)}
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} — {url}"
    except httpx.RequestError as e:
        return f"Error: Download failed — {url} ({e})"
    except Exception as e:
        return f"Error: Failed to download — {e}"

    path = Path(file_path).expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        size = len(response.content)
        return f"Successfully downloaded {size} bytes from {url} to {path}"
    except Exception as e:
        return f"Error saving downloaded file: {e}"


async def _upload_file_handler(url, file_path, method="PUT", field_name="file"):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"Error: Invalid URL: {url}"

    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return ssrf_err

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return f"Error: File not found: {file_path}"
    if not path.is_file():
        return f"Error: Not a file: {file_path}"

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            if method == "POST":
                files = {field_name: (path.name, path.read_bytes())}
                response = await client.post(url, files=files)
            else:
                data = path.read_bytes()
                response = await client.put(url, content=data)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} — {url}"
    except httpx.RequestError as e:
        return f"Error: Upload failed — {url} ({e})"
    except Exception as e:
        return f"Error: Failed to upload — {e}"

    size = path.stat().st_size
    return (
        f"Successfully uploaded {path} ({size} bytes) to "
        f"{url} (HTTP {response.status_code})"
    )


async def _search_wikipedia_handler(query: str, max_results: int = 10):
    return await asyncio.to_thread(_search_wikipedia, query, max_results)


async def _search_github_handler(query: str, max_results: int = 10):
    return await asyncio.to_thread(_search_github, query, max_results)


async def _search_arxiv_handler(
    query: str, max_results: int = 10, sort_by: str = "relevance"
):
    return await asyncio.to_thread(_search_arxiv, query, max_results, sort_by)


def _search_wikipedia(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Search Wikipedia."""
    try:
        results = []
        search_results = wikipedia.search(query, results=max_results)

        for title in search_results[:max_results]:
            try:
                page = wikipedia.page(title)
                results.append(
                    {
                        "title": page.title,
                        "url": page.url,
                        "snippet": (
                            page.summary[:300] + "..."
                            if len(page.summary) > 300
                            else page.summary
                        ),
                        "source": "Wikipedia",
                    }
                )
            except wikipedia.exceptions.DisambiguationError as e:
                # Try the first disambiguation option
                try:
                    page = wikipedia.page(e.options[0])
                    results.append(
                        {
                            "title": page.title,
                            "url": page.url,
                            "snippet": (
                                page.summary[:300] + "..."
                                if len(page.summary) > 300
                                else page.summary
                            ),
                            "source": "Wikipedia",
                        }
                    )
                except Exception:
                    logger.warning(
                        "Wikipedia disambiguation fallback failed for %s", query
                    )
                    continue
            except wikipedia.exceptions.PageError:
                continue
            except Exception:
                logger.warning("Wikipedia page lookup failed for %s", query)
                continue

        return results

    except Exception as e:
        raise Exception(f"Wikipedia search failed: {str(e)}") from e


def _search_github(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Search GitHub repositories, using a token if GITHUB_TOKEN is set."""
    try:
        url = "https://api.github.com/search/repositories"
        ssrf_err = _check_ssrf(url)
        if ssrf_err:
            raise Exception(ssrf_err)
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max_results, 100),
        }
        headers = {"User-Agent": random.choice(_FETCH_USER_AGENTS)}
        response = httpx.get(url, params=params, headers=headers)
        response.raise_for_status()

        data = response.json()
        results = []

        for repo in data.get("items", [])[:max_results]:
            results.append(
                {
                    "title": repo.get("full_name", ""),
                    "url": repo.get("html_url", ""),
                    "snippet": repo.get("description", "")
                    or "No description available",
                    "source": "GitHub",
                    "stars": repo.get("stargazers_count", 0),
                    "language": repo.get("language", ""),
                    "updated": repo.get("updated_at", ""),
                }
            )

        return results

    except Exception as e:
        raise Exception(f"GitHub search failed: {str(e)}") from e


def _search_arxiv(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
) -> List[Dict[str, Any]]:
    """Search arXiv for papers."""
    try:
        sort_criterion = {
            "relevance": arxiv.SortCriterion.Relevance,
            "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
            "submittedDate": arxiv.SortCriterion.SubmittedDate,
        }.get(sort_by, arxiv.SortCriterion.Relevance)

        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=sort_criterion,
        )

        results = []
        for paper in client.results(search):
            results.append(
                {
                    "title": paper.title,
                    "url": paper.entry_id,
                    "pdf_url": paper.pdf_url,
                    "snippet": (
                        paper.summary[:500] + "..."
                        if len(paper.summary) > 500
                        else paper.summary
                    ),
                    "authors": [a.name for a in paper.authors[:5]],
                    "published": (
                        paper.published.strftime("%Y-%m-%d") if paper.published else ""
                    ),
                    "updated": (
                        paper.updated.strftime("%Y-%m-%d") if paper.updated else ""
                    ),
                    "categories": paper.categories,
                    "source": "arXiv",
                }
            )

        return results

    except Exception as e:
        raise Exception(f"arXiv search failed: {str(e)}") from e


def _format_result_lines(records: List[Dict[str, Any]]) -> str:
    if not records:
        return "No results found."
    parts = []
    for r in records:
        line = "\n".join(f"{k}: {v}" for k, v in r.items() if v)
        parts.append(line)
    return "\n---\n".join(parts) + "\n"


async def _search_stackoverflow_handler(
    query: str, max_results: int = 5, tag: str = ""
):
    return await asyncio.to_thread(_search_stackoverflow_sync, query, max_results, tag)


def _search_stackoverflow_sync(query: str, max_results: int = 5, tag: str = "") -> str:
    try:
        site = StackAPI("stackoverflow")
        site.page_size = max_results
        site.max_pages = 1
        params = {"q": query}
        if tag:
            params["tagged"] = tag
        data = site.fetch("search/advanced", **params)
        records = []
        for q in data.get("items", [])[:max_results]:
            records.append(
                {
                    "title": q.get("title"),
                    "url": q.get("link"),
                    "score": q.get("score"),
                    "source": "Stack Overflow",
                }
            )
        return _format_result_lines(records)
    except Exception as e:
        logger.exception("Stack Overflow search failed")
        return f"Error: Stack Overflow search failed: {e}"


async def _search_open_library_handler(query: str, max_results: int = 5):
    return await asyncio.to_thread(_search_open_library_sync, query, max_results)


def _search_open_library_sync(query: str, max_results: int = 5) -> str:
    try:
        url = "https://openlibrary.org/search.json"
        ssrf_err = _check_ssrf(url)
        if ssrf_err:
            return ssrf_err
        resp = httpx.get(
            url,
            params={"q": query, "limit": max_results},
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        records = []
        for d in docs[:max_results]:
            records.append(
                {
                    "title": d.get("title"),
                    "author": (d.get("author_name") or [None])[0],
                    "first_published": d.get("first_publish_year"),
                    "url": f"https://openlibrary.org{d.get('key')}",
                    "source": "Open Library",
                }
            )
        return _format_result_lines(records)
    except Exception as e:
        logger.exception("Open Library search failed")
        return f"Error: Open Library search failed: {e}"


async def _top_hacker_news_handler(max_results: int = 5):
    return await asyncio.to_thread(_top_hacker_news_sync, max_results)


def _top_hacker_news_sync(max_results: int = 5) -> str:
    try:
        ids_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
        ssrf_err = _check_ssrf(ids_url)
        if ssrf_err:
            return ssrf_err
        ids_resp = httpx.get(ids_url, timeout=_FETCH_TIMEOUT)
        ids_resp.raise_for_status()
        records = []
        for item_id in ids_resp.json()[:max_results]:
            item_url = f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
            ssrf_err = _check_ssrf(item_url)
            if ssrf_err:
                continue
            item_resp = httpx.get(item_url, timeout=_FETCH_TIMEOUT)
            item_resp.raise_for_status()
            item = item_resp.json()
            records.append(
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "source": "Hacker News",
                }
            )
        return _format_result_lines(records)
    except Exception as e:
        logger.exception("Top Hacker News fetch failed")
        return f"Error: Top Hacker News fetch failed: {e}"


async def _extract_article_handler(url: str):
    ssrf_err = _check_ssrf(url)
    if ssrf_err:
        return ssrf_err
    return await asyncio.to_thread(_extract_article_sync, url)


def _extract_article_sync(url: str) -> str:
    try:
        article = Article(url)
        article.download()
        article.parse()
        return _format_result_lines(
            [
                {
                    "title": article.title,
                    "authors": ", ".join(article.authors),
                    "publish_date": str(article.publish_date),
                    "text_preview": (article.text or "")[:300],
                }
            ]
        )
    except Exception as e:
        logger.exception("Article extraction failed")
        return f"Error: Article extraction failed: {e}"
