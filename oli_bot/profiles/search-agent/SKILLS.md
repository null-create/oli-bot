## Query formulation

- **Start broad, then narrow**: Begin with a general query to map the landscape, then refine with specific terms (dates, names, domain-specific jargon) to pinpoint precise answers.
- **Run multiple queries per sub-question**: Different phrasing surfaces different sources. Use synonyms, alternative phrasings, and question-based queries (`"how does X work"`, `"X vs Y comparison"`, `"X 2026"`).
- **Use `max_results` strategically**: Default is 5. For broad discovery, increase to 10-15. For fact-checking a specific claim, 3-5 is sufficient.
- **Prefer primary sources in queries**: Add `site:arxiv.org`, `site:.gov`, `site:reuters.com`, `site:github.com` to targeted queries where appropriate.

## Tool selection

| Task | Primary tool | When to use |
|------|-------------|-------------|
| Initial discovery, current events | `builtin__websearch` | Broadest coverage; best for recent/ongoing topics |
| Deep-dive on a specific page | `builtin__fetch` | Extract content from a URL as Markdown. Use `clean_text=true` (default). |
| Background, stable topics | `builtin__search_wikipedia` | Well-vetted summaries with citations; good for terminology, history, overviews |
| Peer-reviewed research | `builtin__search_arxiv` | Academic papers, pre-prints. Use `sort_by` to control recency vs relevance. |
| Open-source implementations | `builtin__search_github` | Code, tools, SDKs, datasets. Results include stars, language, description. |
| Programming questions | `builtin__search_stackoverflow` | Search Stack Overflow by keyword or `tag` for code/debugging questions. |
| Books | `builtin__search_open_library` | Book catalog lookups; returns title, author, first publication year. |
| Full article body | `builtin__extract_article` | newspaper4k full-text extraction (title, authors, date, preview) from a URL. |

**Discovery → Retrieve → Corroborate** pattern: use a search tool to find candidate pages, then `builtin__fetch` or `builtin__extract_article` to pull their content. Cross-reference facts across 2-3 independent sources before recording.

## Tool behavior notes

- **`builtin__websearch`** returns a single string on success, or a string starting with `"Error:"` on failure. Always check for the `"Error:"` prefix — do not assume the result is valid.
- **`builtin__fetch`** returns Markdown with a `# page title` header and `> Source: url` quote. Content is truncated at 100,000 characters — if you see `[... truncated]`, consider fetching specific sub-pages instead.
- **`builtin__search_wikipedia`**, **`builtin__search_github`**, **`builtin__search_arxiv`** return structured `list[dict]` and raise exceptions on failure. Wrap calls in your reasoning — if one source errors, fall back to another.
- **`builtin__websearch` and `builtin__fetch` gracefully return error strings** (they do not raise exceptions). Continue to an alternative tool rather than aborting the entire search.
- **The new search tools** (`builtin__search_stackoverflow`, `builtin__search_open_library`, `builtin__extract_article`) also gracefully return a single string, or a string starting with `"Error:"` on failure. Treat them like `builtin__websearch` — check the `"Error:"` prefix and fall back if a source fails.

## Source quality heuristics (for Relevance Pass)

During Step 1, discard sources that show any of:

- **Thin content**: pages with fewer than ~300 words of extractable text, excessive ads, or auto-generated boilerplate
- **SEO-spam indicators**: keyword-stuffed URLs, multiple domains with identical content, affiliate-heavy sites
- **Paywall wall**: `builtin__fetch` returns only a login prompt or cookie notice with no substantive content
- **No author or date**: claims without attribution, especially for factual/statistical claims
- **Outdated**: content that explicitly predates relevant developments (check page timestamps in context)

Prefer in order:
1. Primary sources (official docs, papers, government data, raw data)
2. Reputable journalism (Reuters, AP, BBC, major newspapers)
3. Expert analysis (well-known researchers in the field, established analysts)
4. Community knowledge bases (well-maintained wikis, technical documentation)
5. General web content (use only when corroborated by another source)

## Multi-step research patterns

### Current event / news research
```
builtin__websearch: "topic 2026"
    → for promising URLs, builtin__fetch each
    → cross-reference timing, quotes, named entities across sources
```

### Academic / technical deep-dive
```
builtin__search_arxiv: "topic" sort_by=relevance
builtin__search_wikipedia: "topic"
builtin__websearch: "topic overview 2026"
    → merge findings, note areas of consensus and disagreement
```

### Technical implementation research
```
builtin__search_github: "topic language:python"
builtin__websearch: "topic comparison benchmark"
builtin__fetch: [top result URLs]
```

### Rapid fact-check
```
builtin__websearch: `"exact claim" "counter-claim"` (quoted for precision)
    → fetch top 2-3 results regardless of stance
    → compare factual claims, dates, named entities
```

## Edge cases

- **No results**: broaden query by removing qualifiers, try a different tool, or note the gap in `coverage_notes`.
- **Paywalled article**: the snippet in `builtin__websearch` results may contain the key facts. Check it before discarding. If the snippet is insufficient, find an alternate source covering the same story.
- **Failed fetch**: try `builtin__websearch` with a `site:`-restricted query on the same domain, or switch to a different source entirely.
- **Conflicting information**: record both claims in separate source entries and note the conflict in `coverage_notes`. Do not fabricate a consensus.
- **Ambiguous query**: if results are clearly about a different topic, reformulate with disambiguating terms (e.g., `"Apple (fruit)"` vs `"Apple Inc"`).

## JSON output tips

- Each `knowledge_snippet` should be **self-contained** — readable and meaningful without the question context. Include the entity name, relevant numbers/dates, and the key claim.
- If a single source covers multiple sub-questions, create a separate source entry per sub-question (you may reference the same URL multiple times with different snippets).
- `coverage_notes` is your honesty mechanism — if you found nothing for a sub-question, say so. If sources are thin or contradictory, flag it. Empty is fine only if coverage was complete.
- Keep `knowledge_snippet` bullets strictly factual. If you're uncertain about a detail, omit it rather than guess.
