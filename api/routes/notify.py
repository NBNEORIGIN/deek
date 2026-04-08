"""
Cairn notify endpoint.

POST /api/cairn/notify — modules signal data changes for async wiki recompilation.

Modules call this after ingesting new data. Cairn queues the affected scope
for wiki recompilation and re-embedding. Fire-and-forget for the caller.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cairn", tags=["Cairn"])

KNOWN_SCOPES = {"products", "clients", "modules", "marketplaces", "blanks"}

KNOWN_MODULES = {
    "amazon_intelligence",
    "etsy_intelligence",
    "manufacture",
    "crm",
    "phloe",
    "ledger",
    "render",
    "meridian",
    "beacon",
    "claw",
}

# Signals the background worker to wake immediately when items are enqueued
_notify_event = asyncio.Event()


def _db_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres123@localhost:5432/claw")


def ensure_notify_schema() -> None:
    """Create wiki_recompile_queue table if it doesn't exist. Called at Cairn startup."""
    try:
        conn = psycopg2.connect(_db_url(), connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS wiki_recompile_queue (
                        id           SERIAL PRIMARY KEY,
                        module       VARCHAR(100) NOT NULL,
                        scope        VARCHAR(50)  NOT NULL,
                        entity       VARCHAR(200),
                        queued_at    TIMESTAMPTZ  DEFAULT NOW(),
                        started_at   TIMESTAMPTZ,
                        completed_at TIMESTAMPTZ,
                        retry_count  INTEGER      DEFAULT 0,
                        status       VARCHAR(20)  DEFAULT 'pending',
                        error        TEXT
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_wrq_status
                        ON wiki_recompile_queue(status)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_wrq_queued_at
                        ON wiki_recompile_queue(queued_at)
                """)
            conn.commit()
        finally:
            conn.close()
        print("[Cairn] wiki_recompile_queue schema ready")
    except Exception as exc:
        print(f"[Cairn] notify schema setup failed: {exc}")


# ─── Request / Response models ────────────────────────────────────────────────

class NotifyRequest(BaseModel):
    module: str
    event_type: Literal["snapshot_completed", "data_ingested", "schema_changed"]
    scope: str
    affected_entities: list[str] = []
    occurred_at: Optional[str] = None


# ─── Endpoint ────────────────────────────────────────────────────────────────

@router.post("/notify")
async def cairn_notify(
    payload: NotifyRequest,
    _: bool = Depends(verify_api_key),
):
    """
    Signal that a module has ingested new data.

    Queues affected entities for wiki recompilation and re-embedding.
    Returns immediately — the actual recompile happens asynchronously.

    curl example:
        curl -X POST http://localhost:8765/api/cairn/notify \\
             -H "Content-Type: application/json" \\
             -d '{"module":"amazon_intelligence","event_type":"snapshot_completed",
                  "scope":"products","affected_entities":["M0001","M0042"],
                  "occurred_at":"2026-04-07T14:30:00Z"}'
    """
    scope = payload.scope
    if scope not in KNOWN_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scope '{scope}'. Valid scopes: {sorted(KNOWN_SCOPES)}",
        )

    module = payload.module
    if module not in KNOWN_MODULES:
        logger.warning(
            "[notify] Unknown module '%s' — accepting for forward compatibility", module
        )

    db_url = _db_url()
    if not db_url:
        raise HTTPException(503, "Database not configured")

    # Build the entity list — at least one row even when no specific entities given
    entities: list[Optional[str]] = payload.affected_entities or [None]
    queued: list[str] = []
    queue_position = 0

    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                # Current pending depth (informational for the caller)
                cur.execute(
                    "SELECT COUNT(*) FROM wiki_recompile_queue WHERE status = 'pending'"
                )
                row = cur.fetchone()
                queue_position = row[0] if row else 0

                for entity in entities:
                    cur.execute(
                        """
                        INSERT INTO wiki_recompile_queue (module, scope, entity, status)
                        VALUES (%s, %s, %s, 'pending')
                        """,
                        (module, scope, entity),
                    )
                    if entity:
                        queued.append(entity)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.error("[notify] DB error enqueueing: %s", exc)
        raise HTTPException(503, f"Failed to queue recompilation: {exc}")

    # Wake the background worker immediately
    _notify_event.set()

    now = datetime.now(timezone.utc)
    estimated_seconds = max(len(queued), 1) * 120 + queue_position * 30
    estimated_completion = (now + timedelta(seconds=estimated_seconds)).isoformat()

    return {
        "received": True,
        "queued_for_recompile": queued,
        "queue_position": queue_position,
        "estimated_completion": estimated_completion,
    }


# ─── Background recompile worker ─────────────────────────────────────────────

async def run_recompile_worker() -> None:
    """
    Background task started in the Cairn lifespan handler.

    Wakes immediately when _notify_event is set, or every 5 minutes as a
    safety net for stuck items. Processes pending wiki_recompile_queue rows
    using WikiCompiler. Retries up to 3 times; marks error after that.
    """
    while True:
        try:
            await asyncio.wait_for(_notify_event.wait(), timeout=300.0)
        except asyncio.TimeoutError:
            pass
        _notify_event.clear()
        await _process_queue()


async def _process_queue() -> None:
    """Process pending items from wiki_recompile_queue."""
    db_url = _db_url()
    if not db_url:
        return

    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
    except Exception as exc:
        logger.error("[recompile-worker] DB connection failed: %s", exc)
        return

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, module, scope, entity
                FROM wiki_recompile_queue
                WHERE status = 'pending' AND retry_count < 3
                ORDER BY queued_at
                LIMIT 50
                """,
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.error("[recompile-worker] Queue query failed: %s", exc)
        conn.close()
        return

    if not rows:
        conn.close()
        return

    logger.info("[recompile-worker] Processing %d queued items", len(rows))

    from core.wiki.compiler import WikiCompiler

    # Group by scope so we call compile() once per scope, not once per entity
    processed_scopes: set[str] = set()

    for row_id, module, scope, entity in rows:
        # Mark as processing
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE wiki_recompile_queue
                       SET status = 'processing', started_at = NOW()
                       WHERE id = %s""",
                    (row_id,),
                )
            conn.commit()
        except Exception as exc:
            logger.warning("[recompile-worker] Could not mark row %s processing: %s", row_id, exc)
            continue

        if scope in processed_scopes:
            # Already compiled this scope in this batch — skip redundant work
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE wiki_recompile_queue
                       SET status = 'completed', completed_at = NOW()
                       WHERE id = %s""",
                    (row_id,),
                )
            conn.commit()
            continue

        try:
            # Scopes the compiler doesn't handle yet are logged and skipped
            supported = {"all", "modules", "products", "clients"}
            if scope not in supported:
                logger.info(
                    "[recompile-worker] Scope '%s' not yet supported by WikiCompiler — skipping", scope
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE wiki_recompile_queue
                           SET status = 'completed', completed_at = NOW(),
                               error = 'scope not yet supported'
                           WHERE id = %s""",
                        (row_id,),
                    )
                conn.commit()
                processed_scopes.add(scope)
                continue

            compiler = WikiCompiler()
            await compiler.compile(scope=scope)
            processed_scopes.add(scope)

            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE wiki_recompile_queue
                       SET status = 'completed', completed_at = NOW()
                       WHERE id = %s""",
                    (row_id,),
                )
            conn.commit()
            logger.info(
                "[recompile-worker] Compiled scope=%s (triggered by %s, entity=%s)",
                scope, module, entity,
            )
        except Exception as exc:
            logger.error(
                "[recompile-worker] Compile failed scope=%s entity=%s: %s", scope, entity, exc
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE wiki_recompile_queue
                           SET retry_count = retry_count + 1,
                               status = CASE WHEN retry_count + 1 >= 3 THEN 'error' ELSE 'pending' END,
                               error = %s
                           WHERE id = %s""",
                        (str(exc)[:500], row_id),
                    )
                conn.commit()
            except Exception as db_exc:
                logger.warning("[recompile-worker] Could not update retry count: %s", db_exc)

    conn.close()
