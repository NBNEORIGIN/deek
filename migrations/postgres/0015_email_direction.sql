-- 0015 — direction column on cairn_email_raw.
--
-- Phase B of the thread-association feature: Deek polls Toby's
-- Sent folder via IMAP so outbound messages also get ingested,
-- indexed, and cross-referenced against the thread→project map.
--
-- Distinguishing direction:
--   'inbound'  — received at a monitored mailbox (cairn@, etc.)
--   'outbound' — sent by a monitored user (toby@, etc.) and
--                pulled from their Sent folder
--
-- All existing rows are inbound — backfill default.
--
-- Idempotent. Safe to re-run.

ALTER TABLE cairn_email_raw
  ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'inbound';

CREATE INDEX IF NOT EXISTS cairn_email_raw_direction_idx
  ON cairn_email_raw (direction, received_at DESC);

CREATE INDEX IF NOT EXISTS cairn_email_raw_thread_direction_idx
  ON cairn_email_raw (thread_id, direction)
  WHERE thread_id IS NOT NULL;
