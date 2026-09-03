# Built-in Tools

All built-in tools are exposed to the model as `builtin__<name>`.

## Tool reference

| Tool                 | Description                                                                                                                                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **read_file**        | Read any text file. Supports optional `offset` and `length` for partial reads.                                                                                                                                                                                |
| **view_image**       | Load an image from a local path or `http(s)` URL and attach it for visual analysis. Uses Pillow to sniff the format, downscale to `max_edge_px` (default 1568), and re-encode. The image reaches the model as vision input on Ollama vision models and OpenAI (`gpt-4o` etc.); on text-only backends the follow-up user message carries a bracketed placeholder describing the attachment. Optional `question` seeds the caption on the synthetic user message. Rejects files over 20 MB and non-image `Content-Type` on URL fetches. Local paths follow the same workspace gating as `read_file`; URL fetches are blocked by offline mode and always pass through the SSRF guard. |
| **write_file**       | Create or overwrite files (up to 100k chars), auto-creates parent directories.                                                                                                                                                                                |
| **edit_file**        | Surgical find-and-replace on existing files; rejects ambiguous multi-matches.                                                                                                                                                                                 |
| **run_command**      | Execute shell commands from a grouped allowlist. Read-only utilities (grep/rg/ag, sed, awk, xargs, jq/yq, tee, find, ls, cat, head/tail, wc, sort, uniq, sha*sum, base64) plus language runtimes (python/python3/pytest, node/npm/npx, ruby, go, cargo/rustc, deno, bun, perl, lua, pip/uv/poetry/pipx). Pipes (`\|`) and logical operators (`&&`, `\|\|`) are **allowed and safe** — each piped command is independently validated. Output redirects (`>`, `>>`) to real files are permitted only when a workspace is set and the target resolves inside it; stderr fd duplication (`2>&1`, `2>&-`) and writes to `/dev/null`, `/dev/stderr`, `/dev/stdout` are always allowed. `xargs`' inner command is validated recursively against the allowlist so `xargs rm` still fails. `git` is allowed for read-only inspection (`status`/`diff`/`log`/`show`/`blame`/etc.) but mutating subcommands (`push`/`pull`/`commit`/`reset`/`checkout`/`clean`/`rebase`/`merge`/etc.) are blocked via `DENIED_ARGS`. Per-command `DENIED_ARGS` / `DENIED_ARG_PREFIXES` also block escape hatches (`find -exec`/`-delete`, `sed -i`, `awk -f`/`-i`). Input redirects (`<`), subshells, backticks, unquoted `{}`, and line-continuations are rejected. Use the `workdir` parameter to change directory — `cd` does not persist across invocations. |
| **glob**             | Recursive file search by glob pattern (e.g. `**/*.py`, `*.json`), capped at 200 results.                                                                                                                                                                      |
| **grep**             | Regex content search with optional `include` file filter, returns `file:line:match`, capped at 50 results.                                                                                                                                                    |
| **list_directory**   | Structured directory listing with type, modification time, and size; dirs-first sort.                                                                                                                                                                         |
| **tree**             | Display directory structure as a tree, with optional depth control.                                                                                                                                                                                           |
| **websearch**        | Web search via DuckDuckGo; returns titles, URLs, and snippets.                                                                                                                                                                                                |
| **fetch**            | Fetch and extract web page content in Markdown format. Uses BeautifulSoup with user-agent rotation, rejects non-textual content types. Supports `include_links` and `include_images`.                                                                         |
| **download_file**    | Download a file from a URL and save it to the filesystem. Uses httpx with user-agent rotation.                                                                                                                                                                |
| **upload_file**      | Upload a local file to an external server via HTTP PUT or POST (multipart).                                                                                                                                                                                   |
| **search_wikipedia** | Search Wikipedia for articles related to a query; returns titles, URLs, and snippets.                                                                                                                                                                         |
| **search_github**    | Search GitHub repositories; returns repo names, URLs, descriptions, stars, and language.                                                                                                                                                                      |
| **search_arxiv**     | Search arXiv for academic papers; returns titles, authors, summaries, sortable by relevance or date.                                                                                                                                                          |
| **search_stackoverflow** | Search Stack Overflow questions; returns titles, URLs, and scores, optionally filtered by tag.                                                                                                                                                            |
| **search_open_library** | Search the Open Library catalog for books; returns titles, authors, first publication year, and URLs.                                                                                                                                                         |
| **extract_article**  | Extract full text from an article URL via newspaper4k; returns title, authors, publish date, and a text preview.                                                                                                                                               |
| **compare**          | Compare files or directories and summarize differences.                                                                                                                                                                                                       |
| **todowrite**        | Create and maintain a structured task list for the current session; tracks progress, organizes multi-step work.                                                                                                                                               |
| **think**            | Internal reasoning scratchpad -- stores chain-of-thought in conversation history without displaying it to the user.                                                                                                                                           |
| **notebook**         | Agent working memory -- store and retrieve Markdown notes across named pages under `notes/`. Pages named `plan-<name>` (as used by `/mode plan`) auto-increment to `plan-<name>-2`, `-3`, ... on collision instead of overwriting.                                                                                                                                                                  |

## Permission system

The agent requires user approval for operations that could affect your system:

- **Write tools** (`write_file`, `edit_file`, `download_file`) -- always prompt.
- **Upload tool** (`upload_file`) -- always prompts.
- **Read/shell tools** (`read_file`, `view_image`, `glob`, `grep`, `list_directory`, `tree`, `run_command`) -- prompt when targeting paths outside the workspace. If no workspace is set (`/workspace unset`), these always prompt.
- **Sensitive files** (`.env*`, `*.pem`, `*.key`, `~/.ssh/`, `~/.aws/`, files with `secret`/`credential`/`password`/`token` in the name) -- prompt on `read_file` even inside the workspace.
- **Sensitive glob/grep patterns** -- requests whose pattern or `include` field references sensitive keywords also prompt.
- **Outbound HTTP tools** (`fetch`, `download_file`, `upload_file`, `search_github`, `view_image` URL branch) -- no permission prompt, but every request passes through the SSRF guard (see below).
- **Unrestricted tools** -- `websearch`, `search_wikipedia`, `search_arxiv`, `search_stackoverflow`, `search_open_library`, `think`, `todowrite`, `notebook` -- no permission gating (the network ones are still blocked by offline mode).

Each permission prompt offers three choices: **Allow once**, **Allow for session**, or **Deny**. Session grants persist for the lifetime of the TUI process.

## Security

oli defends against malicious or confused agents exfiltrating secrets or reaching internal infrastructure:

- **Shell allowlist** -- `run_command` only executes binaries in `ALLOWED_COMMANDS`, composed by unioning grouped frozensets (`_ALLOWED_FILESYSTEM_READ`, `_ALLOWED_TEXT_SEARCH`, `_ALLOWED_TEXT_PROCESSING`, `_ALLOWED_ENCODING_COMPRESSION`, `_ALLOWED_UTILITIES`, `_ALLOWED_SYSTEM_INFO`, `_ALLOWED_RUNTIME_COMMANDS`, `_ALLOWED_VCS`) in `tools/shell.py`. Pipes (`|`) and logical operators (`&&`, `||`, `;`) are allowed and safe because each segment is independently validated. `xargs` is allowlisted but its **inner** command is validated recursively — `xargs rm` still fails. `git` is allowed but restricted via `DENIED_ARGS` to read-only subcommands (`status`/`diff`/`log`/`show`/`blame`/etc.) -- mutating subcommands (`push`/`pull`/`commit`/`reset`/`checkout`/`clean`/`rebase`/`merge`/etc.) are rejected. Once an interpreter is allowlisted, the sandbox for that binary is advisory — `python -c '…'`, `node -e '…'`, `pytest` fixtures etc. can execute arbitrary code by design.
- **Argument denylist** -- known escape hatches on otherwise-safe binaries are rejected (`find -exec`, `find -delete`, `find -execdir`, `-fprint*`, `-ok*`, `sed -i` / `sed -i.bak`, `awk -f`, `awk -i` / `--in-place=`, plus gawk/nawk equivalents).
- **Shell metacharacter blocking** -- dangerous characters (`$`, `` ` ``, `()`, `{}`, `!`), input redirects (`<`), and line-continuations (`\` + newline) are rejected. Control characters are also blocked. Output redirects (`>`, `>>`) to real files are permitted only when a workspace is set (`/workspace set`) **and** the target path resolves inside it; fd duplication (`2>&1`, `2>&-`) and writes to `/dev/null`/`/dev/stderr`/`/dev/stdout` are always allowed. Otherwise real-file redirects are rejected with guidance to use `tee`.
- **SSRF guard** -- `_check_ssrf` in `tools/web.py` inspects every outbound URL reachable from user input (`fetch`, `download_file`, `upload_file`, `search_github`, `extract_article`, `view_image` URL branch, and the fixed Open Library endpoint). Non-`http(s)` schemes are refused; hostnames are resolved and any address that is loopback, link-local (incl. `169.254.169.254`), private (RFC 1918), reserved, multicast, or unspecified is blocked.
- **Sensitive-file gating** -- described above in the permission system.
- **Offline mode** -- default-on kill switch for every outbound tool and HTTP-based MCP transport.
- **Dry-run mode** -- destructive tools return a preview string without executing.

## Dry-run mode

When enabled (via `/config`, `OLI_DRY_RUN=true`, or `--dry-run`), destructive tools (`write_file`, `edit_file`, `download_file`, `upload_file`, `run_command`) return a preview of what would execute without actually running. A `[DRY RUN]` banner appears in the header and status bar.

## Offline mode

Enabled by default. Blocks all network access for web tools (`websearch`, `fetch`, `search_wikipedia`, `search_github`, `search_arxiv`, `search_stackoverflow`, `search_open_library`, `extract_article`) and HTTP-based MCP servers. Toggle at runtime via `/offline` or `/config`. Disable on startup with `--no-offline` or `OLI_OFFLINE_MODE=false`.

## Shell command composition

The `run_command` tool supports pipes and logical operators for safe command composition. Each segment is independently validated, so complex pipelines are both powerful and secure.

### Pipes

Use pipes to chain commands where one command's output feeds into another:

```
# Count matching files
find . -name "*.py" | wc -l

# Show first 20 results
grep -r "pattern" src/ | head -20

# Sort and deduplicate
cat file.txt | sort | uniq

# Complex pipeline
ps aux | grep python | grep -v grep | wc -l
```

**Why it's safe:** The validation splits on pipes and validates each command independently. A blocked command anywhere in the pipeline will reject the entire command:
- ✓ `grep pattern file | head -20` — both commands allowed
- ✗ `grep pattern file | rm -rf /` — fails because `rm` not allowlisted

### Logical operators

Use `&&` for sequential execution (run next command only if previous succeeds), and `||` for fallback (run next command only if previous fails):

```
# Run if previous succeeds
find . -name "*.py" && echo "Found Python files"

# Fallback pattern
cd /tmp && pwd || echo "Failed to change directory"

# Chain multiple commands
grep pattern file && sort output.txt && wc -l output.txt
```

**Why it's safe:** Like pipes, operators are validated at the segment level. Each command must be allowlisted independently.

### Semicolon separator

Use `;` to separate independent commands (all execute regardless of success/failure):

```
# Run both regardless of outcome
ls /tmp ; ps aux
```

## Tool result truncation

Tool results are automatically truncated to conserve context window:

- **Small tier** -- 4,000 characters (configurable via `OLI_TRUNCATION_SMALL`)
- **Large tier** -- 100,000 characters (configurable via `OLI_TRUNCATION_LARGE`)

Truncation preserves sentence and paragraph boundaries when possible, appending a `[... truncated: N characters remaining]` notice.
