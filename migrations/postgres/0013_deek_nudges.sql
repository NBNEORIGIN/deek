-- 0013 — Deek-initiated nudges (Telegram channel).
--
-- Queue for proactive Deek-initiated messages to registered staff
-- (Toby first; Jo + Ivan later via user_profile.yaml). Sits in
-- front of the Telegram send path so everything is idempotent,
-- auditable, and shadow-mode-gateable.
--
-- Lifecycle:
--   1. Trigger (cron or event) calls queue_nudge() → row inserted
--      with state='pending'
--   2. Sender cron reads pending rows, sends via Telegram, flips
--      state='sent' (or state='failed' on error)
--   3. User can reply to the bot; inbound webhook correlates by
--      related_ref and flips state='acknowledged' or 'dismissed'
--
-- Shadow mode (DEEK_NUDGES_SHADOW=true) makes the sender log the
-- intended send to state='shadow' instead of actually firing the
-- Telegram API call. Nothing reaches the user. Toby reviews the
-- table for a week to calibrate trigger thresholds before cutover.
--
-- registered_chats maps user_email → Telegram chat_id. Populated
-- the first time each user sends a message to the bot (webhook
-- receives the chat_id + links to the user via a one-off join code).
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS cairn_intel.deek_nudges (
  id             BIGSERIAL PRIMARY KEY,
  trigger_kind   TEXT NOT NULL,           -- 'stalled_project' | 'fresh_enquiry' | ...
  user_email     TEXT NOT NULL,           -- recipient (matches user_profile)
  state          TEXT NOT NULL            -- 'pending' | 'sent' | 'shadow' | 'acknowledged' | 'dismissed' | 'failed' | 'skipped'
                 DEFAULT 'pending',
  message_text   TEXT NOT NULL,
  related_ref    TEXT,                    -- e.g. 'project:cmlp8jpxs0003...' — dedup key
  context_json   JSONB NOT NULL DEFAULT '{}'::jsonb,  -- trigger-specific payload
  cooldown_hours INTEGER NOT NULL DEFAULT 24,         -- suppress duplicates within this window
  telegram_message_id BIGINT,             -- Telegram's id after send
  error_detail   TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at        TIMESTAMPTZ,
  acknowledged_at TIMESTAMPTZ,
  dismissed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS deek_nudges_pending_idx
  ON cairn_intel.deek_nudges (created_at)
  WHERE state = 'pending';

CREATE INDEX IF NOT EXISTS deek_nudges_related_ref_idx
  ON cairn_intel.deek_nudges (related_ref, created_at DESC)
  WHERE related_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS deek_nudges_user_created_idx
  ON cairn_intel.deek_nudges (user_email, created_at DESC);

CREATE INDEX IF NOT EXISTS deek_nudges_trigger_kind_idx
  ON cairn_intel.deek_nudges (trigger_kind, created_at DESC);


-- Registered Telegram chats: one row per (user_email, chat_id).
-- Populated by the webhook when a user first messages the bot
-- with their join code.
CREATE TABLE IF NOT EXISTS cairn_intel.registered_telegram_chats (
  id            BIGSERIAL PRIMARY KEY,
  user_email    TEXT NOT NULL,
  chat_id       BIGINT NOT NULL,
  telegram_username TEXT,
  first_name    TEXT,
  registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at    TIMESTAMPTZ,
  UNIQUE (user_email, chat_id)
);

CREATE INDEX IF NOT EXISTS registered_telegram_chats_user_idx
  ON cairn_intel.registered_telegram_chats (user_email)
  WHERE revoked_at IS NULL;


-- One-off join codes that map a human to a chat_id on first
-- contact. Toby runs a CLI that prints a code; he sends it to
-- the bot from his phone; the webhook consumes the code to
-- register his chat_id.
CREATE TABLE IF NOT EXISTS cairn_intel.telegram_join_codes (
  code         TEXT PRIMARY KEY,
  user_email   TEXT NOT NULL,
  expires_at   TIMESTAMPTZ NOT NULL,
  consumed_at  TIMESTAMPTZ,
  consumed_by_chat_id BIGINT
);

CREATE INDEX IF NOT EXISTS telegram_join_codes_expires_idx
  ON cairn_intel.telegram_join_codes (expires_at)
  WHERE consumed_at IS NULL;
