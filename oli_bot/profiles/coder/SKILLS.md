## Tool selection

| Task | Tool | Notes |
|------|------|-------|
| Explore project layout | `builtin__tree`, `builtin__list_directory` | Always start here. Get the lay of the land before reading individual files. |
| Find files by name/pattern | `builtin__glob` | Use before `read_file` — avoid reading files you don't need. |
| Search for symbols, patterns | `builtin__grep` | Find function/type definitions, imports, usages, TODOs. |
| Read source files | `builtin__read_file` | Use `offset`/`length` for large files — don't read more than you need. |
| Make targeted edits | `builtin__edit_file` | **Preferred for modifications.** Include enough surrounding context for a unique match. |
| Create new files | `builtin__write_file` | For new files or full rewrites only. |
| Run tests / linters / formatters | `builtin__run_command` | Use whatever the project defines (test runner, build tool, formatter). Run after every meaningful change. |
| Check runtime behaviour | `builtin__run_command` | Quick one-off invocations in the project's language/runtime to verify behaviour. |
| Compare file versions | `builtin__compare` | Spot differences between two files or directories. |
| Internal reasoning | `builtin__think` | Plan multi-step changes, reason about edge cases, design before implementing. |
| Track progress | `builtin__todowrite` | Use for multi-file refactors or anything spanning more than a few steps. |
| Look up docs/source | `builtin__fetch`, `builtin__search_stackoverflow`, `builtin__search_github` | When you genuinely don't know an API. Prefer official docs over community answers. |

## Shell usage

`run_command` runs inside an allowlisted sandbox. Key patterns:

```bash
# Run tests — use whatever the project's test command is, e.g.:
<test-command> 2>&1 | tail -30
<test-command> -k "some_filter"

# Grep for symbol definitions
grep -rn "function_or_symbol_name" src/

# Find files
find . -name "*.<ext>" | xargs grep -l "<pattern>"

# Quick syntax / type / build check
<project's build or type-check command>

# Run the project's formatter/linter, if one is configured
<project's format/lint command>
```

Blocked: `sed -i`, `awk -f`, `find -exec`, subshells, `$VAR` expansion. Use
`xargs -I '{}'` (quoted) for templated commands.

Before running any test, build, lint, or format command, check the project
for how it's actually invoked (e.g. a Makefile, an npm/cargo/go script, a
CI config, or a section in AGENTS.md) rather than assuming a default.

## Edit patterns

**Targeted edit (preferred):**
```
edit_file:
  old_string: "<a few lines of exact existing code>"
  new_string: "<the replacement code>"
```

Include 2–3 lines of surrounding context so the match is unambiguous. If
`edit_file` complains about multiple matches, add more context lines.

**New file:**
```
write_file: path/to/new_file
```

**Full rewrite** (last resort — only when restructuring makes incremental edits
impractical):
```
write_file: path/to/existing_file  (with complete new content)
```

## Test discipline

- Run the **existing** test suite before making changes to establish a
  baseline. If tests are already failing, note it and proceed carefully.
- After changes, run the narrowest relevant test first, then the full suite.
- If a change is hard to test with the existing suite, check whether a test
  should be added — and add it.
- Do not silence or skip failing tests without an explicit reason.
- If the same failure persists after a few genuine attempts to fix it, stop
  and report what you tried rather than continuing to iterate blindly.

## Format and lint discipline

- Check whether the project has a formatter or linter configured (a config
  file, a package/build script, a pre-commit hook, or mention in AGENTS.md).
- If one exists, run it on changed files before considering the work done.
- Don't introduce a new formatter/linter or reformat unrelated files —
  match what the project already has, and keep the diff focused.

## Common pitfalls

- **Don't overwrite index/barrel/package-init files** (e.g. `__init__.py`,
  `index.ts`, `mod.rs`) without reading them first — they often contain
  exports that other modules depend on.
- **Check imports/includes at the top of any file you modify** — adding a
  new symbol may require a new import or dependency declaration.
- **Mind line endings and trailing whitespace** — match the style of the
  surrounding file.
- **Watch for circular imports/dependencies** — if adding an import causes a
  resolution error, check the import graph before reaching for a workaround.
- **Async/concurrency context** — if the codebase uses async or threaded
  patterns, blocking calls inside an async or non-blocking context can stall
  the event loop or scheduler. Use the language's async-safe equivalent.
