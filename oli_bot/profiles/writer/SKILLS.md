## Tool selection

| Task | Tool | Notes |
|------|------|-------|
| Read existing docs and source | `builtin__read_file` | Always read before writing. Match existing conventions. |
| Discover files to document | `builtin__glob`, `builtin__tree`, `builtin__list_directory` | Map the codebase structure before writing reference docs. |
| Search for terminology/patterns | `builtin__grep` | Find how a concept is named and used across the project. |
| Compare doc versions | `builtin__compare` | Spot what changed between drafts or between a doc and its source. |
| Write new documents | `builtin__write_file` | For new files. Creates parent directories automatically. |
| Edit existing documents | `builtin__edit_file` | **Preferred for targeted changes.** Include context for a unique match. |
| Look up external references | `builtin__fetch`, `builtin__websearch` | Fetch official docs, specs, or examples to cite accurately. |
| Look up books | `builtin__search_open_library` | For bibliography or citation purposes. |
| Full article text | `builtin__extract_article` | Pull the full body from a URL when a snippet isn't enough. |
| Wikipedia background | `builtin__search_wikipedia` | Quick background on a concept or technology. |
| Internal reasoning / outline | `builtin__think` | Sketch structure, resolve ambiguity, plan before writing. |
| Track multi-doc progress | `builtin__todowrite` | For documentation projects spanning multiple files. |
| Store notes / outlines | `builtin__notebook` | Save working outlines or reference snippets between steps. |
| View diagrams / screenshots | `builtin__view_image` | Describe or reference visual assets in documentation. |

## Writing patterns

### READMEs

Good README structure (adapt as needed):

```
# Project name
One-line description.

## Why / What problem this solves
Brief motivation — 2–3 sentences max.

## Features
Bullet list of key capabilities.

## Quick start
Minimal working example (install + run).

## Usage / Configuration
Reference material (flags, env vars, config options).

## Documentation
Links to deeper docs.
```

### Changelogs (Keep a Changelog format)

```markdown
## [1.2.0] - 2026-09-04

### Added
- Short description of new feature.

### Changed
- What changed and why (not just "updated X").

### Fixed
- Bug description — what it was, what it does now.

### Removed
- What was removed and the migration path.
```

### API reference entries

Document each item with: purpose (one sentence), parameters/options (table),
return value or side effect, and a usage example. Omit parameter rows with no
useful information — don't just repeat the type.

### Step-by-step guides

Number the steps. Each step = one action. State the expected outcome at the end
of any step that produces visible output. If a step can fail, note the failure
mode and how to recover.

## Editing checklist

Before writing the final file, check:

- [ ] All code examples are syntactically correct (paste and mentally trace them)
- [ ] All cross-references and links resolve to real files or URLs
- [ ] Terminology is consistent with the rest of the project
- [ ] No TODO or placeholder text left in the output
- [ ] Headers form a logical hierarchy (`#` → `##` → `###`, no skipped levels)
- [ ] Tables have header rows; columns are aligned for readability in raw Markdown

## Common pitfalls

- **Documenting the implementation, not the interface.** Readers usually want
  to know *what* something does and *how to use it*, not *how it is
  implemented internally*.
- **Copy-pasting outdated examples.** Always verify commands and code samples
  against the actual source before including them.
- **Over-nesting.** More than three heading levels usually signals that the
  document needs to be split, not nested deeper.
- **Passive voice in steps.** "The server is started with…" → "Start the
  server with…"
