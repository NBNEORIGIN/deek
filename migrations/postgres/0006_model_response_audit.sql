-- 0006_model_response_audit.sql
--
-- Brief 1a.2 Phase B Task 5 — structured logging of every model
-- response. The ground truth for diagnosing regressions of the
-- "voice path forgot its identity" class without having to reproduce
-- them live.
--
-- One row per response. 30-day retention enforced by a lightweight
-- cleanup in the writer (see core/memory/response_audit.py).
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS model_response_audit (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  path TEXT NOT NULL,                  -- chat | voice | voice_stream | mcp | api
  session_id TEXT,
  model TEXT,

  -- System-prompt integrity
  system_prompt_hash TEXT NOT NULL,    -- sha256 of the prompt actually sent
  identity_hash TEXT,                  -- current DEEK_IDENTITY.md + DEEK_MODULES.yaml hash
  identity_prefix_present BOOLEAN NOT NULL,

  -- Response shape
  response_length_chars INTEGER NOT NULL DEFAULT 0,
  response_contains_non_answer BOOLEAN NOT NULL DEFAULT FALSE,
  non_answer_pattern TEXT,             -- which pattern matched, for tuning

  -- Light-weight debugging context
  user_question_sha TEXT,              -- hash of the user text (not text itself)
  latency_ms INTEGER
);

CREATE INDEX IF NOT EXISTS ix_model_response_audit_created
  ON model_response_audit (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_model_response_audit_non_answer
  ON model_response_audit (created_at DESC)
  WHERE response_contains_non_answer = TRUE;

CREATE INDEX IF NOT EXISTS ix_model_response_audit_path
  ON model_response_audit (path, created_at DESC);

-- Partial index for identity-divergence detection: rows where the
-- prompt hash was computed but the identity prefix was NOT present
-- (something built a prompt without going through the assembler).
CREATE INDEX IF NOT EXISTS ix_model_response_audit_missing_identity
  ON model_response_audit (created_at DESC)
  WHERE identity_prefix_present = FALSE;
