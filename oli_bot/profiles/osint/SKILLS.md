# SKILLS.md — OSINT Agent Playbook

Companion to [`SYSTEM_PROMPT.md`](./SYSTEM_PROMPT.md). This file is the working
manual: **how and when** to use each MCP tool, end-to-end example workflows, and
the tradecraft that separates real intelligence from graph queries.

The MCP server is `osint-graph` (Streamable HTTP, default
`http://localhost:9090/mcp`). All tools are read/write over the same Neo4j graph.

---

## 1. Mental model

Think of the graph as a **fused sensor picture** with four raw feeds and three
derived layers:

```
raw         news (Article)   maritime (Vessel)   air (Flight)   imagery (SatelliteImage/AOI)
                 │                  │                 │                    │
derived      Actor ── Location ── Event <─────────────┴────────────────────┘
                 │        │         │
             timeline  hotspots   bursts / chains / correlations
```

- **Raw feeds** are noisy and time-stamped. Trust them only as far as the source.
- **Derived nodes** (Event/Actor/Location) are only as good as the extraction
  that created them — always be able to fall back to the raw article/vessel.
- **Correlation tools** are where value concentrates: a single signal is a lead,
  two independent signals agreeing is intelligence.

**IDs are deterministic and reusable:**

| Entity         | Identifier                                     |
| -------------- | ---------------------------------------------- |
| Article        | 16-char sha256 of URL (`article.id`)           |
| Vessel         | `mmsi`                                         |
| Flight         | `fr24_id` (callsign/registration as secondary) |
| Airport        | `icao`                                         |
| Actor          | `name`                                         |
| Location       | `name`                                         |
| Event          | `event.id`                                     |
| AOI            | `aoi.id`                                       |
| SatelliteImage | `image.id` (format `{aoi_id}_...`)             |

---

## 2. Start every session with orientation

Never jump straight to a hypothesis. Establish the picture first.

```text
osint-graph__get_graph_summary_stats()                      # what labels exist, how much data
osint-graph__get_recent_events(hours=48)                    # current events + their actors/locations
osint-graph__get_recent_articles(hours=72)                  # current reporting + mentioned entities
osint-graph__get_aois(include_stats=True)                   # which areas have imagery
```

Interpretation:

- Sparse/zero counts → the pipeline may not have run, or the AOI is quiet. Say so.
- Many articles but few events → extraction gaps; lean on articles + manual reading
  (`osint-graph__get_article_content`).
- Events with `credibility="low"` or no `source_url` → treat as leads, not fact.

---

## 3. Tool selection by analytic question

| You are asked…                | Start with                                                                 | Then                                                                                                                 |
| ----------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| "What's happening?"           | `osint-graph__get_recent_events`, `osint-graph__get_recent_articles`       | `osint-graph__get_event_bursts`, `osint-graph__get_rolling_event_trends`                                             |
| "Is it escalating?"           | `osint-graph__get_event_bursts`, `osint-graph__get_event_sequence`         | `osint-graph__get_rolling_event_trends(type_filter=...)`, `osint-graph__get_alert_chains`                            |
| "Where are the hotspots?"     | `osint-graph__get_event_location_clusters`                                 | `osint-graph__get_location_event_timeline` per hotspot                                                               |
| "Who is behind this?"         | `osint-graph__get_actor_severity_influence`                                | `osint-graph__get_actor_network`, `osint-graph__get_actor_pathways`, `osint-graph__get_actor_event_history`          |
| "Which actors work together?" | `osint-graph__get_actor_interaction_matrix`                                | `osint-graph__get_actor_pathways(depth=2)`                                                                           |
| "Tell me about vessel X"      | `osint-graph__get_vessel(mmsi)`                                            | `osint-graph__get_vessel_anomalies`, `osint-graph__get_alert_chains`, `osint-graph__get_entity_timeline_sorted`      |
| "Any dark/suspicious ships?"  | `osint-graph__get_vessel_anomalies`                                        | `osint-graph__get_alert_chains`, `osint-graph__get_crossmodal_correlation`                                           |
| "What aircraft are involved?" | `osint-graph__get_flight_anomalies`                                        | `osint-graph__get_tracked_flights`, `osint-graph__get_flights_in_area`, `osint-graph__get_vessel_flight_correlation` |
| "Is this reported/covered?"   | `osint-graph__get_article`/`osint-graph__get_recent_articles`              | `osint-graph__get_undocumented_events`                                                                               |
| "What does imagery show?"     | `osint-graph__get_latest_satellite_images` / `osint-graph__get_aoi_images` | `osint-graph__get_image_related_entities`, `osint-graph__get_satellite_event_correlation`                            |
| "Everything about entity Y"   | `osint-graph__get_entity_timeline_sorted`                                  | `osint-graph__get_actor_network` / location timeline, then correlation tools                                         |
| "Find related reporting"      | `osint-graph__vector_search(index_name="article_embeddings")`              | `osint-graph__get_article_content` on top hits                                                                       |

---

## 4. Workflows

### Workflow A — Daily watch-floor brief

```text
1. osint-graph__get_graph_summary_stats()
2. osint-graph__get_recent_events(hours=24)
3. osint-graph__get_recent_articles(hours=24)
4. osint-graph__get_vessel_anomalies()
5. osint-graph__get_flight_anomalies()
6. osint-graph__get_undocumented_events(min_severity="high")
7. osint-graph__get_alert_chains()              # anomaly → event follow-through
8. osint-graph__get_crossmodal_correlation()    # corroborate events with vessel activity
```

Output: threat level + key developments, each with source ids. Flag anything that
appears in more than one of steps 2–8 as elevated.

### Workflow B — Location escalation assessment

```text
1. osint-graph__get_event_sequence(location_name="<place>", days=30)      # chronology
2. osint-graph__get_location_event_timeline(location_name="<place>", days=90)
3. osint-graph__get_rolling_event_trends(window_days=7)                    # trend direction
4. osint-graph__get_event_bursts(min_events=3, days=14)                    # spikes
5. osint-graph__get_event_location_clusters(proximity_km=50)               # nearby hotspots
6. osint-graph__get_crossmodal_correlation()                               # vessel/air corroboration
```

Interpretation: rising event counts + an active burst + nearby vessel anomalies =
escalation. A burst with no cross-modal corroboration is weaker (could be media
cycle).

### Workflow C — Maritime anomaly investigation (dark vessel)

```text
1. osint-graph__get_vessel_anomalies()                       # find dark_hours / ais_dark
2. osint-graph__get_vessel(mmsi)                             # identity, flag, destination, last_seen
3. osint-graph__get_alert_chains(days_before=7, days_after=7)  # did an event follow the dark period?
4. osint-graph__get_vessel_flight_correlation(proximity_km=10, time_window_hours=1)
5. osint-graph__get_entity_timeline_sorted(entity_name="<vessel mmsi or name>", days=90)
6. osint-graph__vector_search(...) on related article text if a news lead exists
```

Tradecraft: AIS gaps are common (coverage, not always intent). Weight them by
flag/destination, proximity to chokepoints/AOIs, and whether an event followed.

### Workflow D — Air anomaly investigation

```text
1. osint-graph__get_flight_anomalies()                       # anomaly_score, squawk, no_callsign
2. osint-graph__get_tracked_flights()
3. osint-graph__get_flights_in_area(area_name="<region>")
4. osint-graph__get_vessel_flight_correlation()              # coordination with shipping
5. osint-graph__get_crossmodal_correlation()                 # event linkage
```

Signals: emergency squawks (7500/7600/7700), military squawk ranges, missing
callsigns, tracked airline/registration hits, anomalous altitude/speed.

### Workflow E — Actor attribution & network

```text
1. osint-graph__get_actor_severity_influence()               # rank by severity-weighted activity
2. osint-graph__get_actor("<name>")
3. osint-graph__get_actor_network(actor_name="<name>")       # who/what they touch
4. osint-graph__get_actor_interaction_matrix(min_events=2)   # frequent partners
5. osint-graph__get_actor_pathways(actor_name="<name>", depth=2)
6. osint-graph__get_actor_event_history(actor_name="<name>", days=90)
```

Tradecraft: co-involvement is not causation. Look for repeated, geographically
and temporally clustered meetings before asserting a relationship.

### Workflow F — Cross-domain correlation (highest value)

This is the signature OSINT skill: fuse independent feeds.

```text
1. osint-graph__get_recent_events(hours=48)
2. osint-graph__get_crossmodal_correlation(proximity_km=20, time_window_hours=24)
3. osint-graph__get_vessel_flight_correlation(proximity_km=10, time_window_hours=1)
4. osint-graph__get_alert_chains(days_before=7, days_after=7)
5. osint-graph__get_latest_satellite_images()
6. osint-graph__get_satellite_event_correlation()
```

A finding corroborated across ≥2 independent domains (e.g. news event + vessel
proximity + satellite capture) is materially stronger. Say so explicitly, and
quantify the space/time overlap.

### Workflow G — Satellite imagery triage

```text
1. osint-graph__get_aois(include_stats=True)
2. osint-graph__get_latest_satellite_images(aoi_id="<id>", layer="<layer>", limit=5)
3. osint-graph__get_aoi_images(aoi_id="<id>", date_from="<ISO>", date_to="<ISO>")
4. osint-graph__get_satellite_image(image_id="<id>")         # full metadata + bbox
5. osint-graph__get_image_related_entities(image_id="<id>")  # vessels/flights/events/actors nearby
6. osint-graph__get_satellite_event_correlation()
```

If an image has no linked entities, link it with `osint-graph__link_image_to_entities` (using
its bbox + `captured_at`), then re-query related entities.

### Workflow H — Hidden / under-reported events

```text
1. osint-graph__get_undocumented_events(min_severity="high")
2. For each: osint-graph__get_event(event_id) + osint-graph__get_article_content of any near matches
3. osint-graph__get_location_event_timeline(location_name=..., days=90)
4. osint-graph__get_crossmodal_correlation()                 # independent physical corroboration
```

A high-severity event with no article coverage plus corroborating vessel/flight
or imagery activity is a priority lead.

### Workflow I — Entity deep dive

```text
1. osint-graph__get_entity_timeline_sorted(entity_name="<name>", days=90)   # sorted articles+events
2. osint-graph__get_actor_network / osint-graph__get_location_event_timeline depending on entity type
3. osint-graph__vector_search on the entity's name/context in article/event embeddings
4. osint-graph__get_article_content on top hits for primary-source reading
```

---

## 5. OSINT tradecraft

### 5.1 Source credibility

- Grade each item mentally with a simple **source reliability × information
  credibility** scheme (e.g. A–F source, 1–6 info).
- `Event.credibility` is a shortcut but never authoritative. Cross-check against
  `source_url`, article source, and independent feeds.
- Wire services and multiple independent outlets > a single outlet > social
  claims. Watch for circular reporting (many articles, one origin).

### 5.2 Corroboration

- Require **two independent** sources or **two independent modalities** before
  elevating confidence.
- "Independent" excludes articles recycling the same wire copy. Look at origin,
  timing, and wording.
- Physical sensors (AIS, ADS-B, imagery) are independent of media — the strongest
  corroboration is media claim + physical trace.

### 5.3 Temporal reasoning

- Always state the window you queried. `osint-graph__get_event_sequence` and
  `osint-graph__get_location_event_timeline` return ascending chronology — read for ordering
  and cadence, not just content.
- Distinguish **ingestion time** from **event time**. A burst can reflect a
  reporting surge, not a real-world surge.
- Look for precursor → event chains (`osint-graph__get_alert_chains`): anomalies _before_ an
  event carry more predictive weight than coincident noise.

### 5.4 Geospatial reasoning

- Keep AOI/region bboxes and `proximity_km`/`time_window_hours` in mind: the
  defaults (10–50 km, 1–24 h) are starting points, not truths. Tighten to reduce
  false positives; widen to avoid missing links.
- Chokepoints, straits, and port approaches amplify the significance of any
  anomaly. A dark ship mid-ocean is routine; the same near a chokepoint is not.

### 5.5 Link vs coincidence

- Co-location in space/time is a _lead_, not a conclusion. Require repetition,
  pattern, or a plausible causal mechanism.
- Beware base rates: busy areas (Hormuz, major airports) produce many proximity
  hits by chance. Compare against the expected background.

### 5.6 Deception, bias, and gaps

- Consider spoofing/jamming (AIS manipulation), callsign reuse, and false-flag
  reporting.
- Actively look for **disconfirming** evidence. Ask "what would make this wrong?"
- Name your intelligence gaps: missing data, dark periods, no imagery, no
  independent sourcing. Gaps shape `recommended_monitoring`.

### 5.7 OPSEC and footprint

- This graph may be shared. Writes are visible. Prefer reads; only write when
  genuinely enriching or linking.
- Never leak source-handling details or credentials into outputs.
- Do not delete nodes to "clean up" — deletion is destructive and explicit-ask only.

### 5.8 Confidence language

Use calibrated language and map it to `predictions[].confidence`:

| Word          | Meaning                                           |
| ------------- | ------------------------------------------------- |
| _confirmed_   | ≥2 independent sources / direct physical evidence |
| _probable_    | strong single-source + consistent context         |
| _possible_    | plausible but single-source or inference          |
| _unconfirmed_ | lead only; do not act on alone                    |

---

## 6. Writing to the graph (when enriching)

Only when the task is to ingest or link, and always **read first**:

1. **Find existing nodes** (`osint-graph__get_actor`, `osint-graph__get_vessel`, `osint-graph__get_aoi`,
   `osint-graph__vector_search`) to avoid duplicates — ids/names are unique keys.
2. **Upsert** the node (`osint-graph__upsert_event`, `osint-graph__upsert_actor`, `osint-graph__upsert_vessel`,
   `osint-graph__upsert_article`, `osint-graph__upsert_aoi`, `osint-graph__upsert_satellite_image`).
3. **Link** it with the `link_*` helpers (`osint-graph__link_event_to_actor`,
   `osint-graph__link_article_to_event`, `osint-graph__link_image_to_entities`, …).
4. **Verify** with the matching getter (`osint-graph__get_event`, `osint-graph__get_image_related_entities`).

Common `osint-graph__upsert_event` requirements: `date`, `type`, `severity`, `description`,
`lat`, `lon`, plus an `embedding` (768-dim) and a `source_url`/`credibility`.

---

## 7. Tool caveats & pitfalls

- **Empty results are a finding, not a failure.** Report the null result, then
  broaden parameters (more `hours`/`days`, larger `proximity_km`) and retry.
- **Defaults matter.** `osint-graph__get_recent_articles` defaults to 72 h,
  `osint-graph__get_recent_events` to 48 h, most timelines to 30–90 days. State what you used.
- **Limits.** Most tools cap at 30–200 rows. For exhaustive work, iterate and
  narrow by entity/location/type rather than expecting one call to return all.
- **`osint-graph__get_entity_timeline` can be unsorted** (Neo4j `collect` order is not
  guaranteed). Prefer `osint-graph__get_entity_timeline_sorted` for anything chronological.
- **`osint-graph__vector_search` needs an embedding**, not text. The index must be one of
  `article_embeddings`, `event_embeddings`, `actor_embeddings`.
- **`osint-graph__get_aoi_images` filters on image id prefix** (`{aoi_id}_`) as well as the
  `HAS_IMAGE` relationship — keep `aoi_id` exact.
- **`osint-graph__get_flights_in_area` matches `region`** exactly or by substring, not
  arbitrary geometry. Use the region keys from the AOI config.
- **Correlation tools use coordinate + timestamp joins.** Records missing
  `lat`/`lon` or `last_seen` are silently skipped — gaps in output may be data
  quality, not absence of activity.

---

## 8. Quick reference: preferred default calls

```text
osint-graph__get_graph_summary_stats()
osint-graph__get_recent_events(hours=24)
osint-graph__get_recent_articles(hours=72)
osint-graph__get_vessel_anomalies()
osint-graph__get_flight_anomalies()
osint-graph__get_undocumented_events(min_severity="high")
osint-graph__get_event_bursts(min_events=3, days=14)
osint-graph__get_event_location_clusters(proximity_km=50, min_events=3)
osint-graph__get_actor_severity_influence()
osint-graph__get_actor_interaction_matrix(min_events=2)
osint-graph__get_alert_chains(days_before=7, days_after=7)
osint-graph__get_crossmodal_correlation(proximity_km=20, time_window_hours=24)
osint-graph__get_vessel_flight_correlation(proximity_km=10, time_window_hours=1)
osint-graph__get_latest_satellite_images(limit=5)
osint-graph__get_satellite_event_correlation()
```

Run the cross-domain block last, on the picture you built — correlation without
context produces noise.
