-- 0017_voice_session_meta_and_projects.sql
--
-- Per-session metadata + per-user projects for the chat-history sidebar.
--
-- Rationale: deek_voice_sessions stores one row per turn keyed by
-- session_id, so anything that's "per session" (custom title, project
-- assignment, archive flag) needs its own table. Putting these on
-- deek_voice_sessions would force every turn to repeat the same value.
--
-- Schema:
--   deek_voice_projects     — per-user named buckets (e.g. "Customers",
--                              "Suppliers", "HR"). Free-form string,
--                              no hierarchy.
--   deek_voice_session_meta — one row per session_id with title /
--                              project_id / archived_at overrides.
--                              Absence of a row = "uses defaults":
--                              title from first message, no project,
--                              not archived.
--
-- Idempotent. Safe to re-run.

CREATE TABLE IF NOT EXISTS deek_voice_projects (
  id            SERIAL PRIMARY KEY,
  user_label    VARCHAR(100) NOT NULL,
  name          VARCHAR(100) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_label, name)
);

CREATE INDEX IF NOT EXISTS idx_deek_voice_projects_user
  ON deek_voice_projects (user_label, name);

CREATE TABLE IF NOT EXISTS deek_voice_session_meta (
  session_id    VARCHAR(100) PRIMARY KEY,
  user_label    VARCHAR(100) NOT NULL,
  title         VARCHAR(200),                       -- override; null = use first message
  project_id    INTEGER REFERENCES deek_voice_projects(id) ON DELETE SET NULL,
  archived_at   TIMESTAMPTZ,                        -- null = active
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deek_voice_session_meta_user
  ON deek_voice_session_meta (user_label, archived_at NULLS FIRST);

CREATE INDEX IF NOT EXISTS idx_deek_voice_session_meta_project
  ON deek_voice_session_meta (project_id) WHERE project_id IS NOT NULL;
