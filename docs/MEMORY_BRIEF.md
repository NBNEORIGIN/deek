# Memory Brief

Every morning, Deek emails a small set of questions grounded in live
memory state. Replies (Phase B) parse back into memory corrections —
closing the loop between algorithmic beliefs and human ground truth.

**Status: Phase A — send only, no reply parsing yet.** Trial phase
(Toby only). Cron starts in `--dry-run` mode so the first 3 days are
logged to stdout without sending email; after reviewing question
quality the `--dry-run` flag comes off the cron.

## Why this exists

The memory layer (Briefs 2–4) builds sophisticated machinery —
salience, schemas, graphs, dreams — on top of a corpus that hasn't
been audited for quality. Algorithmic salience has a rich-get-richer
problem; consolidation can distil plausible-sounding gists that are
subtly wrong; the graph can link nodes that should be merged. The
Memory Brief is the human-in-the-loop correction channel.

It's also cheaper and more reliable than Brief 4's fully-autonomous
dream state for surfacing non-obvious connections — human pattern
recognition is hard to hallucinate.

## Question sources (Phase A)

Four categories, 1–4 questions per day depending on what's in the DB:

| Category | Source query | Skipped when |
|---|---|---|
| `belief_audit` | active schema, `access_count <= 2`, derived >= 2 days ago | no eligible schemas |
| `gist_validation` | active schema, confidence 0.7-0.9, derived 7–30 days ago | no eligible schemas |
| `salience_calibration` | memory-bearing chunk with `salience > 3.0`, indexed in last 36h | no high-salience recent memory |
| `open_ended` | none — always included | never |

Question-set always contains at least `open_ended`. Most days at low
memory volume will be 1-2 questions; this is correct — padding with
weak questions trains the reader to ignore the email.

Every non-open question carries `provenance` (schema_id or memory_id)
so Phase B's reply parser can write answers back to the correct row.

## Templates

`config/brief/templates.yaml` — versioned prompt + reply-format per
category. Changes are a PR, not a runtime setting. Loaded on each
run so merge-to-main applies on the next morning's brief without a
deploy.

## Scheduling

Hetzner cron, 07:30 UTC daily:

```cron
# Deek memory brief — Tier 1 (Toby only, trial phase)
30 7 * * * docker exec -w /app -e PYTHONPATH=/app deploy-deek-api-1 \
  python scripts/send_memory_brief.py --user toby@nbnesigns.com --dry-run \
  >> /var/log/deek-memory-brief.log 2>&1
```

**Remove `--dry-run` after 3 days of reviewed output** (around
2026-04-23). Dry-run mode still writes to `memory_brief_runs` with
`delivery_status='dry_run'` so the question quality can be inspected.

## Idempotency

The unique index `ux_memory_brief_runs_user_date` prevents duplicate
successful sends on the same day. Failed sends can be retried
(they're excluded from the uniqueness constraint). `--force`
overrides the check for manual re-sends.

## Reply handling (Phase B)

Replies to the email go to `cairn@nbnesigns.com`, which is polled
every 15 minutes by the existing inbox infrastructure. Phase B adds
a parser that:

1. Recognises brief-reply emails by the `[Deek brief]` subject
   prefix (Phase B adds this; Phase A subjects don't carry it)
2. Splits the body by `--- Q<n> ---` delimiters
3. For each answer block, looks up the provenance from
   `memory_brief_runs.questions` and writes the correction to the
   relevant schema / memory row
4. Records the parse in `memory_brief_responses`

Phase A leaves replies accumulating in the inbox for manual review
until the parser lands.

## Tier expansion (Phase C)

Tier 1 — Toby (daily, trial now).
Tier 2 — Jo + Ivan (daily, once Tier 1 proves). Code change: add
additional `--user` crons, each with its own user-specific question
sources (e.g. Jo's brief emphasises relationship memories; Ivan's
emphasises production specifics).
Tier 3 — Gabby, Sanna, Ben (triggered ad hoc by specific events —
job completions, material issues — not scheduled). Separate code
path that emits `memory_brief_runs` rows on triggers, not on cron.

None of this is in Phase A.

## Files

```
migrations/postgres/0005_memory_brief.sql
config/brief/templates.yaml
core/brief/__init__.py
core/brief/questions.py
core/brief/composer.py
scripts/send_memory_brief.py
tests/memory/test_memory_brief.py
```

## Failure modes (logged, never silent)

| Failure | Behaviour |
|---|---|
| DB unreachable at start | Exit 1 before anything else |
| DB reachable, no eligible schemas for a category | Skip that category, log reason in `notes`, include in email for debugging |
| SMTP unset and not dry-run | `delivery_status='failed'`, `error` populated, exit 1 |
| SMTP transient failure | Same — next run retries (not blocked by idempotency index) |
| Template file missing/broken | Individual categories skip; open-ended falls back to hard-coded prompt |
| Run already sent today | Exit 0 silently (idempotent no-op unless `--force`) |

## Dry-run review workflow

```bash
ssh root@178.104.1.152
docker exec -w /app -e PYTHONPATH=/app deploy-deek-api-1 \
  python scripts/send_memory_brief.py --user toby@nbnesigns.com --dry-run -v
```

Inspect the printed email, the recorded `memory_brief_runs` row
(`SELECT * FROM memory_brief_runs ORDER BY generated_at DESC LIMIT 3`),
and the per-category notes for "why did we skip this category today".

Happy with the output? Remove `--dry-run` from the crontab entry.
