"""
Wiki Generation API routes.

Endpoints:
    POST /wiki/generate/direct-notes   — process all wiki_candidate emails now
    POST /wiki/generate/clusters       — run full seed-topic cluster generation
    GET  /wiki/generate/status         — generation log summary
"""
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/wiki/generate', tags=['wiki-generation'])

_direct_notes_running = False
_cluster_running = False


# ---------------------------------------------------------------------------
# Direct notes
# ---------------------------------------------------------------------------

def _run_direct_notes():
    global _direct_notes_running
    try:
        from core.wiki_gen.processor import process_wiki_candidates
        result = process_wiki_candidates()
        logger.info('Direct notes job complete: %s', result)
        # Trigger wiki recompile so new articles are searchable immediately
        try:
            import httpx
            httpx.post('http://localhost:8765/api/wiki/compile', timeout=10)
        except Exception:
            pass
    except Exception as exc:
        logger.error('Direct notes job failed: %s', exc, exc_info=True)
    finally:
        _direct_notes_running = False


@router.post('/direct-notes')
async def generate_direct_notes(background_tasks: BackgroundTasks):
    """
    Generate wiki articles from all unprocessed wiki_candidate emails.
    Returns immediately; runs in background.
    """
    global _direct_notes_running
    if _direct_notes_running:
        raise HTTPException(409, 'Direct notes job already running')
    _direct_notes_running = True
    background_tasks.add_task(_run_direct_notes)
    return {'started': True, 'message': 'Direct notes wiki generation started.'}


# ---------------------------------------------------------------------------
# Cluster generation
# ---------------------------------------------------------------------------

def _run_cluster_generation(topics: list[str] | None, force: bool = False):
    global _cluster_running
    try:
        from core.wiki_gen.cluster import run_cluster_generation
        result = run_cluster_generation(topics=topics, force=force)
        logger.info('Cluster generation complete: %s', result)
        try:
            import httpx
            httpx.post('http://localhost:8765/api/wiki/compile', timeout=10)
        except Exception:
            pass
    except Exception as exc:
        logger.error('Cluster generation failed: %s', exc, exc_info=True)
    finally:
        _cluster_running = False


@router.post('/clusters')
async def generate_clusters(
    background_tasks: BackgroundTasks,
    topics: list[str] | None = None,
    force: bool = False,
):
    """
    Run wiki article generation for all seed topics (or a supplied subset).
    One article per topic, quality-gated. Long-running — returns immediately.

    force=true: re-run topics that already have a passing article (for refresh).
    Default: skip already-completed topics (idempotent scheduled task behaviour).
    """
    global _cluster_running
    if _cluster_running:
        raise HTTPException(409, 'Cluster generation already running')
    _cluster_running = True
    background_tasks.add_task(_run_cluster_generation, topics, force)
    return {
        'started': True,
        'topics': len(topics) if topics else 35,
        'force': force,
        'message': 'Cluster wiki generation started. Poll /wiki/generate/status for progress.',
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get('/status')
async def generation_status():
    """Return summary of wiki generation log."""
    from core.wiki_gen.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    source_type,
                    COUNT(*)                                      AS total,
                    COUNT(*) FILTER (WHERE quality_passed = TRUE) AS passed,
                    COUNT(*) FILTER (WHERE quality_passed = FALSE) AS failed,
                    SUM(tokens_used)                              AS tokens,
                    MAX(created_at)                               AS last_run
                FROM cairn_wiki_generation_log
                GROUP BY source_type
                """
            )
            rows = cur.fetchall()

            cur.execute(
                "SELECT COUNT(*) FROM claw_code_chunks "
                "WHERE project_id='claw' AND chunk_type='wiki'"
            )
            wiki_chunks = cur.fetchone()[0]

    breakdown = {
        row[0]: {
            'total': row[1], 'passed': row[2], 'failed': row[3],
            'tokens': row[4] or 0,
            'last_run': row[5].isoformat() if row[5] else None,
        }
        for row in rows
    }

    return {
        'breakdown': breakdown,
        'wiki_chunks_in_vector_store': wiki_chunks,
        'direct_notes_job_running': _direct_notes_running,
        'cluster_job_running': _cluster_running,
    }
