# Security

oli is designed to be given real access to your filesystem, shell, and network — that only works if you can trust its boundaries. This document describes the permission system, sandboxing, and sub-agent security model in full, including where the guarantees currently end.

If you find a way to bypass any of the boundaries described here, please treat it as a real security issue rather than a minor bug when reporting it.

## Design principle

oli's default posture is **deny by default, escalate on ambiguity**. A tool call either:

1. Runs unconditionally (a small, explicitly unrestricted set of read-only/informational tools),
2. Passes through a structural filter with no human involved (the SSRF guard, the shell allowlist), or
3. Requires an explicit human decision (a permission prompt) before it runs.

Nothing in category 2 or 3 is bypassable by asking nicely, rephrasing, or wrapping the request differently — the checks are structural, not prompt-based, and they run regardless of what the model claims about its own intent.

## The permission system

### Tiers

| Tier                                  | Behavior                                                                                                    | Tools                                                                                                                                                                                    |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Always prompt**                     | Every call requires a decision, workspace or not                                                            | `write_file`, `edit_file`, `download_file`, `upload_file`                                                                                                                                |
| **Workspace-conditional**             | Prompts only when targeting a path outside the configured workspace; if no workspace is set, always prompts | `read_file`, `view_image`, `glob`, `grep`, `list_directory`, `tree`, `run_command`                                                                                                       |
| **Sensitive-file override**           | Prompts even _inside_ the workspace                                                                         | any read against `.env*`, `*.pem`, `*.key`, `~/.ssh/`, `~/.aws/`, or filenames containing `secret`/`credential`/`password`/`token`                                                       |
| **Sensitive-pattern override**        | Prompts even inside the workspace                                                                           | `glob`/`grep` calls whose pattern or `include` filter references sensitive keywords                                                                                                      |
| **Outbound, unprompted but filtered** | No permission prompt, but every request is checked by the SSRF guard before it's allowed to resolve         | `fetch`, `download_file`, `upload_file`, `search_github`, the URL branch of `view_image`                                                                                                 |
| **Unrestricted**                      | No permission gating at all                                                                                 | `websearch`, `search_wikipedia`, `search_arxiv`, `search_stackoverflow`, `search_open_library`, `think`, `todowrite`, `notebook` (network-backed ones are still subject to offline mode) |

Every prompt that does fire offers exactly three choices: **Allow once**, **Allow for session**, or **Deny**. Session grants live for the lifetime of the TUI process and are not persisted to disk.

### Why sensitive files override workspace trust

Being inside the workspace is not treated as sufficient justification to read credentials. A workspace grant answers "is this file part of what I'm working on," not "is this file safe to hand to a model." Those are different questions, and only the second one governs access to `.env`, SSH keys, AWS credentials, and anything with `secret`/`password`/`token` in its name — regardless of whether it happens to sit inside an approved directory.

### Profile-level permission manifests

Independent of the runtime prompt system, each agent profile (`profiles/<name>/profile.json`) carries its own static permission manifest:

```json
{
  "permissions": {
    "allow_tools": ["builtin__read_*", "builtin__glob"],
    "deny_tools": ["builtin__write_*"]
  }
}
```

- `allow_tools` / `deny_tools` accept glob patterns (`*` matches any tool name; `builtin__write_*` matches all write tools).
- Profiles can inherit from a `base` profile. Enforcement across the inheritance chain is layered and deliberately conservative:
  1. **Deny overrides allow at the same level.**
  2. **A child's deny overrides a parent's allow.**
  3. **Both the child and the parent must allow a tool for it to be callable.**

In other words, permissiveness never propagates downward by default — a child profile can only narrow what it inherits, never broaden it. A permissive base profile does not make a restrictive child profile permissive; the reverse direction is the only one that's structurally possible.

This manifest is evaluated independently of, and prior to, the runtime prompt tiers above — a tool denied by the profile manifest never reaches the prompt system at all.

## Shell execution sandboxing

`run_command` is the highest-risk built-in tool, and has the most layered set of defenses.

**Allowlist composition.** Only binaries present in `ALLOWED_COMMANDS` may run at all. This set is a union of grouped frozensets (`_ALLOWED_FILESYSTEM_READ`, `_ALLOWED_TEXT_SEARCH`, `_ALLOWED_TEXT_PROCESSING`, `_ALLOWED_ENCODING_COMPRESSION`, `_ALLOWED_UTILITIES`, `_ALLOWED_SYSTEM_INFO`, `_ALLOWED_RUNTIME_COMMANDS`, `_ALLOWED_VCS`) defined in `tools/shell.py`. Broadly: read-only filesystem/text utilities, common language runtimes (Python, Node, Ruby, Go, Rust, Deno, Bun, Perl, Lua, and their package managers), and read-only Git.

**Command composition is validated per-segment, not as a whole string.** Pipes (`|`) and logical operators (`&&`, `||`, `;`) are permitted specifically because each segment on either side is independently checked against the allowlist. `grep pattern file | head -20` passes because both halves are allowed; `grep pattern file | rm -rf /` fails because `rm` is not in the allowlist, regardless of what it's piped from.

**Recursive validation for command-wrapping utilities.** `xargs` is allowlisted, but its inner command is extracted and validated recursively against the same allowlist — `xargs rm` still fails, because the check doesn't stop at `xargs` itself.

**Git is allowed, but read-only.** Inspection subcommands (`status`, `diff`, `log`, `show`, `blame`, etc.) are permitted. Mutating subcommands (`push`, `pull`, `commit`, `reset`, `checkout`, `clean`, `rebase`, `merge`, etc.) are blocked via `DENIED_ARGS`.

**Denylisted escape hatches on otherwise-safe binaries.** Certain flags are blocked even on allowlisted commands because they turn a read-only tool into a write primitive: `find -exec`, `find -delete`, `find -execdir`, `-fprint*`, `-ok*`, `sed -i` / `sed -i.bak`, `awk -f`, `awk -i` / `--in-place=` (and gawk/nawk equivalents).

**Metacharacter and redirect blocking.** `$`, `` ` ``, `()`, `{}`, `!`, input redirects (`<`), and line-continuations are rejected outright, as are control characters. Output redirects (`>`, `>>`) to real files are permitted _only_ when a workspace is configured and the resolved target path falls inside it; writes to `/dev/null`, `/dev/stderr`, `/dev/stdout` and stderr fd duplication (`2>&1`, `2>&-`) are always allowed regardless of workspace state. Any other real-file redirect attempt is rejected with guidance to use `tee` instead.

**The one explicit, documented limitation:** once an interpreter is allowlisted, oli does not sandbox _inside_ that interpreter. `python -c '...'`, `node -e '...'`, `pytest` fixtures, and similar constructs can execute arbitrary code by design, because the allowlist operates at the level of "which binary may run," not "what that binary is permitted to do once it's running." Granting a profile access to `run_command` with these interpreters available is effectively granting general code execution — treat it accordingly when writing or auditing a profile manifest.

## SSRF protection

Every tool that can reach a URL supplied by, or influenced by, model reasoning is routed through a shared guard (`_check_ssrf` in `tools/web.py`): `fetch`, `download_file`, `upload_file`, `search_github`, `extract_article`, the URL branch of `view_image`, and the fixed Open Library endpoint.

The guard:

- Rejects any non-`http(s)` scheme outright.
- Resolves the hostname via DNS rather than trusting the literal string, so a hostname that resolves to an internal address is caught even if the string itself looks external.
- Blocks the resolved address if it is loopback, link-local (including the `169.254.169.254` cloud-metadata address), private (RFC 1918), reserved, multicast, or unspecified.

This closes the standard SSRF-to-cloud-metadata and SSRF-to-internal-service paths that a naive "check the URL string" filter would miss.

## Offline mode

On by default. Blocks all outbound calls from `websearch`, `fetch`, `search_wikipedia`, `search_github`, `search_arxiv`, `search_stackoverflow`, `search_open_library`, `extract_article`, and any HTTP-based MCP server transport. This is a deliberate kill switch, not just a default value — for a harness meant to run local-first, "no network calls happened" should be verifiable and trivial to guarantee, not an emergent property of which tools happen to get called.

Toggle at runtime with `/offline`, `/config`, `--no-offline`, or `OLI_OFFLINE_MODE=false`.

## Dry-run mode

When enabled (`/config`, `--dry-run`, or `OLI_DRY_RUN=true`), destructive tools (`write_file`, `edit_file`, `download_file`, `upload_file`, `run_command`) return a description of what _would_ execute instead of executing it. Useful for auditing a new profile or an unfamiliar `agents.yaml` pool before trusting it with real side effects.

## Sub-agent / pooling security model

Agent pooling introduces a second execution context, and it inherits the permission system rather than bypassing it:

- Each sub-agent runs its own full `Agent.process()` loop, but shares the **same permission callback** as the root agent, serialized with a lock so concurrent sub-agents don't race on permission state or produce interleaved/ambiguous prompts.
- `dispatch` itself is stripped from the tool set handed to every sub-agent, specifically to prevent a dispatched agent from re-dispatching. This is a structural recursion guard, not a convention the model is asked to follow — a sub-agent cannot dispatch further tasks even if instructed to try.
- Per-agent errors during a dispatch batch are caught and reported inline (`Error: <message>` under that agent's heading) rather than being allowed to fail or stall the rest of the batch.
- Pool size is capped (`agent_pool_size`, default 5). A pool with zero agents, or more than the cap, raises a startup error rather than silently truncating; malformed individual entries (missing `name`, `model`, or `backend.type`) are logged and skipped rather than aborting the whole pool.

**Per-agent profile enforcement:** each entry in `agents.yaml` can declare a `profile` field, intended to give that specific sub-agent its own persona and, more importantly, its own permission manifest — distinct from whatever profile the root agent is running under. This is the mechanism that makes privacy-tiered pooling (e.g. a locked-down local sub-agent handling sensitive data alongside a permissive remote agent handling everything else) actually enforceable rather than just a naming convention.

**What pooling does _not_ yet enforce:** even with per-agent profiles fully wired, there is currently no mechanism governing what a sub-agent's _output_ is allowed to carry back to whoever dispatched it. A locked-down local sub-agent can still return an unredacted summary of sensitive data directly into a remote root agent's context — profile-level tool restrictions control what a sub-agent can _do_, not what it's allowed to _say back_. A `data_sensitivity` / egress-control field for `agents.yaml` is planned but not yet implemented; until it lands, treat cross-agent output as fully trusted/unfiltered regardless of either agent's profile.

## API server trust model

The OpenAI-compatible API server (`oli-server`) removes the human from the permission loop by necessity — there's no terminal to prompt. Consequently, **the API server auto-allows permission scopes for the current request.** Offline mode and dry-run gating from `AppConfig` still apply and are not bypassed, but the interactive allow/deny/session-grant system described above does not run in this mode.

This has a direct implication for any surface you place in front of the API server: **the API server's security boundary is "whoever can reach this port," not "whoever clicks allow."** If you expose `oli-server` to anything beyond localhost — a container network, a reverse proxy, a chat platform integration — the profile loaded via `OLI_API_PROFILE` is the only thing standing between an inbound request and full tool access at that profile's permission ceiling. Treat the profile bound to the API server the way you'd treat a service account: give it the minimum tool surface the intended use case requires, not the default profile's full access.

## Known limitations

Stated plainly, so nobody discovers these the hard way:

- **Allowlisted interpreters are not sandboxed internally.** See the shell execution section above — this is a fundamental limitation of allowlisting at the binary level, not an oversight.
- **No cross-boundary output filtering yet.** Sub-agent results flow back to the dispatching agent's context unfiltered, regardless of either agent's profile permissions.
- **No egress/data-sensitivity control yet.** `agents.yaml` currently has no field governing what data is allowed to leave a given backend or agent.
- **No audit log of cross-boundary data flow.** There is currently no way to retroactively answer "what did a remote backend ever see," beyond reading session history manually.
- **Session permission grants are process-lifetime only.** They are not persisted, but they are also not scoped more narrowly than "the rest of this process" — a session-granted tool stays granted for every subsequent call in that TUI process, not just the current task.

## Verifying this yourself

Because a permission system is only as trustworthy as its weakest untested path, a few checks worth running after any change to profiles, pooling, or the API server config:

1. **Profile isolation check:** dispatch the same task to two pool entries with deliberately different `deny_tools` lists, and confirm the restricted one actually refuses the tool call it should refuse — don't just confirm the config loads without error.
2. **Sensitive-file check:** point `read_file` at a `.env` file inside an approved workspace and confirm it still prompts, rather than silently succeeding because the path is "trusted."
3. **SSRF check:** attempt a `fetch` against `http://169.254.169.254` and confirm it's rejected, rather than assuming the guard covers it.
4. **API server exposure check:** confirm whatever profile `OLI_API_PROFILE` points to has a permission manifest appropriate for "anyone who can reach this port," not the default profile, before exposing the server beyond localhost.
