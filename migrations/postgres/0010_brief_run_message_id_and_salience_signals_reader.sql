-- 0010 — Reply correlation via In-Reply-To + salience signal retrieval.
--
-- Two small fixes bundled because they share the same "make memory
-- actually connected" theme:
--
-- (1) memory_brief_runs.outgoing_message_id
--     Today: reply processor correlates replies to runs by
--     (user_email, date). When two briefs go out on the same day
--     (e.g. the quote-fix flow we hit 2026-04-22) replies get
--     misattributed. Fix: capture the outgoing RFC-5322 Message-ID
--     and match on it. Fallback to date match remains for legacy
--     rows without a stored message-id.
--
-- (2) Partial index on claw_code_chunks.salience_signals->'toby_flag'
--     Today: five call sites WRITE toby_flag=1.0 into salience_signals
--     (brief replies, triage replies, memory corrections). Zero
--     readers in the retrieval path. This index makes the
--     "toby_flag > 0" filter cheap so retriever boosts can land.
--
-- Idempotent. Safe to re-run.

ALTER TABLE memory_brief_runs
  ADD COLUMN IF NOT EXISTS outgoing_message_id TEXT;

CREATE INDEX IF NOT EXISTS ix_memory_brief_runs_outgoing_msgid
  ON memory_brief_runs (outgoing_message_id)
  WHERE outgoing_message_id IS NOT NULL;

-- Partial GIN index on the JSONB column so retriever code can cheaply
-- answer "does this chunk have a toby_flag signal?". Covers the most
-- common predicate; full JSONB scans remain available for ad-hoc
-- queries.
CREATE INDEX IF NOT EXISTS ix_chunks_salience_signals_toby_flag
  ON claw_code_chunks ((salience_signals->>'toby_flag'))
  WHERE (salience_signals->>'toby_flag') IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_chunks_salience_signals_via
  ON claw_code_chunks ((salience_signals->>'via'))
  WHERE (salience_signals->>'via') IS NOT NULL;
