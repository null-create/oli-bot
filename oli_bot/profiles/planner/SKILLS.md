## Tool selection

| Task | Tool | Notes |
|------|------|-------|
| Read existing source and docs | `builtin__read_file` | Understand what exists before planning what to build. |
| Discover files and structure | `builtin__glob`, `builtin__tree`, `builtin__list_directory` | Map the project before diving in. |
| Search for context | `builtin__grep` | Find relevant patterns, TODOs, existing decisions. |
| Check existing plans | `builtin__notebook` (`action=list`, then `action=get`) | Always check for prior plans before starting a new one. |
| Research prior art / context | `builtin__websearch`, `builtin__fetch` | Background research on external dependencies or approaches. |
| Academic / technical depth | `builtin__search_arxiv`, `builtin__search_github` | Find prior art, existing implementations, or benchmarks. |
| Wikipedia background | `builtin__search_wikipedia` | Quick orientation on a concept or technology. |
| Full article text | `builtin__extract_article` | Pull the full body of a reference article. |
| Internal decomposition | `builtin__think` | The most important tool — reason through phases, dependencies, risks before writing. |
| Track planning progress | `builtin__todowrite` | For complex plans, track which sections are drafted and which need more research. |
| **Save the plan** | `builtin__notebook` (`action=set`, `page="plan-<name>"`) | **Always save the finished plan.** This is the primary output of this profile. |

## Planning patterns

### Starting a planning session

```
1. builtin__notebook action=list              → check for related prior plans
2. builtin__tree / builtin__glob              → map the project
3. builtin__read_file (key files)             → understand the current state
4. builtin__think                             → decompose the goal
5. (web search if external context needed)
6. Write the plan in the output format
7. builtin__notebook action=set page=plan-<name>
```

### Decomposition heuristics

- A step is **too large** if it would take a skilled developer more than a day
  of focused work, or if it contains more than one distinct decision.
- A step is **too small** if it is a sub-action of something that always
  happens together (e.g. "create file" + "write content" is one step, not two).
- A phase boundary is natural when: there is a **verification gate** (something
  to test or review), a **handoff** between teams or agents, or a distinct
  **risk profile** change.

### Dependency notation

In the plan Markdown, express dependencies inline:

```markdown
- [ ] Step 3 — implement the cache layer
  - Depends on: Step 1 (schema finalised), Step 2 (interface agreed)
  - Blocks: Step 5, Step 6
```

For large plans, add a dependency summary section:

```markdown
## Dependency map
Step 1 → Step 3, Step 4
Step 2 → Step 3
Step 3 → Step 5, Step 6
Step 4 (independent)
```

### Risk classification

| Likelihood × Impact | Label |
|---------------------|-------|
| High × High | 🔴 Critical |
| High × Low or Low × High | 🟡 Watch |
| Low × Low | 🟢 Accept |

For each 🔴 Critical risk, always include a mitigation or contingency.

## Common pitfalls

- **Planning before understanding.** A plan written without reading the
  relevant source or docs is speculation dressed up as structure. Always
  read first.
- **Flat lists without sequencing.** A list of 20 unordered steps is not a
  plan — it's a backlog dump. Sequence and group the work.
- **Missing "out of scope".** If you don't state what's excluded, implementors
  will add it. Explicit exclusions prevent scope creep.
- **Forgetting to save.** The plan only exists if it's persisted to `notebook`.
  The final step is always `notebook(action="set", page="plan-<name>")`.
- **Treating open questions as resolved.** If something is uncertain, flag it.
  Plans that paper over unknowns fail at implementation time.

## Notebook conventions

- Page names follow `plan-<kebab-case-title>` (e.g. `plan-auth-refactor`,
  `plan-search-v2`).
- If a plan supersedes a prior one, save it as a new page (the notebook
  auto-increments: `plan-auth-refactor-2`). Do not overwrite the old plan
  without noting it.
- Use `notebook(action=get, page=plan-<name>)` to retrieve and review a
  prior plan before updating it.
