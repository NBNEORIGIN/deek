-- 0005_memory_brief.sql
--
-- Memory Brief Phase A — daily human-in-the-loop memory audit.
--
-- Deek generates 1-4 questions per morning from live memory state
-- (low-access schemas for belief audit, recent consolidations for
-- gist validation, yesterday's high-salience memories for salience
-- calibration, plus an always-on open-ended prompt), sends them to
-- Toby by email, and in Phase B parses replies back into memory
-- corrections.
--
-- Schema notes:
--   * user_email not tenant_id — per strategic preamble, no
--     multi-tenant scaffolding until Brian/Phloe Pro actually land.
--   * questions JSONB stores the full question-set + provenance
--     (which schema_id / memory_id each question references), so
--     Phase B's reply parser can write answers back to the right
--     row without guessing.
--   * run identified by (user_email, generated_at::date) — the
--     idempotency guard uses this.
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS memory_brief_runs (
  id UUID PRIMARY KEY,
  user_email TEXT NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  questions JSONB NOT NULL DEFAULT '[]'::jsonb,
  subject TEXT,
  body_text TEXT,
  delivery_status TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | failed | dry_run
  delivered_at TIMESTAMPTZ,
  error TEXT,
  dry_run BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_memory_brief_runs_user
  ON memory_brief_runs (user_email, generated_at DESC);

CREATE INDEX IF NOT EXISTS ix_memory_brief_runs_status
  ON memory_brief_runs (delivery_status, generated_at DESC);

-- NOTE: idempotency (one non-dry-run per user per day) is enforced
-- in application code — scripts/send_memory_brief.py queries for an
-- existing successful send before inserting a new row. A SQL-level
-- UNIQUE index on (user_email, generated_at::date) would be neater
-- but Postgres refuses to index on the timezone-dependent `::date`
-- cast (not IMMUTABLE). The app-level check is sufficient because
-- the script is the only writer.


-- Responses are populated by Phase B (reply parser hooks into the
-- existing cairn@ inbox poll). Phase A creates the table so the
-- shape is known.
CREATE TABLE IF NOT EXISTS memory_brief_responses (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES memory_brief_runs(id) ON DELETE CASCADE,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  raw_body TEXT NOT NULL,
  parsed_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
  applied_at TIMESTAMPTZ,
  applied_summary JSONB
);

CREATE INDEX IF NOT EXISTS ix_memory_brief_responses_run
  ON memory_brief_responses (run_id);

CREATE INDEX IF NOT EXISTS ix_memory_brief_responses_unapplied
  ON memory_brief_responses (received_at DESC)
  WHERE applied_at IS NULL;
