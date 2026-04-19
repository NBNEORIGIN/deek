-- 0003_dream_candidates.sql
--
-- Brief 4 Phase A (Dream State) — nocturnal free-association output.
-- Seeds are memories; candidates are LLM-generated patterns; survivors
-- of the filter pass get surfaced in the morning briefing; Toby
-- accepts/rejects/edits/defers; accepted ones promote to schemas.
--
-- source_memory_ids is INTEGER[] (claw_code_chunks.id is INTEGER).
-- source_entity_ids is UUID[] (entity_nodes.id is UUID).
-- promoted_schema_id is UUID (schemas.id is UUID).
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS dream_candidates (
  id UUID PRIMARY KEY,
  candidate_text TEXT NOT NULL,
  candidate_type TEXT NOT NULL,          -- 'pattern' | 'rule' | 'analogy' | 'prediction'
  source_memory_ids INTEGER[] NOT NULL,
  source_entity_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
  generation_temperature REAL NOT NULL,
  generation_model TEXT NOT NULL,
  confidence REAL NOT NULL,
  filter_signals JSONB NOT NULL DEFAULT '{}'::jsonb,
  score REAL,                             -- final rank score; NULL = rejected pre-scoring
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  surfaced_at TIMESTAMPTZ,
  reviewed_at TIMESTAMPTZ,
  review_action TEXT,                     -- 'accepted' | 'rejected' | 'edited' | 'deferred' | 'expired'
  review_notes TEXT,
  promoted_schema_id UUID REFERENCES schemas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_dream_candidates_unreviewed
  ON dream_candidates (generated_at DESC)
  WHERE reviewed_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_dream_candidates_review_action
  ON dream_candidates (review_action)
  WHERE review_action IS NOT NULL;
