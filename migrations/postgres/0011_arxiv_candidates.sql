-- 0011 — arXiv research-loop candidates.
--
-- Stage 1 of the arXiv research loop (brief idea: poll papers on
-- Deek-relevant topics, have local Qwen score applicability, then
-- surface the best via the daily memory brief for Toby to
-- accept/reject/defer).
--
-- Shape: one row per arxiv paper we've considered. Dedup on
-- arxiv_id (the stable identifier the arxiv API returns).
--
-- Lifecycle:
--   1. poll_arxiv.py inserts rows with applicability_score +
--      applicability_reason set by local Qwen.
--   2. The memory brief's question builder picks the top
--      un-surfaced candidate (score >= threshold), sets
--      surfaced_at, adds a research_prompt question.
--   3. Reply parser captures Toby's YES/NO/LATER into
--      toby_verdict / toby_verdict_at.
--   4. (Follow-up) On YES, a drafter fetches the PDF, writes a
--      brief file, sets brief_drafted_at + brief_path.
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS cairn_intel.arxiv_candidates (
  id                   BIGSERIAL PRIMARY KEY,
  arxiv_id             TEXT NOT NULL UNIQUE,
  title                TEXT NOT NULL,
  abstract             TEXT NOT NULL,
  authors              TEXT[] NOT NULL DEFAULT '{}',
  published_at         DATE NOT NULL,
  pdf_url              TEXT NOT NULL,
  query                TEXT NOT NULL,
  applicability_score  REAL,
  applicability_reason TEXT,
  toby_verdict         TEXT,
  toby_verdict_at      TIMESTAMPTZ,
  surfaced_at          TIMESTAMPTZ,
  brief_drafted_at     TIMESTAMPTZ,
  brief_path           TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS arxiv_candidates_unsurfaced_idx
  ON cairn_intel.arxiv_candidates (applicability_score DESC)
  WHERE surfaced_at IS NULL AND applicability_score IS NOT NULL;

CREATE INDEX IF NOT EXISTS arxiv_candidates_created_idx
  ON cairn_intel.arxiv_candidates (created_at DESC);

CREATE INDEX IF NOT EXISTS arxiv_candidates_verdict_idx
  ON cairn_intel.arxiv_candidates (toby_verdict)
  WHERE toby_verdict IS NOT NULL;
