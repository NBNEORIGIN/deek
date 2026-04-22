-- 0009_conversational_reply_shadow.sql
--
-- Conversational reply normaliser (memory brief + triage digest).
-- While DEEK_CONVERSATIONAL_REPLY_SHADOW=true, every normalisation
-- run logs a row here so Toby can audit accuracy before the
-- cutover cron flips shadow off (scheduled 2026-05-06).
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS cairn_intel.conversational_reply_shadow (
  id               BIGSERIAL PRIMARY KEY,
  source           TEXT NOT NULL,         -- 'brief' | 'triage'
  reference_id     TEXT NOT NULL,         -- run_id (brief) or triage_id (triage)
  raw_body         TEXT NOT NULL,
  normalised       JSONB NOT NULL DEFAULT '{}'::jsonb,
  applied          BOOLEAN NOT NULL DEFAULT FALSE,
  toby_reviewed    BOOLEAN NOT NULL DEFAULT FALSE,
  toby_verdict     TEXT,                  -- 'good' | 'partial' | 'wrong' after review
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS conversational_reply_shadow_source_ref_idx
  ON cairn_intel.conversational_reply_shadow (source, reference_id);

CREATE INDEX IF NOT EXISTS conversational_reply_shadow_created_at_idx
  ON cairn_intel.conversational_reply_shadow (created_at DESC);

CREATE INDEX IF NOT EXISTS conversational_reply_shadow_unreviewed_idx
  ON cairn_intel.conversational_reply_shadow (created_at DESC)
  WHERE toby_reviewed = FALSE;
