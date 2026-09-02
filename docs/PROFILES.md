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

| Profile         | Purpose                                                                                                              |
| --------------- | -------------------------------------------------------------------------------------------------------------------- |
| `default`       | General-purpose assistant with access to all built-in tools                                                          |
| `search-agent`  | Specialist web-research agent -- high recall/high precision discovery with structured JSON output                    |
| `analyst`       | Specialist data-analyst agent -- extracts claims, triangulates across sources, flags tensions                        |
| `bug-hunter`    | Cybersecurity audit agent -- identifies vulnerabilities, logic flaws, and security defects                           |

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
- **`base`** -- optional parent profile name. When set, the child inherits the parent's system prompt and permission rules.
- **`default_model_tier`** -- `"large"` or `"small"` for initial model size selection.
- **`required_tools`** -- tool names the profile expects to be available.

### Permission enforcement

Permission enforcement is layered:

1. Deny overrides allow at the same level.
2. Child denies override parent allows.
3. Both child and parent must allow for a tool to be callable.

## Creating profiles

- **Manually** -- create a `profiles/<name>/` directory with an `AGENTS.md` and optional `profile.json`.
- **Auto-generate** -- `/profile create <name>` invokes the current model to generate an `AGENTS.md` tailored to the given name. The prompt includes tool descriptions and (if available) the default profile as a reference. Only `AGENTS.md` is auto-generated; `SKILLS.md` and `profile.json` must be created manually if desired.

## Loading profiles

- **Startup** -- `--profile <name>` (default: `default`)
- **Runtime** -- `/profile load <name>` clears the conversation and prepends the new system message
- **List** -- `/profile list` shows available profiles

See [TOOLS.md](TOOLS.md) for how profile permissions interact with the tool permission system.
