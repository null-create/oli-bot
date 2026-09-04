## Tool selection

| Task | Tool | Notes |
|------|------|-------|
| Explore project layout | `builtin__tree`, `builtin__list_directory` | Always start here. Get the lay of the land before reading individual files. |
| Find files by name/pattern | `builtin__glob` | Use before `read_file` — avoid reading files you don't need. |
| Search for symbols, patterns | `builtin__grep` | Find function definitions, imports, usages, TODOs. |
| Read source files | `builtin__read_file` | Use `offset`/`length` for large files — don't read more than you need. |
| Make targeted edits | `builtin__edit_file` | **Preferred for modifications.** Include enough surrounding context for a unique match. |
| Create new files | `builtin__write_file` | For new files or full rewrites only. |
| Run tests / linters | `builtin__run_command` | `pytest`, `node`, `go test`, etc. Run after every meaningful change. |
| Check runtime behaviour | `builtin__run_command` | `python -c`, `node -e`, quick one-liners to verify behaviour. |
| Compare file versions | `builtin__compare` | Spot differences between two files or directories. |
| Internal reasoning | `builtin__think` | Plan multi-step changes, reason about edge cases, design before implementing. |
| Track progress | `builtin__todowrite` | Use for multi-file refactors or anything spanning more than a few steps. |
| Look up docs/source | `builtin__fetch`, `builtin__search_stackoverflow`, `builtin__search_github` | When you genuinely don't know an API. Prefer official docs over community answers. |

## Shell usage

`run_command` runs inside an allowlisted sandbox. Key patterns:

```bash
# Run tests
pytest tests/ -q 2>&1 | tail -30
pytest tests/test_foo.py -k "test_bar" -q

# Grep for symbol definitions
grep -rn "def my_function" src/

# Find files
find . -name "*.py" | xargs grep -l "import foo"

# Quick syntax / type check
python -m py_compile path/to/file.py

# Node / JS
node -e "console.log(require('./package.json').version)"
npx tsc --noEmit
```

Blocked: `sed -i`, `awk -f`, `find -exec`, subshells, `$VAR` expansion. Use
`xargs -I '{}'` (quoted) for templated commands.

## Edit patterns

**Targeted edit (preferred):**
```
edit_file:
  old_string: "    def old_method(self):\n        return 1"
  new_string: "    def old_method(self):\n        return 2"
```

Include 2–3 lines of surrounding context so the match is unambiguous. If
`edit_file` complains about multiple matches, add more context lines.

**New file:**
```
write_file: path/to/new_module.py
```

**Full rewrite** (last resort — only when restructuring makes incremental edits
impractical):
```
write_file: path/to/existing.py  (with complete new content)
```

## Test discipline

- Run the **existing** test suite before making changes to establish a
  baseline. If tests are already failing, note it and proceed carefully.
- After changes, run the narrowest relevant test first (`pytest tests/test_foo.py`),
  then the full suite (`pytest`).
- If a change is hard to test with the existing suite, check whether a test
  should be added — and add it.
- Do not silence or skip failing tests without an explicit reason.

## Common pitfalls

- **Don't overwrite `__init__.py` files** without reading them first — they
  often contain exports that other modules depend on.
- **Check imports at the top of any file you modify** — adding a new symbol
  may require a new import.
- **Mind line endings and trailing whitespace** — match the style of the
  surrounding file.
- **Watch for circular imports** — if adding an import causes an `ImportError`,
  check the import graph before reaching for a workaround.
- **Async context** — if the codebase uses `async/await`, sync blocking calls
  (`requests.get`, `open().read()`, etc.) inside `async` functions will stall
  the event loop. Use the async equivalent or `asyncio.to_thread`.
