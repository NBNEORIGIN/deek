# Crosslink graph

Deek's memory layer now carries an entity graph alongside salience
and recency. Entities (customers, suppliers, SKUs, materials,
modules, error types, job numbers) become nodes; co-occurrence across
memories becomes edges. Later phases will walk the graph at retrieval
to surface structurally-related memories that pure cosine similarity
misses.

Status: **Phase B — graph walk live in shadow mode**. Every retrieval
runs the walk in the background, logs divergence to
`data/graph_shadow.jsonl`, but returns the pre-Phase-B result to the
caller. Cutover (flipping `DEEK_CROSSLINK_SHADOW=false`) is Phase C.

## Entity taxonomy

`config/entity_taxonomy.yaml` defines the types Deek recognises, the
extraction method for each, and the stop-entity blocklist.

| Type | Source | Notes |
|---|---|---|
| `m_number` | regex `\bM\d{4,5}\b` | NBNE job numbers. Stable, high-signal. |
| `module` | `DEEK_MODULES.yaml` | Pulled via `core.identity.assembler`. |
| `customer` | `config/customers.yaml` | Toby-curated. CRM sync is Phase B+. |
| `supplier` | `config/suppliers.yaml` | As above. |
| `material` | `config/materials.yaml` | As above. |
| `error_type` | regex | Common error class names / HTTP codes. |

### Canonicalisation

Canonical form = `lower().strip()` with whitespace collapsed.
`"Flowers By Julie"` and `"flowers by julie"` resolve to the same
node. Unique key in the DB is `(type, canonical_name)`.

### Stop entities

Too ubiquitous to carry signal; a walk through them matches
everything. Default blocklist:

```
toby, jo, deek, nbne, cairn, claude, claude code, deek agent
```

Additions require a PR to `config/entity_taxonomy.yaml`.

## Schema

`migrations/postgres/0002_crosslink_graph.sql`:

- `entity_nodes(id uuid, type, canonical_name, display_name, aliases[], mention_count, first_seen, last_seen)` — `UNIQUE(type, canonical_name)`
- `entity_edges(source_id, target_id, weight, co_occurrence_count, outcome_signal, last_reinforced)` — PK `(source_id, target_id)` with `CHECK source_id < target_id` so each undirected pair is stored once
- `memory_entities(memory_id INTEGER REFERENCES claw_code_chunks(id), entity_id uuid)` — the link table. INTEGER (not UUID) because Deek's memory store is `claw_code_chunks`, not the imaginary `memory_entries` the brief assumed.

## Write path

`api/main.py::_embed_memory_to_pgvector` now calls into
`core.memory.entities.upsert_entities_and_edges` inside the same
transaction as the memory insert. Failure to extract entities is
non-fatal — logged via `logger.warning` and the memory still writes
cleanly without graph links.

### Edge math

- `co_occurrence_count` increments on every reinforcement
- `weight = LEAST(10, weight + 1/(count+1))` — diminishing returns
- `outcome_signal` = running mean of memory outcomes on the edge, in
  `[-1, +1]` (fail = -1, success = +1, unknown = 0)

## Curating the canonical lists

Three files in `config/`:

- `customers.yaml`
- `suppliers.yaml`
- `materials.yaml`

Each entry has a `canonical` display name and an optional `aliases`
list. Short aliases (<3 chars) are skipped at extraction time to
avoid noise. Word-boundary matching prevents substring hits.

To grow the lists:

1. Edit the YAML file
2. Open a PR
3. After merge + deploy, the next API restart picks up the new entries
4. Optionally re-run `scripts/seed_entity_graph.py` to retroactively
   extract the newly-canonical entities from existing memories
   (idempotent — already-populated pairs are no-ops)

## Graph walk at retrieval (Phase B)

On every retrieval:

1. Extract query entities using the same extractor as write-time
2. Resolve canonical names to `entity_nodes.id`
3. BFS 1 hop along all incident edges (scored by `edge_weight *
   (1 + max(-0.9, outcome_signal))`)
4. Optional 2-hop along edges with `weight >= graph_2hop_edge_threshold`
   (default 2.0), with a 0.5× decay
5. Visit `memory_entities` to surface memories linked to walked
   entities
6. Down-weight by `1 / mention_count` so ubiquitous entities don't
   dominate — "Origin Designed" in 50 memories contributes 1/50 of
   the score that "M1234" in 2 memories does

Returns up to `graph_max_memories` candidates (default 10). Config:

```yaml
# config/retrieval.yaml
graph_max_hops: 2
graph_2hop_edge_threshold: 2.0
graph_weight: 0.15
graph_max_memories: 10
```

## Shadow mode

Controlled by `DEEK_CROSSLINK_SHADOW` (default `true`). Under shadow
the walk runs and `data/graph_shadow.jsonl` records:

- Query
- Old top-5 chunk IDs (what retrieval returned)
- Graph top candidates with score + path entities

Analyse with `scripts/analyze_graph_shadow.py`:

```
Records logged:            123
With a graph hit:          34 (27.6%)
Empty walks:               89 (72.4%)
Graph added new memories:  22 of queries
Mean candidates / query:   1.3
Top path entities (degenerate nodes cluster here):
    flowers by julie   14
    beacon             12
    ...
```

Flip live via `DEEK_CROSSLINK_SHADOW=false` + restart API.

## Inspecting the graph (Phase B)

Diagnostic endpoints (no auth, internal network):

- `GET /api/deek/memory/graph/stats` — node count by type, top-10
  edge weights, orphan count
- `GET /api/deek/memory/graph/entity/{id}` — 2-hop neighbourhood for
  one entity
- `GET /api/deek/memory/graph/walk?query=...` — debug: show the walk
  the retriever would run for a query

Direct SQL against `entity_nodes` / `entity_edges` also works.

## Scale expectation (2026-04-19)

16 memory rows today. Graph will be small (<100 nodes, <500 edges)
after Phase A seeds. The brief's pass gate for "structural analogy"
tests is likely to be unreachable at this scale — the infrastructure
is correct but there aren't enough memories yet for cross-domain
patterns to emerge. Revisit when memory volume reaches ~100+.

## Not yet live (Phase C)

- Flipping `DEEK_CROSSLINK_SHADOW=false` after shadow review
- `NBNE_PROTOCOL.md` patch (in `nbne-policy`)

Phase C automation pattern will mirror Impressions Phase C: a
cron-scheduled cutover script with safety gates that runs after
sufficient shadow data accumulates.
