"""
Cairn Email Ingestion API routes.

Endpoints:
    POST /email/embed              — start background embedding job
    GET  /email/embed/status       — embedding progress
    GET  /email/cairn/context      — full email store stats (Cairn context endpoint)
    POST /email/process-inbox      — check cairn@ for new messages, ingest + embed
"""
import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException

from core.email_ingest.db import get_conn, ensure_schema
from core.email_ingest.embedder import embed_email_batch, get_embed_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/email', tags=['email'])

# Simple in-process flag to avoid overlapping embed jobs
_embed_job_running = False


# ---------------------------------------------------------------------------
# Background embed job
# ---------------------------------------------------------------------------

def _run_embed_job(batch_size: int) -> None:
    global _embed_job_running
    try:
        logger.info('Background embed job starting (batch_size=%d)', batch_size)
        # Run until no pending emails remain
        total_embedded = 0
        while True:
            result = embed_email_batch(batch_size=batch_size)
            total_embedded += result['embedded']
            if result['embedded'] == 0 or result['errors'] > result['embedded']:
                break
        logger.info('Background embed job complete — total embedded: %d', total_embedded)
    except Exception as exc:
        logger.error('Background embed job failed: %s', exc, exc_info=True)
    finally:
        _embed_job_running = False


@router.post('/embed')
async def start_embed_job(
    background_tasks: BackgroundTasks,
    batch_size: int = 50,
):
    """
    Start a background job to embed all pending emails into claw_code_chunks.
    Runs until the pending queue is empty. Returns immediately.
    Only one job can run at a time — returns 409 if already running.
    """
    global _embed_job_running
    if _embed_job_running:
        raise HTTPException(status_code=409, detail='Embed job already running')

    _embed_job_running = True
    background_tasks.add_task(_run_embed_job, batch_size)

    status = get_embed_status()
    return {
        'started': True,
        'batch_size': batch_size,
        'pending_at_start': status['pending'],
        'message': 'Embedding job started in background. Poll /email/embed/status for progress.',
    }


@router.get('/embed/status')
async def embed_status():
    """
    Return current embedding progress.

    Fields:
        embedded     — emails with is_embedded=TRUE
        pending      — emails with is_embedded=FALSE and no skip_reason
        skipped      — emails filtered by PII/relevance rules
        total        — all emails in cairn_email_raw
        vector_chunks — rows in claw_code_chunks with chunk_type='email'
        job_running  — whether a background embed job is active
    """
    status = get_embed_status()
    status['job_running'] = _embed_job_running
    return status


# ---------------------------------------------------------------------------
# Cairn context endpoint
# ---------------------------------------------------------------------------

@router.get('/cairn/context')
async def email_cairn_context():
    """
    Cairn context endpoint for the email store.
    Returns aggregate stats, mailbox breakdown, wiki candidates, and recent direct notes.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Per-mailbox breakdown
            cur.execute(
                """
                SELECT
                    mailbox,
                    COUNT(*)                                                AS total,
                    COUNT(*) FILTER (WHERE is_embedded = TRUE)             AS embedded,
                    MAX(received_at)                                       AS last_received
                FROM cairn_email_raw
                GROUP BY mailbox
                """
            )
            mailbox_rows = cur.fetchall()

            # Overall totals
            cur.execute(
                """
                SELECT
                    COUNT(*)                                                            AS total,
                    COUNT(*) FILTER (WHERE is_embedded = TRUE AND skip_reason IS NULL) AS total_embedded,
                    COUNT(*) FILTER (WHERE is_embedded = FALSE AND skip_reason IS NULL) AS pending,
                    MAX(created_at)                                                     AS last_ingest
                FROM cairn_email_raw
                """
            )
            total, total_embedded, pending, last_ingest = cur.fetchone()

            # Vector chunk count
            cur.execute(
                "SELECT COUNT(*) FROM claw_code_chunks WHERE project_id='claw' AND chunk_type='email'"
            )
            vector_chunks = cur.fetchone()[0]

            # Wiki candidates (direct notes not yet turned into wiki articles)
            cur.execute(
                """
                SELECT COUNT(*) FROM cairn_email_raw
                WHERE 'wiki_candidate' = ANY(labels)
                  AND NOT ('wiki_generated' = ANY(COALESCE(labels, '{}'::text[])))
                """
            )
            wiki_candidates = cur.fetchone()[0]

            # Recent direct notes (last 10)
            cur.execute(
                """
                SELECT subject, received_at
                FROM cairn_email_raw
                WHERE 'direct_note' = ANY(labels)
                ORDER BY received_at DESC
                LIMIT 10
                """
            )
            recent_notes = [
                {'subject': row[0], 'received_at': row[1].isoformat() if row[1] else None}
                for row in cur.fetchall()
            ]

    mailbox_breakdown = {}
    for mailbox, total_mb, embedded_mb, last_received in mailbox_rows:
        mailbox_breakdown[mailbox] = {
            'total': total_mb,
            'embedded': embedded_mb,
            'last_received': last_received.isoformat() if last_received else None,
        }

    return {
        'total_emails_stored': total,
        'total_embedded': total_embedded,
        'embedding_pending': pending,
        'vector_chunks': vector_chunks,
        'last_ingest': last_ingest.isoformat() if last_ingest else None,
        'mailbox_breakdown': mailbox_breakdown,
        'wiki_candidates': wiki_candidates,
        'recent_direct_notes': recent_notes,
    }


# ---------------------------------------------------------------------------
# Ongoing cairn@ inbox processor
# ---------------------------------------------------------------------------

_process_inbox_running = False


def _run_process_inbox() -> None:
    global _process_inbox_running
    try:
        from core.email_ingest.processor import process_cairn_inbox
        process_cairn_inbox(embed_immediately=True)
    except Exception as exc:
        logger.error('process_cairn_inbox failed: %s', exc, exc_info=True)
    finally:
        _process_inbox_running = False


@router.post('/process-inbox')
async def process_inbox(background_tasks: BackgroundTasks):
    """
    Check cairn@ for new unprocessed emails, ingest and embed them.
    Run by Windows Scheduled Task every 15 minutes.
    Returns 409 if already running.
    """
    global _process_inbox_running
    if _process_inbox_running:
        raise HTTPException(status_code=409, detail='Inbox processing already running')

    _process_inbox_running = True
    background_tasks.add_task(_run_process_inbox)

    return {
        'started': True,
        'message': 'cairn@ inbox processing started in background.',
    }
