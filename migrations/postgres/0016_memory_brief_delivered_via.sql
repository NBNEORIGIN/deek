-- 0016 — delivered_via column on memory_brief_runs.
--
-- Memory Brief can now deliver via Telegram as well as email. The
-- send path picks the channel from config/brief/user_profiles.yaml
-- per recipient; this column records what was actually used so the
-- inbound Telegram webhook can find the "most recent brief awaiting
-- reply" for a given user without guessing.
--
-- Existing rows all default to 'email' (current behaviour).
--
-- Idempotent. Safe to re-run.

ALTER TABLE memory_brief_runs
  ADD COLUMN IF NOT EXISTS delivered_via TEXT NOT NULL DEFAULT 'email';

-- Lookup index: "what's the most recent unreplied Telegram brief
-- for this user?" — covers the webhook hot path.
CREATE INDEX IF NOT EXISTS ix_memory_brief_runs_unreplied_telegram
  ON memory_brief_runs (user_email, delivered_at DESC)
  WHERE delivery_status = 'sent' AND delivered_via = 'telegram';
