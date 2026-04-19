"""Dream-state HTTP surface — Brief 4 Phase B.

Two kinds of endpoints:

1. GET /api/deek/briefing/morning — list the top surfaced
   candidates from the most recent nocturnal run that haven't been
   reviewed yet. Consumed by the PWA Brief tab.
2. POST /api/deek/briefing/candidate/{id}/review — record a review
   action (accept/reject/edit/defer). On accept, promote the
   candidate to the `schemas` table so it becomes retrievable.

All endpoints sit under the existing `/api/deek/` prefix so nginx
already proxies them. Auth matches the rest of the module —
`verify_api_key` on Hetzner, session cookie via the Next.js proxy.

See docs/DREAM_STATE.md for the overall mechanism.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


def _db_url() -> str:
    u = os.getenv('DATABASE_URL', '')
    if not u:
        raise HTTPException(status_code=503, detail='database not configured')
    return u


def _connect():
    import psycopg2
    return psycopg2.connect(_db_url(), connect_timeout=5)


# ── GET morning briefing ──────────────────────────────────────────────

def _fetch_source_summaries(conn, memory_ids: list[int]) -> dict[int, str]:
    """Return {memory_id: first ~200 chars of chunk_content} for the
    given IDs. Empty for missing memories — the caller decides how
    to surface them.
    """
    if not memory_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, chunk_content FROM claw_code_chunks "
            "WHERE id = ANY(%s::int[])",
            (memory_ids,),
        )
        out: dict[int, str] = {}
        for mid, content in cur.fetchall():
            text = str(content or '').strip()
            if len(text) > 220:
                text = text[:217].rstrip() + '…'
            out[int(mid)] = text
    return out


@router.get('/briefing/morning')
@router.get('/api/deek/briefing/morning')
async def morning_briefing(
    limit: int = 5,
    _: bool = Depends(verify_api_key),
):
    """Return unreviewed, surfaced candidates sorted by score desc.

    Shows only candidates from the most recent nocturnal run (the
    latest `generated_at` date). Cards older than today with no
    action are handled by the Phase C auto-archive sweep.
    """
    try:
        conn = _connect()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'db: {exc}')
    try:
        with conn.cursor() as cur:
            # Find the most recent run's UTC date
            cur.execute(
                "SELECT MAX(generated_at)::date FROM dream_candidates"
            )
            (latest_date,) = cur.fetchone()
            if latest_date is None:
                return {'date': None, 'candidates': []}

            cur.execute(
                """SELECT id::text, candidate_text, candidate_type,
                          confidence, score, source_memory_ids,
                          generated_at
                     FROM dream_candidates
                    WHERE reviewed_at IS NULL
                      AND surfaced_at IS NOT NULL
                      AND generated_at::date = %s
                    ORDER BY score DESC NULLS LAST, generated_at DESC
                    LIMIT %s""",
                (latest_date, limit),
            )
            rows = cur.fetchall()

        if not rows:
            return {'date': latest_date.isoformat(), 'candidates': []}

        all_source_ids: list[int] = []
        for r in rows:
            all_source_ids.extend(int(i) for i in (r[5] or []))
        summaries = _fetch_source_summaries(conn, list(set(all_source_ids)))

        candidates = []
        for r in rows:
            cid, text, ctype, conf, score, source_ids, generated_at = r
            src_ids = list(source_ids or [])
            candidates.append({
                'id': cid,
                'text': text,
                'type': ctype,
                'confidence': float(conf or 0.0),
                'score': float(score or 0.0),
                'source_memory_ids': src_ids,
                'source_summaries': [
                    {'memory_id': mid, 'text': summaries.get(mid, '')}
                    for mid in src_ids
                ],
                'generated_at': generated_at.isoformat()
                if generated_at else None,
                'actions': ['accept', 'reject', 'edit', 'defer'],
            })
        return {
            'date': latest_date.isoformat(),
            'candidates': candidates,
        }
    finally:
        conn.close()


# ── POST review ───────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    action: str                   # accept | reject | edit | defer
    notes: Optional[str] = None
    edited_text: Optional[str] = None


_VALID_ACTIONS = {'accept', 'reject', 'edit', 'defer'}


def _embed_text(text: str) -> list[float] | None:
    """Embed for schemas promotion. None on failure — caller handles."""
    try:
        from core.wiki.embeddings import get_embed_fn
        fn = get_embed_fn()
        if fn is None:
            return None
        v = fn(text[:6000])
        return [float(x) for x in v] if v else None
    except Exception as exc:
        logger.debug('[dream review] embed failed: %s', exc)
        return None


def _promote_to_schema(
    conn, candidate_row: tuple, final_text: str,
) -> str | None:
    """Insert a new schemas row, return its id. None on any failure."""
    candidate_id, _text, ctype, conf, source_mem_ids, source_ent_ids, model = candidate_row
    embedding = _embed_text(final_text)
    if embedding is None:
        return None
    schema_id = str(uuid.uuid4())
    # Schemas.source_memory_ids is INTEGER[] in Brief 2's schema.
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO schemas
                    (id, schema_text, embedding, salience,
                     source_memory_ids, derived_at, last_accessed_at,
                     access_count, status, model, confidence)
                   VALUES (%s, %s, %s::vector, %s, %s::int[],
                           NOW(), NOW(), 0, 'active', %s, %s)
                   RETURNING id::text""",
                (
                    schema_id,
                    final_text,
                    embedding,
                    1.0 + float(conf or 0.0) * 2.0,  # seed salience 1..3
                    list(int(i) for i in (source_mem_ids or [])),
                    model or 'dream:unknown',
                    float(conf or 0.0),
                ),
            )
            (new_id,) = cur.fetchone()
        return new_id
    except Exception as exc:
        logger.warning('[dream review] promote failed: %s', exc)
        return None


@router.post('/briefing/candidate/{candidate_id}/review')
@router.post('/api/deek/briefing/candidate/{candidate_id}/review')
async def review_candidate(
    candidate_id: str,
    body: ReviewRequest,
    _: bool = Depends(verify_api_key),
):
    """Record a review action + (on accept/edit) promote to schemas.

    Actions:
      - accept: reviewed_at=NOW(), review_action='accepted',
        schema row created, promoted_schema_id set.
      - edit:   same as accept but with body.edited_text as the
        schema_text.
      - reject: reviewed_at=NOW(), review_action='rejected'. No
        promotion; the rejected text lives in dream_candidates for
        future duplication-gate training.
      - defer:  clear surfaced_at so the next morning picks it up
        again. review_action='deferred' tagged but reviewed_at stays
        NULL (it'll be re-reviewed). Idempotent across multiple
        defers.
    """
    action = (body.action or '').strip().lower()
    if action not in _VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f'action must be one of {sorted(_VALID_ACTIONS)}')
    if action == 'edit' and not (body.edited_text and body.edited_text.strip()):
        raise HTTPException(
            status_code=400, detail='edit action requires edited_text',
        )

    try:
        conn = _connect()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'db: {exc}')
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id::text, candidate_text, candidate_type,
                          confidence, source_memory_ids, source_entity_ids,
                          generation_model, review_action
                     FROM dream_candidates
                    WHERE id = %s::uuid""",
                (candidate_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail='candidate not found')

        cid_str, text, ctype, conf, src_mem, src_ent, model, prev_action = row
        promoted_id: str | None = None

        if action == 'defer':
            # Re-queue for tomorrow: clear surfaced_at, record the defer.
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE dream_candidates
                          SET surfaced_at = NULL,
                              review_action = 'deferred',
                              review_notes = COALESCE(%s, review_notes)
                        WHERE id = %s::uuid""",
                    (body.notes, candidate_id),
                )
            conn.commit()
            return {
                'id': candidate_id, 'action': 'deferred',
                'deferred_to_next_briefing': True,
            }

        # accept / edit / reject → finalise reviewed_at
        final_text = text
        if action == 'edit':
            final_text = body.edited_text.strip()

        if action in ('accept', 'edit'):
            promoted_id = _promote_to_schema(
                conn,
                candidate_row=(cid_str, text, ctype, conf, src_mem, src_ent, model),
                final_text=final_text,
            )

        with conn.cursor() as cur:
            cur.execute(
                """UPDATE dream_candidates
                      SET reviewed_at = NOW(),
                          review_action = %s,
                          review_notes = %s,
                          candidate_text = %s,
                          promoted_schema_id = %s::uuid
                    WHERE id = %s::uuid""",
                (
                    'accepted' if action == 'edit' else action if action == 'reject' else 'accepted',
                    body.notes,
                    final_text if action == 'edit' else text,
                    promoted_id,
                    candidate_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        'id': candidate_id,
        'action': 'accepted' if action in ('accept', 'edit') else action,
        'promoted_schema_id': promoted_id,
        'edited': action == 'edit',
    }


__all__ = ['router']
