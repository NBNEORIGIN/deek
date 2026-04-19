# Dream state

Overnight, Deek samples high-salience recent memories, pulls distant
graph-connected companions, generates speculative patterns at high
temperature on the local model, and **aggressively filters** them.
A handful of survivors surface in the PWA morning briefing where
Toby accepts, rejects, edits, or defers. Accepted candidates promote
to `schemas` — the same table consolidation writes to. Rejected
candidates train the duplication gate.

Design principle: **free association produces plausible nonsense by
default. The value is in the filter.** Budget ~100 attempts in → ~3
surface. Every surfaced candidate cites specific source memory IDs
and is falsifiable on inspection.

Status: **Phase A — nocturnal loop runnable, no cron, no PWA
surfacing**. Runnable manually via
`python scripts/dream_nightly.py`. Phase B wires cron + the
`/briefing/morning` API + PWA Brief tab extension. Phase C adds the
feedback loop, staleness sweeps, and `nbne-policy` patch.

## Mechanism

### 1. Seed selection

Top N=20 memories from the last 30 days ranked by
`salience × exp(-hours_since_access / 72h)` — same ranking as the
consolidation job (`core/memory/consolidation.py`).

### 2. Distant-pair generation

For each seed, find companions that:

- Share at least one entity with the seed (via `memory_entities`)
- Have cosine similarity to the seed `< 0.4` (topically distant)
- Come from the broader memory pool, not just other seeds

Top 3–5 companions by `(1 − similarity) × salience` become the
bundle. Bundles with fewer than 3 members are dropped.

### 3. Candidate generation

For each bundle, prompt the local LLM (default `qwen2.5:7b-instruct`
via Tailscale to deek-gpu) at temperature 0.9 with
`core/dream/prompts/v1_dream.txt`. The prompt accepts `candidate:
null` for "no pattern" and requires JSON-formatted positive
responses citing ≥3 memory IDs from the bundle.

### 4. Filter

`core/dream/filter.py` runs four gates in order. Candidates failing
any gate are dropped with a breakdown in `filter_signals`:

| Gate | What it catches |
|---|---|
| **Grounding** | <3 sources, or candidate's key terms don't appear in cited memories |
| **Specificity** | Anti-pattern match (platitudes like "customers prefer", "reduce costs") |
| **Actionability** | No entity / channel / price / decision keyword / timeframe |
| **Duplication** | Cosine > 0.85 vs existing active schemas or recent rejected candidates |

`config/dream/anti_pattern_list.yaml` grows over time — each entry
is a data point about what "too generic" means in NBNE's context.

### 5. Scoring

```
score = 0.4 * confidence
      + 0.2 * min(1.0, source_memory_count / 10)
      + 0.2 * entity_type_diversity
      + 0.2 * (1.0 if actionability_ok else 0.0)
```

`entity_type_diversity`: 1.0 if sources span 3+ entity types, 0.6
for 2, 0.3 for 1, 0 otherwise.

Top K by score → surfaced (`surfaced_at = NOW()`). Others persist
for retrospective review.

## Schema

`migrations/postgres/0003_dream_candidates.sql`:

```
dream_candidates(
  id UUID PRIMARY KEY,
  candidate_text TEXT,
  candidate_type TEXT,            -- pattern | rule | analogy | prediction
  source_memory_ids INTEGER[],    -- claw_code_chunks.id
  source_entity_ids UUID[],       -- entity_nodes.id
  generation_temperature REAL,
  generation_model TEXT,
  confidence REAL,
  filter_signals JSONB,           -- per-gate breakdown
  score REAL,
  generated_at TIMESTAMPTZ,
  surfaced_at TIMESTAMPTZ,        -- NULL if not in top K
  reviewed_at TIMESTAMPTZ,
  review_action TEXT,             -- accepted|rejected|edited|deferred|expired
  review_notes TEXT,
  promoted_schema_id UUID REFERENCES schemas(id)
)
```

## Running manually

```bash
# Full run
python scripts/dream_nightly.py

# Dry run — everything except the DB writes
python scripts/dream_nightly.py --dry-run

# Smaller seed set for testing
python scripts/dream_nightly.py --seed-limit 5 --max-attempts 10
```

Cost: zero cloud calls; all inference local.

## Scale caveat

At 16 memories and 6 entity graph nodes, the loop will produce
**zero or near-zero candidates most nights**. That's not a bug.
Seeds exist, bundles may form, but either no shared entities surface
distant companions, or the filter kills the generator's output.
Build now; observe; tune once memory volume reaches ~100+.

## Not in Phase A

- Cron scheduling → Phase B
- `GET /briefing/morning` API endpoint → Phase B
- PWA Brief tab extension for candidate review → Phase B
- Feedback loop (accept promotes to schema, reject trains dedupe) → Phase C
- Stale-candidate sweep (7-day auto-archive) → Phase C
- Schema decay (90-day → dormant, 180-day → archived) → Phase C
- Postmark daily digest → Phase C
- `NBNE_PROTOCOL.md` patch → Phase C

## Files

```
core/dream/__init__.py
core/dream/nocturnal.py           seed → bundle → generate → persist
core/dream/filter.py              grounding / specificity / actionability / dedupe + scoring
core/dream/prompts/v1_dream.txt   prompt template
config/dream/anti_pattern_list.yaml
scripts/dream_nightly.py          entry point
migrations/postgres/0003_dream_candidates.sql
tests/memory/test_dream_filter.py
```
