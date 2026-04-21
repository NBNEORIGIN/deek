-- 0007_triage_similarity_debug.sql
--
-- Triage Phase D — similarity surfacing debug table.
--
-- While DEEK_SIMILARITY_SHADOW is on, find_similar_jobs() runs on
-- every triage digest but the result is NOT rendered. Instead each
-- run logs here so Toby can audit whether the suggestions would have
-- been useful. After a week of review (cutover scheduled 2026-05-05),
-- the cron flips shadow off and the digest starts rendering.
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS cairn_intel.triage_similarity_debug (
  id               BIGSERIAL PRIMARY KEY,
  triage_id        INTEGER NOT NULL,
  enquiry_summary  TEXT NOT NULL,
  candidates       JSONB NOT NULL DEFAULT '[]'::jsonb,
  latency_ms       INTEGER NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Filled in by Phase E / future review tooling: which candidate
  -- (if any) Toby marked useful via the Q5 reply.
  useful_index     INTEGER,
  useful_flagged_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS triage_similarity_debug_triage_id_idx
  ON cairn_intel.triage_similarity_debug (triage_id);

CREATE INDEX IF NOT EXISTS triage_similarity_debug_created_at_idx
  ON cairn_intel.triage_similarity_debug (created_at DESC);
