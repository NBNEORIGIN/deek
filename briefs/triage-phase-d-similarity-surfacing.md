# Triage Phase D — Similar-past-jobs surfacing

**Target repo:** Deek (`D:\claw` / `NBNEORIGIN/deek`)
**Module:** Deek (this repo — no CRM edits)
**Consumer:** the Deek CC session executes this
**Protocol:** `NBNE_PROTOCOL.md`
**Originating work:** Triage Phases A/B/C (all live on Hetzner as of
  2026-04-21, commit `d50f6d5`)

---

## Why this brief exists

Phases A/B/C closed the loop for the **current** email — match, draft,
confirm, write-back, fold folder-path onto the project record. What
they don't do yet is help Toby with the *quoting decision itself*.

When a customer writes "how much for illuminated fascia signs for a
new coffee shop in Morpeth?" the information Toby wants alongside the
triage digest is: *the three most similar jobs we've already done*,
with their quoted prices, lead times, specs, and outcomes. That's what
"similarity surfacing" means in this programme.

The data is already in CRM — projects have quotes, specs, notes,
outcomes. The retrieval surface (`/api/cairn/search`) already does
pgvector + BM25 hybrid. The gap is connecting the triage digest to
that surface with a *different query shape* than the project-match
query uses today.

---

## What's already in place (do not redo)

- `scripts/email_triage/project_matcher.py` calls
  `/api/cairn/search` with `types=['project', 'client']` filtered by
  sender/client name match. **That's the "which existing project is
  this?" question.** Do NOT overload it.
- The digest already surfaces the top 3 project candidates for human
  confirmation (`_build_candidates_block`).
- Reply parser in `core/triage/replies.py` knows the 4-question block
  shape and writes back to CRM + memory.
- Phase C folder column lets us (eventually) scope similarity by "has
  a real project folder" = "a job we actually worked on" signal.

---

## Pre-flight self-check

Before writing code:

1. Read `CLAUDE.md`, `NBNE_PROTOCOL.md`, `core.md` for the current
   retrieval / memory contract.
2. Confirm `/api/cairn/search` accepts a `types` filter that includes
   `project` AND a free-text `query` argument with the CRM body /
   specs indexed (not just project name). If specs aren't indexed
   yet, that's a scope cut — flag before proceeding.
3. Read `scripts/email_triage/digest_sender.py` to see where the new
   "Similar past jobs" block should render.
4. Read the existing classifier output (`scripts/email_triage/classifier.py`)
   — the enquiry summary Qwen produces is the natural query text.
5. Report findings before Task 1.

---

## Tasks

### Task 1 — Similarity query helper

New module `core/triage/similar_jobs.py` exporting:

```python
def find_similar_jobs(
    enquiry_summary: str,
    *,
    client_id: str | None = None,
    exclude_project_id: str | None = None,
    limit: int = 3,
    min_score: float = 0.02,
) -> list[SimilarJob]:
    """Return top-N past projects most similar to this enquiry.

    Excludes the project we already matched (if any) so we don't
    recommend a job to itself. If ``client_id`` is set, BIAS toward
    same-client jobs but do not exclude others — cross-client
    similarity is often more useful (the Morpeth coffee shop matters
    even if it's a different client).
    """
```

Implementation:
- POST to CRM `/api/cairn/search` with:
  - `query = enquiry_summary` (Qwen-generated 1-2 sentence summary
    from the classifier, not the raw email)
  - `types = ['project']`
  - `limit = limit * 3` so we can filter + rerank locally
- Rerank results locally: boost projects with `localFolderPath` set
  (Phase C signal — "this is a real tracked job"), boost same-client
  jobs by +0.1 score.
- Drop results scoring below `min_score` post-rerank.
- Return structured `SimilarJob` dataclass:
  ```python
  @dataclass
  class SimilarJob:
      project_id: str
      project_name: str
      client_name: str | None
      quoted_amount: float | None     # from CRM project record
      quoted_currency: str             # default 'GBP'
      lead_time_days: int | None
      status: str | None               # 'quoted' | 'won' | 'lost' | 'in_progress'
      summary: str                     # 1-sentence distillation
      score: float
  ```

Fields that aren't in the CRM search response get fetched in a
second call — `GET /api/cairn/projects/{id}` — but **only if the
first call returns ≤ `limit` candidates** (avoid N+1 on every digest).

### Task 2 — Digest block

Extend `digest_sender.py` with `_build_similar_jobs_block(jobs)`
rendering:

```
--- Similar past jobs (top 3) ---

1. [M1234] Flowers by Julie — Alnwick
   Internal window + fascia signs
   Quoted £2,850 · lead 14 days · won
   Match: 0.67

2. [M1102] Napco Pizza — Amble
   Internal + external signage package
   Quoted £4,200 · lead 21 days · won
   Match: 0.41

3. [M0987] Demnurse — Morpeth
   Shopfront signs + vehicle livery
   Quoted £3,150 · lead 18 days · lost
   Match: 0.33
```

Render between the candidate block and the 4-question block. If
`find_similar_jobs` returns empty, render nothing (not "no similar
jobs found" — reduce noise).

### Task 3 — Reply-back Q5 (optional)

Add an **optional** fifth question to the reply-back block:

```
--- Q5 (similar_job_useful) ---
[which number was useful for this quote? 1/2/3 or SKIP]
```

Parser in `core/triage/replies.py` learns this category. When Toby
answers, write a structured memory note:

```
update_memory(
  project='deek',
  note=f'Triage row {row_id} enquiry for {client} matched similar '
       f'job {similar_job_id} ({similar_job_name}) — marked useful '
       f'by Toby. Quote shape: £{amount}, {days}d lead, {status}.',
  tags=['triage', 'similarity', 'toby_flag'],
)
```

This is the **learning signal** for future ranking — Phase E will
aggregate these into a cross-client similarity graph. For Phase D,
just capture the signal; don't act on it yet.

### Task 4 — Shadow mode first

Gate the new block behind `DEEK_SIMILARITY_SHADOW=true`. In shadow
mode:
- `find_similar_jobs` runs
- Results logged to `cairn_intel.triage_similarity_debug` table (new
  migration) with `row_id`, `enquiry_summary`, `candidates` JSONB,
  `latency_ms`
- Digest email does NOT render the block

Toby reviews the debug table for a week. If the similarity quality is
right, flip shadow off via a one-line cron cutover (same pattern as
Impressions + Crosslink).

Migration: `migrations/postgres/NNNN_triage_similarity_debug.sql`.

### Task 5 — Memory / write-back

Standard `update_memory(...)` per `NBNE_PROTOCOL.md` Step 4. The
`decision` field should capture:
- Whether CRM `/api/cairn/search` exposes project body/specs (if
  not, what you did instead — embed summaries on the fly? defer?)
- The reranking weights chosen + why
- The shadow cutover date (default 2026-05-05, one week after
  deploy)

### Task 6 — Tests

- Unit: reranker boosts folder-path + same-client jobs as expected
- Unit: `SimilarJob` dataclass round-trips through JSONB
- Integration (opt-in): live CRM call against known test project id,
  expects ≥ 1 similar job back
- Regression: existing Phase A/B/C tests still pass
- Shadow-mode: digest rendering with `DEEK_SIMILARITY_SHADOW=true`
  omits the block

### Deliverable

Single PR on Deek repo with:
- `core/triage/similar_jobs.py`
- digest renderer extension
- reply-parser Q5 handler
- migration for debug table
- tests green
- shadow mode default-on
- cutover cron scheduled for 2026-05-05 (mirrors impressions pattern)

---

## Out of scope

- CRM-side work. Assume `/api/cairn/search` already indexes project
  bodies + specs. If it doesn't, stop and write a spanning brief.
- Cross-client aggregation / learning. That's Phase E.
- Chat UI surfacing (this lands in email digest only for now).
- Similarity for non-email triggers (manual chat queries already use
  retrieve_codebase_context).

---

## Constraints

- One extra CRM round-trip per digest max (no N+1 fetches)
- P95 latency for the similarity block < 2s — if exceeded, drop the
  block for that digest, log to debug table, continue
- Never surface lost/cancelled jobs above won jobs of the same score
- Respect the existing Deek cost discipline — Qwen local for
  summarisation, no Claude API calls on the triage path

---

## Rules of engagement

Stay in Deek repo. Do NOT touch CRM search implementation — if its
shape is wrong for this use case, write a spanning brief. Do NOT
touch the Phase A/B/C surfaces except to ADD the new block and the
new Q5 category.

Phase D is shadow-first by default. If you find yourself wanting to
skip shadow mode "because the match quality feels right" — you are
wrong. The discipline is: shadow → review → cutover cron. Always.
