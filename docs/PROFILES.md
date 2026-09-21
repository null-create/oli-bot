# Profiles

Agent profiles define the system prompt, available tools, and permission rules for different agent personalities and workflows.

## Structure

Profiles live under `profiles/<name>/` and consist of:

| File           | Required | Purpose                                                 |
| -------------- | -------- | ------------------------------------------------------- |
| `AGENTS.md`    | Yes      | System prompt; loaded at startup and on `/profile load` |
| `SKILLS.md`    | No       | Additional usage guidance appended after AGENTS.md      |
| `profile.json` | Auto     | Manifest with permissions, base inheritance, model tier |

When a profile is loaded, both `AGENTS.md` and `SKILLS.md` are combined into the system prompt.

## Built-in profiles

| Profile      | Purpose                                                                                          |
| ------------ | ------------------------------------------------------------------------------------------------ |
| `default`    | General-purpose assistant with access to all built-in tools                                      |
| `researcher` | Specialist web-research agent — high recall/high precision discovery with structured JSON output |
| `analyst`    | Specialist data-analyst agent — extracts claims, triangulates across sources, flags tensions     |
| `coder`      | Software-engineer profile — read, write, and run access for end-to-end development workflows     |
| `reviewer`   | Code-review profile — read-only analysis with test/lint execution; no file modifications         |
| `writer`     | Technical writer profile — prose, documentation, READMEs, changelogs, and guides                 |
| `planner`    | Planning agent — decomposes goals into structured, saved plans; no file modifications            |

## profile.json manifest

Each profile directory includes a `profile.json` (auto-generated if missing) with:

```json
{
  "schema_version": 1,
  "name": "my-profile",
  "version": "0.1.0",
  "description": "",
  "default_model_tier": "large",
  "required_tools": ["builtin__read_file", "builtin__glob"],
  "base": null,
  "permissions": {
    "allow_tools": ["builtin__read_*", "builtin__glob"],
    "deny_tools": ["builtin__write_*"]
  }
}
```

### Fields

- **`permissions`** -- `allow_tools`/`deny_tools` glob pattern lists. Wildcards: `*` matches any tool name, `builtin__write_*` matches all write tools.
- **`base`** -- optional parent profile name. When set, the child inherits the parent's system prompt (base content is prepended to the child's own `AGENTS.md`/`SKILLS.md`) and the parent becomes a *permission* base. Permission-wise the inheritance is an **intersection**: see the enforcement rules below. Every profile keeps its own explicit `allow_tools`/`deny_tools`, so a restrictive base does not broaden a more permissive child, and no child can exceed a parent's allow list.
- **`default_model_tier`** -- `"large"` or `"small"` for initial model size selection.
- **`required_tools`** -- tool names the profile expects to be available.

### Permission enforcement

Permission enforcement is layered:

1. A tool must be permitted by the profile's own `allow_tools` (default `["builtin__*"]`), or it is denied.
2. Deny overrides allow at the same level.
3. When a profile has a `base`, a tool must also pass the base's enforcer (`child_allowed and base_allowed`). That check recurses up the chain, so the effective rule set is the **intersection** of every profile's decision: a deny anywhere blocks the call, and a tool absent from any ancestor's `allow_tools` is blocked too.
4. `base` also carries the parent's system prompt (base content is prepended to the child's own `AGENTS.md`/`SKILLS.md`).

Practical consequence: permissiveness never propagates downward — a child can only narrow what its (single) base allows, never broaden it. And because the base's own `allow_tools`/`deny_tools` are consulted via the AND, a restrictive base tightens every child that points at it. There is no explicit "deny-list expansion" of the child with the parent's rules: the child must still explicitly allow a tool in its own `allow_tools` (they are not merged). See [SECURITY.md](SECURITY.md) for the full threat model.

## Creating profiles

- **Manually** -- create a `profiles/<name>/` directory with an `AGENTS.md` and optional `profile.json`.
- **Auto-generate** -- `/profile create <name>` invokes the current model to generate an `AGENTS.md` tailored to the given name. The prompt includes tool descriptions and (if available) the default profile as a reference. Only `AGENTS.md` is auto-generated; `SKILLS.md` and `profile.json` must be created manually if desired.

## Loading profiles

- **Startup** -- `--profile <name>` (default: `default`)
- **Runtime** -- `/profile load <name>` clears the conversation and prepends the new system message
- **List** -- `/profile list` shows available profiles

See [TOOLS.md](TOOLS.md) for how profile permissions interact with the tool permission system.
