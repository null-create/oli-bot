## Long-term memory tool reference

### Quick-pick table

| Scenario | Tool(s) | Notes |
|----------|---------|-------|
| Session start — check prior knowledge | `knowledge_check` | Always call first; use `topic` = what the user asked about |
| Store a self-contained fact or note | `memory_store` | Set `category`, `importance`, and `tags` |
| Check for duplicate before storing | `memory_find_similar` | Pass a descriptive `query`; use `min_similarity=0.7` |
| Update or correct an existing memory | `memory_update` | Preserves id/created_at/access_count; regenerates embedding on content change |
| Recall relevant memories for a query | `memory_recall` | Filter by `category` and/or `min_importance` to narrow results |
| Recall all memories for a project | `memory_recall_project` | Pass bare project name (with or without `project:` prefix) |
| Fetch a specific memory by id | `memory_get` | Use when a Document URL is `memory://<id>` |
| Delete a stale memory | `memory_delete` | Use sparingly — prefer `memory_update` to correct |
| Add a typed entity to the graph | `graph_upsert_entity` | Deduplicates by semantic name similarity |
| Store a directed relationship | `graph_store_relationship` | Both entities must already exist or be created in the same batch |
| Batch-ingest entities + relationships | `graph_bulk_ingest` | One call for many items — always prefer over repeated single calls |
| Store a verifiable fact/claim | `graph_store_claim` | Set `status` (`unverified` / `supported` / `disputed`) and `confidence` |
| Build rich graph context for a query | `graph_recall_context` | Combines entities, relationships, claims, and memories in one result |
| Find entities by semantic search | `graph_find_entities` | Filter by `node_types` when you know the type |
| Traverse relationships around entities | `graph_get_relationships` | Use `max_hops=2` for indirect links |
| Find shortest path between two entities | `graph_find_paths` | Useful for tracing dependency chains or lineage |
| Find common neighbours | `graph_find_common_neighbors` | Good for discovering hidden shared dependencies |
| Browse available memory categories | `memory_list_categories` | Use at session start to orient; shows count + recency per category |
| List project-specific categories | `memory_list_categories` → filter `project:*` | Reveals all projects already in memory |

### Decision: `memory_store` vs. graph tools

Use **`memory_store`** when the information is:
- A standalone fact, summary, or note
- A user preference or constraint
- A session observation without clear structural relationships
- Anything you'd write as a sticky note

Use **graph tools** when the information involves:
- Named objects (modules, APIs, services, people, technologies) and how they connect
- Typed relationships (`DEPENDS_ON`, `IMPLEMENTS`, `AUTHORED_BY`, etc.)
- Facts that may need verification or could contradict other stored facts
- Hierarchies (IS_A edges via `graph_store_hierarchy`)

When in doubt, start with `memory_store` and promote to graph entities later
if the information grows into a network of connected facts.

### Memory workflow: store new project info

```
# 1. Check if already known
long-term-memory__knowledge_check  topic="<project name>"

# 2. If not known, bulk-ingest the key entities
long-term-memory__graph_bulk_ingest  entities=[
  {"name": "<ProjectName>", "entity_type": "project", "description": "..."},
  {"name": "<MainModule>",  "entity_type": "module",  "description": "..."}
]  relationships=[
  {"source": "<ProjectName>", "target": "<MainModule>", "relation": "CONTAINS"}
]

# 3. Store a summary memory for fast recall
long-term-memory__memory_store
  content    = "<ProjectName>: <one-paragraph purpose + stack summary>"
  category   = "project:<projectname>"
  importance = 8
  tags       = ["<lang>", "<framework>"]
```

### Memory workflow: record a significant code change

```
long-term-memory__memory_store
  content    = "Changed <what> in <module>: <why>. Affected: <list>. Caveat: <any>."
  category   = "project:<projectname>"
  importance = 7
  tags       = ["change", "<module-name>"]

# If the change affects a graph entity, update the entity description too
long-term-memory__graph_upsert_entity
  name        = "<ModuleName>"
  description = "<updated description reflecting the change>"
```

### Memory workflow: record a user preference

```
long-term-memory__memory_store
  content    = "User prefers <preference>. Scope: <global|project:name>."
  category   = "preferences"
  importance = 6
  tags       = ["preference", "<topic>"]
```

### Memory workflow: update stale info

```
# 1. Find the existing memory
long-term-memory__memory_find_similar  query="<topic>"

# 2. Update in-place (do not delete + re-create)
long-term-memory__memory_update
  memory_id  = "<id from step 1>"
  content    = "<corrected/extended content>"
  importance = <revised score if needed>
  tags       = ["<updated tags>"]
```

### Avoid these patterns

- **Don't create a memory without checking for duplicates first.** Always run
  `memory_find_similar` or `knowledge_check` before `memory_store`.
- **Don't store raw secrets or credentials.** Record the pattern, location, and
  loading mechanism — never the value itself.
- **Don't store highly volatile data** (e.g. current line numbers, transient
  test output). Focus on durable facts.
- **Don't wait until the end of a session.** Store as you go — partial sessions
  still produce useful memories.
- **Don't delete when you can update.** `memory_update` preserves provenance;
  `memory_delete` is irreversible.
