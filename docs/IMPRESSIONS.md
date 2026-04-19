# Impressions layer

Deek's retrieval layer weights memories by **relevance × salience ×
recency**, not relevance alone. Strong impressions last longer;
unused memories fade; repetition becomes schema.

Status: **Phase B — shadow mode**. The ranker computes the new ordering
on every retrieval but returns the OLD ordering to callers. Shadow
data lands in `data/impressions_shadow.jsonl` for review before the
flip. Nightly consolidation, schema retrieval, and diagnostic
endpoints are live as of Phase B; cutover (shadow off) is Phase C.

## Three components

### 1. Salience at write time

`core/memory/salience.py` scores each memory write on five signals:

| Signal | Weight | What it catches |
|---|---:|---|
| `money` | 2.5 | numeric amounts in £/$/€, log-scaled |
| `customer_pushback` | 2.0 | keyword-based friction (complaint, refund, escalate, rework) |
| `outcome_weight` | 3.0 | explicit outcome — failures and deferrals score higher than wins |
| `novelty` | 1.5 | `1 − max_cosine` against the last 100 memories |
| `toby_flag` | 5.0 | hard star flag in metadata |

Weights live in `config/salience.yaml`. The extractor runs **only** on
memory-bearing chunk types (`memory`, `email`, `wiki`,
`module_snapshot`, `social_post`). Code chunks keep `salience = 1.0`
so retrieval ordering cannot downgrade code relative to where it was
pre-Brief-2.

Final salience is `base_score + Σ(weight × signal)`, clipped to
[0, 10]. Budget: <50ms median per write, no LLM call.

### 2. Reranking at retrieval time

`core/memory/impressions.py::rerank()` applies after RRF fusion:

```
final = α · relevance + β · salience + γ · recency
```

Each term is min-max normalised within the candidate set, so weights
are meaningful regardless of absolute RRF score magnitude. Defaults:

```yaml
# config/retrieval.yaml
alpha: 0.5       # relevance
beta:  0.25      # salience
gamma: 0.25      # recency
tau_hours: 72.0  # recency half-life
top_k: 20
```

`recency = exp(-hours_since_last_access / tau)`. With `tau = 72`,
a memory read 3 days ago scores 0.37 on recency; one read just now
scores 1.0.

### 3. Reinforcement

Every retrieval that returns a memory-bearing chunk triggers an
async write-back:

```
access_count       += 1
last_accessed_at    = NOW()
salience            = min(10.0, salience + 0.1)
```

Fire-and-forget on a daemon thread so it never blocks the response.
Only reinforces memory-bearing chunks — code chunks don't gain
salience from being read.

## Shadow mode

Controlled by `DEEK_IMPRESSIONS_SHADOW` (default `true`). When shadow:

- Ranker runs, new ordering computed
- Old (pre-Brief-2) ordering is returned to the caller
- A JSONL record lands in `data/impressions_shadow.jsonl` with
  both top-5s and the per-candidate signal breakdown

Review the shadow log; once satisfied the new ordering is better,
set `DEEK_IMPRESSIONS_SHADOW=false` and redeploy.

## Schema

Migration `migrations/postgres/0001_impressions_layer.sql` adds:

- `claw_code_chunks.salience REAL DEFAULT 1.0`
- `claw_code_chunks.last_accessed_at TIMESTAMPTZ DEFAULT NOW()`
- `claw_code_chunks.access_count INTEGER DEFAULT 0`
- `claw_code_chunks.salience_signals JSONB DEFAULT '{}'`
- New `schemas` table (populated in Phase B by the nightly
  consolidation job — empty for now)

Applied automatically at API startup by
`core/memory/migrations.py`. Idempotent — safe to re-run.

## Nightly consolidation (Phase B)

`core/memory/consolidation.py` + `scripts/consolidate_memories.py` run
nightly via Hetzner cron at 02:00 UTC. Each pass:

1. Samples up to 50 memories from the last 30 days ranked by
   `salience × exp(-hours_since_access / 72h)`.
2. Clusters them with single-link agglomerative clustering over
   pairwise cosine similarity (threshold 0.55).
3. For each cluster of ≥3 members, asks the local Ollama model
   (`qwen2.5:7b-instruct` via Tailscale to deek-gpu) to distil a
   recurring pattern.
4. Filters: confidence ≥ 0.7, ≥3 source memories (IDs grounded in
   the cluster — not hallucinated).
5. Dedupes against existing active schemas (cosine > 0.9 = duplicate,
   skip).
6. Writes survivors to the `schemas` table.

Hard cap: 500 active schemas. Overflow demotes the lowest-salience
actives to `dormant`.

Cost: zero cloud calls. Every run is bounded by `max_schemas=10`
writes so a bad clustering day can't flood the table.

Cron entry (Hetzner `/etc/crontab` snippet):

```cron
# Deek impressions — nightly memory consolidation at 02:00 UTC.
# Samples high-salience recent memories, clusters, distils via
# local Ollama, writes schemas. Zero cloud cost.
0 2 * * * docker exec -w /app -e PYTHONPATH=/app deploy-deek-api-1 \
  python scripts/consolidate_memories.py \
  >> /var/log/deek-consolidation.log 2>&1
```

Last-run metadata is logged to `data/consolidation_runs.jsonl` and
surfaced via `GET /memory/consolidation/last-run`.

## Schema retrieval (Phase B)

When a query looks **strategic** (keyword match on architecture,
decision, principle, pattern, plan, etc., OR length ≥ 20 tokens),
the retriever also pulls top-3 active schemas by cosine similarity
and appends them to the result list tagged `chunk_type='schema'`.

Schemas carry a 1.5× score boost because they're distilled — each
row is richer per token than a raw memory.

Reinforcement also applies: every retrieved schema gets
`access_count += 1`, `last_accessed_at = NOW()`, `salience += 0.1`.

## Diagnostic endpoints (Phase B)

- `GET /api/deek/memory/salience/distribution` — histogram of
  salience across memory-bearing chunks. Sanity-check the extractor
  isn't producing all-1.0 (not firing) or all-10.0 (weights too
  hot).
- `GET /api/deek/memory/schemas/active` — active schemas with
  confidence, source count, recent access.
- `GET /api/deek/memory/consolidation/last-run` — most recent
  run summary from `data/consolidation_runs.jsonl`.

No auth — internal network only.

## Phase C — automated cutover

Cutover is pre-baked and scheduled. A one-shot Hetzner cron entry
fires on **2026-04-26 at 09:00 UTC**:

```cron
# Deek impressions Phase C — ONE-SHOT cutover scheduled for 2026-04-26
0 9 26 4 * cd /opt/nbne/deek && python3 scripts/impressions_cutover.py \
  >> /var/log/deek-phase-c-cutover.log 2>&1
```

When it fires, `scripts/impressions_cutover.py`:

1. Runs `scripts/analyze_impressions_shadow.py` against
   `data/impressions_shadow.jsonl` — reports records, span, top-1
   agreement, top-5 Jaccard, per-signal impact.
2. Applies safety gates:
   - `>= 100` shadow records logged
   - `>= 72h` span between first and last record
   - `0.02 < top-5 Jaccard < 0.98` (neither rerank-is-identity nor
     rerank-is-pathological)
   - Env file writable, container running
3. If ALL pass: rewrites `/opt/nbne/deek/deploy/.env` setting
   `DEEK_IMPRESSIONS_SHADOW=false`, restarts `deploy-deek-api-1` via
   `docker compose up -d --force-recreate`, runs
   `scripts/sync-policy.sh` to pull the (by-then-merged) policy patch.
4. Writes a record to `data/impressions_cutover.jsonl`.

If **any** gate fails, the script exits 0 silently with a written
reason — no retries, no noise. Human review decides whether to
re-run with `--force` or adjust config and wait longer.

### Companion PR

`NBNEORIGIN/nbne-policy#1` opens the Identity Layer + Impressions
Layer backport for `NBNE_PROTOCOL.md`. The cutover's `sync-policy`
step pulls whichever state that PR is in on the day.

### Cancelling or rescheduling

To skip the automated cutover:

```bash
ssh root@178.104.1.152
crontab -l | grep -v 'Phase C' | grep -v 'impressions_cutover' | crontab -
```

To run it manually before the scheduled date (once shadow data
exists):

```bash
ssh root@178.104.1.152
python3 /opt/nbne/deek/scripts/impressions_cutover.py --dry-run  # preview
python3 /opt/nbne/deek/scripts/impressions_cutover.py            # apply
```

## Files

```
core/memory/salience.py          extractor + signal scorers
core/memory/impressions.py       rerank + reinforcement + shadow
core/memory/migrations.py        Postgres migration bootstrapper
config/salience.yaml             weights
config/retrieval.yaml            rerank weights + tau
migrations/postgres/             numbered idempotent SQL
tests/memory/                    unit tests (47 passing)
```

## Tuning

Both config files hot-apply on next API restart. Start with the
defaults; after 1 week of shadow data you'll know whether `alpha`
should be higher (retrieval is already well-targeted) or lower
(salience and recency add genuine signal). Log lives at
`data/impressions_shadow.jsonl`.
