"""
Cairn Social database schema + query helpers.

Tables use the `social_` prefix and live in the same Cairn PostgreSQL DB as
everything else. Same psycopg2 pattern as core/amazon_intel/db.py.
"""
import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras


def get_db_url() -> str:
    return os.getenv(
        'DATABASE_URL',
        'postgresql://postgres:postgres123@localhost:5432/claw',
    )


@contextmanager
def get_conn():
    conn = psycopg2.connect(get_db_url(), connect_timeout=5)
    try:
        yield conn
    finally:
        conn.close()


_SQL_SCHEMA = """
-- Drafting session: one row per "Generate" click. Holds the input mode + brief
-- (or original text in proofread mode) and the request metadata.
CREATE TABLE IF NOT EXISTS social_draft (
    id                  SERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    created_by          VARCHAR(64) NOT NULL DEFAULT 'jo',
    -- 'brief'    = Jo gave a short prompt; tool drafts in her voice
    -- 'proofread' = Jo wrote a finished post; tool refines/adapts per platform
    source_mode         VARCHAR(20) NOT NULL DEFAULT 'brief',
    brief_text          TEXT,
    original_text       TEXT,
    content_pillar      VARCHAR(20),
    platforms_requested JSONB DEFAULT '[]',
    voice_guide_version INTEGER NOT NULL DEFAULT 1,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_social_draft_created
    ON social_draft (created_at DESC);

-- Per-platform variant of a draft. One row per (draft, platform). Refinements
-- create new rows linked back via parent_variant_id.
CREATE TABLE IF NOT EXISTS social_draft_variant (
    id                 SERIAL PRIMARY KEY,
    draft_id           INTEGER NOT NULL REFERENCES social_draft(id) ON DELETE CASCADE,
    platform           VARCHAR(20) NOT NULL,
    content            TEXT NOT NULL,
    generated_at       TIMESTAMPTZ DEFAULT NOW(),
    generation_model   VARCHAR(64),
    revision_count     INTEGER NOT NULL DEFAULT 0,
    parent_variant_id  INTEGER REFERENCES social_draft_variant(id) ON DELETE SET NULL,
    is_published       BOOLEAN NOT NULL DEFAULT FALSE,
    published_at       TIMESTAMPTZ,
    published_url      TEXT,
    cairn_memory_id    VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_social_variant_draft
    ON social_draft_variant (draft_id);
CREATE INDEX IF NOT EXISTS idx_social_variant_published
    ON social_draft_variant (is_published, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_variant_platform
    ON social_draft_variant (platform);
"""


def ensure_schema() -> None:
    """Create social_* tables if they don't exist. Called at Cairn startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_SCHEMA)
            conn.commit()


# ── Draft CRUD ─────────────────────────────────────────────────────────────────

def create_draft(
    *,
    source_mode: str,
    brief_text: Optional[str],
    original_text: Optional[str],
    platforms: list[str],
    content_pillar: Optional[str],
    voice_guide_version: int,
    created_by: str = 'jo',
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO social_draft
                  (created_by, source_mode, brief_text, original_text,
                   content_pillar, platforms_requested, voice_guide_version)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    created_by,
                    source_mode,
                    brief_text,
                    original_text,
                    content_pillar,
                    json.dumps(platforms),
                    voice_guide_version,
                ),
            )
            draft_id = cur.fetchone()[0]
            conn.commit()
            return draft_id


def insert_variant(
    *,
    draft_id: int,
    platform: str,
    content: str,
    generation_model: str,
    parent_variant_id: Optional[int] = None,
    revision_count: int = 0,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO social_draft_variant
                  (draft_id, platform, content, generation_model,
                   parent_variant_id, revision_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (draft_id, platform, content, generation_model,
                 parent_variant_id, revision_count),
            )
            variant_id = cur.fetchone()[0]
            conn.commit()
            return variant_id


def get_draft(draft_id: int) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM social_draft WHERE id = %s", (draft_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_variant(variant_id: int) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM social_draft_variant WHERE id = %s",
                (variant_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def list_variants_for_draft(draft_id: int) -> list[dict]:
    """Latest variant per platform for a given draft (so refinements supersede)."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (platform) *
                FROM social_draft_variant
                WHERE draft_id = %s
                ORDER BY platform, generated_at DESC
                """,
                (draft_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def list_recent_drafts(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM social_draft
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def list_published(
    *,
    platform: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    where = ['is_published = TRUE']
    params: list = []
    if platform:
        where.append('platform = %s')
        params.append(platform)
    params.extend([limit, offset])
    sql = f"""
        SELECT v.*, d.source_mode, d.brief_text, d.original_text,
               d.content_pillar
        FROM social_draft_variant v
        JOIN social_draft d ON d.id = v.draft_id
        WHERE {' AND '.join(where)}
        ORDER BY v.published_at DESC
        LIMIT %s OFFSET %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def mark_variant_published(
    *,
    variant_id: int,
    published_url: Optional[str],
    cairn_memory_id: Optional[str],
) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE social_draft_variant
                SET is_published = TRUE,
                    published_at = NOW(),
                    published_url = COALESCE(%s, published_url),
                    cairn_memory_id = COALESCE(%s, cairn_memory_id)
                WHERE id = %s
                RETURNING *
                """,
                (published_url, cairn_memory_id, variant_id),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None


def recent_published_for_few_shot(limit: int = 5) -> list[dict]:
    """Return up-to-N recently published variants, most recent first.

    Used as few-shot examples in the drafting prompt alongside the seed posts.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT platform, content, published_at
                FROM social_draft_variant
                WHERE is_published = TRUE
                ORDER BY published_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def isoformat_row(row: dict) -> dict:
    """Convert datetime fields to ISO strings for JSON serialisation."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
