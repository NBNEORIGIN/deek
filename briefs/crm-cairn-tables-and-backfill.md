# CRM Brief — Restore cairn-integration tables + run embeddings backfill

**Target repo:** CRM (`D:\crm` / `NBNEORIGIN/crm`)
**Module:** CRM
**Consumer:** Claude Code (CRM session)
**Protocol:** Follow `NBNE_PROTOCOL.md`.
**Urgency:** Low — Deek ran the idempotent fix so the endpoints
are alive. But search returns 0 results until backfill runs.
**Origin:** Deek session 2026-04-23 investigation after Deek + the
chat agent both hit consistent 500 errors on `/api/cairn/search`
and `/api/cairn/memory`.

---

## What Deek already did (accept or override)

Deek ran `docker exec deploy-deek-db-1 psql ... -f create_cairn_tables.sql`
against the Hetzner `crm` database on 2026-04-23 to restore the
two missing tables:

- `crm_embeddings` (+ HNSW cosine index, GIN trigram, GIN tsvector,
  unique (source_type, source_id) index)
- `cairn_recommendations` (+ project and active indexes)
- `emails` (the forward-compat table in the same script; harmless
  if unused)

Script used was the idempotent `CREATE TABLE IF NOT EXISTS`
version already committed at `scripts/create_cairn_tables.sql`.
Nothing was dropped. Verification:
`POST /api/cairn/memory` now returns 201 with the new row.

If you'd rather the CRM session owns restoration: these tables
can be dropped + recreated with no data loss since they were
empty. But there's no reason to undo what's there.

---

## Root cause

1. `cairn_recommendations` and `crm_embeddings` aren't in the
   Prisma schema — they're created by the manual SQL script at
   `scripts/create_cairn_tables.sql`, ran once-ever when the
   CRM v2 stack was stood up on nbne1.
2. The CRM later moved to Hetzner (`deploy-deek-db-1:5432/crm`)
   and the Prisma schema was pushed to the new DB — but the
   non-Prisma init SQL was never replayed on the new host.
3. Phase A of the quote generator used `prisma db push` to
   work around a broken migration
   (`20260418130000_project_attribution_fields`), which didn't
   apply the missing cairn-integration tables either.
4. Search + memory-write started 500-ing this afternoon, blocking
   write_crm_memory + search_crm tool calls from Deek chat.

---

## Tasks

### Task 1 — Integrate `create_cairn_tables.sql` into deploy flow

The SQL script being outside Prisma is the root cause. Two
acceptable resolutions:

**(a) Keep SQL separate, auto-apply on deploy**
- Add a step in the CRM Docker entrypoint or deploy script that
  runs `psql -f /app/scripts/create_cairn_tables.sql` against the
  cairn DB on container start. `CREATE TABLE IF NOT EXISTS` is
  safe to re-run.

**(b) Fold into Prisma (preferred long-term)**
- Model the `crm_embeddings`, `cairn_recommendations`, `emails`
  tables in `prisma/schema.prisma`
- Add `extensions = [vector, pg_trgm]` to Prisma's datasource
  block (Prisma 5.5+ supports this)
- Create a new `prisma/migrations/YYYY_MM_DD_cairn_integration/`
  migration that idempotently creates the structures (empty
  CREATE IF NOT EXISTS + raw SQL for the vector + HNSW bits that
  Prisma can't model natively)
- Remove the standalone script once migrated

(b) is nicer but requires teaching Prisma about the vector type
via `@db.Vector(768)`. (a) is 30 mins of work, (b) is a day's.
Recommend (a) now, file a follow-up brief for (b).

### Task 2 — Run the embeddings backfill

Script exists: `scripts/backfill-embeddings.ts`. Re-indexes every
Project / Client / ClientBusiness / Material / LessonLearned /
Quote into `crm_embeddings`. At current scale (~100 projects, ~50
clients, ~50 lessons, ~800 emails) this costs roughly $1-2 of
OpenAI embedding calls + takes ~5 minutes.

Run:
```bash
cd /opt/nbne/crm
docker exec crm-crm-1 npx tsx scripts/backfill-embeddings.ts
```

Verify:
```sql
SELECT source_type, COUNT(*) FROM crm_embeddings GROUP BY 1;
```

Expected: rows across all six source_types.

### Task 3 — Fix the broken migration

Flagged in the Phase A handoff but not addressed:
`prisma/migrations/20260418130000_project_attribution_fields/`
fails against the shadow DB, forcing `prisma db push` as a
workaround. Every subsequent schema change now has the same
problem. Fix the migration file so `prisma migrate dev` works
cleanly.

### Task 4 — Tests

- Unit: init flow applies the SQL idempotently (run twice → no error)
- Integration: search returns results > 0 after backfill on a
  populated DB
- Regression: existing `/api/cairn/*` endpoints still pass

### Deliverable

Single PR covering tasks 1-4. Tasks 2 (backfill) is a one-off
ops command — run it, confirm search works, don't need a PR for
just the run. Tasks 1, 3, 4 go in a PR.

---

## Out of scope

- Changing Deek. Deek's already consuming `/api/cairn/search` and
  `/api/cairn/memory` correctly — the failure was purely DB-side.
- Phase B of the quote generator (line-item editor, catalogue
  autocomplete) — still the priority follow-up from the Phase A
  handoff.
- Phloe WhatsApp brief (`briefs/phloe-whatsapp-booking-reminders.md`
  committed in the Deek repo 2026-04-22) — separate, independent.

---

## Constraints

- No Prisma `db push` in production going forward. Fix Task 3
  first; the broken migration is the reason Task 1 exists.
- Backfill runs against live DB — verify no concurrent write
  operations would conflict before kicking off.
- Keep the script idempotent — Deek's fix relies on
  `CREATE TABLE IF NOT EXISTS` semantics.

---

## Rules of engagement

Stay in the CRM repo. Do NOT modify Deek — Deek's diagnosis +
idempotent restoration are recorded; any further action is
CRM-side. If Task 1 turns into Task 2 (the full Prisma
integration), spin it out as a separate brief — don't silently
expand scope.
