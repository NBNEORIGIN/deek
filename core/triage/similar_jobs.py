"""Triage Phase D — Similar past jobs surfacing.

Given an enquiry summary (the short Qwen-generated distillation of an
inbound email), query the CRM's pgvector + BM25 hybrid search for
the top-N most similar PAST projects. The digest renders them below
the candidate block so Toby has price + spec + outcome context when
drafting the reply.

This is deliberately a DIFFERENT query shape from project_matcher:

  * project_matcher asks "which existing project is this email about?"
    — sender + subject + client-name-guess → narrow match
  * find_similar_jobs asks "what past work is SHAPED like this enquiry?"
    — enquiry summary (full spec text) → broad semantic match

Shadow-mode gated via DEEK_SIMILARITY_SHADOW. When shadow is on:
  * the query still runs
  * results log to cairn_intel.triage_similarity_debug for review
  * the digest block is NOT rendered

When shadow is off (post-cutover):
  * same query + log
  * the block renders in the digest

Cutover cron flips the env var per the Impressions/Crosslink pattern.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


log = logging.getLogger(__name__)


CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
CRM_SEARCH_PATH = '/api/cairn/search'
CRM_REQUEST_TIMEOUT = 10.0

# Post-rerank minimum. CRM hybrid returns scores ~0.015 for a weak
# match and ~0.05+ for a good one. The Phase D threshold is deliberately
# a bit higher than project_matcher's 0.015 because the cost of a
# bad "similar job" suggestion is confusion — we'd rather show fewer
# jobs than mis-recommend.
DEFAULT_MIN_SCORE = 0.02
DEFAULT_LIMIT = 3

# Reranker weights
_SAME_CLIENT_BOOST = 0.10
_WON_STATUS_BOOST = 0.03          # small thumb on the scale for won jobs
_LOST_STATUS_PENALTY = 0.0        # per user: include lost jobs, no penalty
_HAS_FOLDER_BOOST = 0.05          # Phase C signal — "a real tracked job"

# Stage values in the CRM that count as "won" vs "lost" for ranking.
# Stages we don't recognise are treated neutrally.
_WON_STAGES = frozenset({'WON', 'INVOICED', 'COMPLETED', 'DELIVERED'})
_LOST_STAGES = frozenset({'LOST', 'CANCELLED', 'DECLINED', 'DEAD'})


@dataclass
class SimilarJob:
    project_id: str
    project_name: str
    client_name: str | None
    quoted_amount: float | None
    quoted_currency: str
    status: str | None            # 'won' | 'lost' | 'in_progress' | 'quoted' | raw stage
    summary: str                  # 1-2 sentence distillation
    score: float                  # post-rerank score
    raw_score: float              # pre-rerank score from CRM
    has_local_folder: bool

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _classify_status(stage: str | None) -> str | None:
    if not stage:
        return None
    up = stage.upper()
    if up in _WON_STAGES:
        return 'won'
    if up in _LOST_STAGES:
        return 'lost'
    if up == 'QUOTED':
        return 'quoted'
    if up in ('LEAD', 'CONTACTED', 'NEGOTIATING', 'IN_PROGRESS'):
        return 'in_progress'
    return stage.lower()


def _summarise_content(content: str, max_chars: int = 180) -> str:
    """Pull a 1-2 sentence distillation from the CRM content field.

    The content is already a project description + append-only updates.
    The first non-header sentence before the first "--- Update" block
    is usually the brief. If not present, first N chars of the whole.
    """
    if not content:
        return ''
    text = content.strip()
    # Trim off update sections
    update_marker = text.find('--- Update')
    if update_marker > 0:
        text = text[:update_marker].strip()
    # Drop field prefixes ("Client: ...", "Status: ...") that sit on
    # their own sentence-like clauses and aren't useful in the digest
    sentences: list[str] = []
    for sent in text.replace('\r', '').split('. '):
        s = sent.strip()
        if not s:
            continue
        if s.startswith(('Client:', 'Status:', 'Value:', 'Stage:')):
            continue
        sentences.append(s)
        if sum(len(x) for x in sentences) > max_chars:
            break
    out = '. '.join(sentences).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + '…'
    return out


def _crm_search(
    query: str, limit: int, base_url: str, token: str,
) -> list[dict]:
    params = {
        'q': query[:500],
        'types': 'project',
        'limit': limit,
    }
    with httpx.Client(timeout=CRM_REQUEST_TIMEOUT) as client:
        r = client.get(
            f'{base_url}{CRM_SEARCH_PATH}',
            params=params,
            headers={'Authorization': f'Bearer {token}'},
        )
    if r.status_code != 200:
        log.warning(
            'similar_jobs: CRM search HTTP %d — %s',
            r.status_code, r.text[:200],
        )
        return []
    try:
        data = r.json()
    except Exception:
        return []
    return data.get('results') or []


def find_similar_jobs(
    enquiry_summary: str,
    *,
    client_id: str | None = None,
    client_name: str | None = None,
    exclude_project_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
    min_score: float = DEFAULT_MIN_SCORE,
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[SimilarJob]:
    """Return the top-N past projects most similar to this enquiry.

    ``client_id`` / ``client_name`` (either — we match on whatever the
    CRM exposes in metadata) biases the reranker toward same-client
    jobs but does NOT exclude cross-client matches.  Cross-client
    similarity is often more useful (the Morpeth coffee shop matters
    even if it's a different client).

    ``exclude_project_id`` drops the project we already matched so we
    don't recommend a job to itself.

    Never raises. On any error returns [].
    """
    enquiry_summary = (enquiry_summary or '').strip()
    if not enquiry_summary:
        return []
    base = (
        base_url or os.getenv('CRM_BASE_URL') or CRM_DEFAULT_BASE_URL
    ).rstrip('/')
    token = api_key or (
        os.getenv('DEEK_API_KEY')
        or os.getenv('CAIRN_API_KEY')
        or os.getenv('CLAW_API_KEY', '')
    ).strip()
    if not token:
        return []

    # Fetch extra so we have headroom after dedupe + rerank.
    fetch_limit = max(limit * 3, 6)
    try:
        results = _crm_search(enquiry_summary, fetch_limit, base, token)
    except Exception as exc:
        log.warning('similar_jobs: CRM search failed: %s', exc)
        return []

    client_needle = (client_name or '').strip().lower()
    jobs: list[SimilarJob] = []
    for r in results:
        pid = r.get('source_id') or ''
        if not pid:
            continue
        if exclude_project_id and pid == exclude_project_id:
            continue
        md = r.get('metadata') or {}
        raw_score = float(r.get('score') or 0.0)
        stage = md.get('stage')
        status = _classify_status(stage)
        client_on_row = (md.get('client') or '').strip()
        same_client = bool(
            client_needle
            and client_on_row
            and client_needle in client_on_row.lower()
        )
        # `has_local_folder` isn't in the CRM search response today.
        # Phase C ships the column but we don't have it surfaced on
        # search metadata — leaving a hook here; flip to True when
        # metadata.has_local_folder becomes available. For now it's
        # a passive reranker signal with zero effect.
        has_folder = bool(md.get('has_local_folder'))

        # Rerank
        score = raw_score
        if same_client:
            score += _SAME_CLIENT_BOOST
        if status == 'won':
            score += _WON_STATUS_BOOST
        elif status == 'lost':
            score -= _LOST_STATUS_PENALTY
        if has_folder:
            score += _HAS_FOLDER_BOOST

        if score < min_score:
            continue

        content = r.get('content') or ''
        summary = _summarise_content(content)

        value = md.get('value')
        try:
            quoted_amount: float | None = (
                float(value) if value is not None else None
            )
        except (TypeError, ValueError):
            quoted_amount = None

        jobs.append(SimilarJob(
            project_id=pid,
            project_name=(md.get('project_name') or r.get('title') or '')[:200],
            client_name=client_on_row or None,
            quoted_amount=quoted_amount,
            quoted_currency='GBP',
            status=status,
            summary=summary,
            score=round(score, 4),
            raw_score=round(raw_score, 4),
            has_local_folder=has_folder,
        ))

    # Sort by reranked score, won-before-lost at equal score.
    def _sort_key(j: SimilarJob):
        status_rank = 0
        if j.status == 'won':
            status_rank = -1
        elif j.status == 'lost':
            status_rank = 1
        return (-j.score, status_rank)

    jobs.sort(key=_sort_key)
    return jobs[:limit]


# ── Shadow-mode gating + debug logging ──────────────────────────────

def is_similarity_shadow() -> bool:
    """True when DEEK_SIMILARITY_SHADOW is a truthy string.

    Default: shadow-on. The digest block does NOT render until the
    cutover cron flips the env var (scheduled 2026-05-05 — mirrors the
    Impressions and Crosslink cutover pattern).
    """
    raw = (os.getenv('DEEK_SIMILARITY_SHADOW') or 'true').strip().lower()
    return raw in {'true', '1', 'yes', 'on'}


def log_similarity_debug(
    conn,
    triage_id: int,
    enquiry_summary: str,
    jobs: list[SimilarJob],
    latency_ms: int,
) -> int | None:
    """Insert a debug row so Toby can audit similarity quality during
    shadow mode. Never raises."""
    try:
        payload = [j.to_json() for j in jobs]
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cairn_intel.triage_similarity_debug
                    (triage_id, enquiry_summary, candidates,
                     latency_ms, created_at)
                   VALUES (%s, %s, %s::jsonb, %s, NOW())
                   RETURNING id""",
                (int(triage_id), enquiry_summary[:2000],
                 json.dumps(payload), int(latency_ms)),
            )
            (new_id,) = cur.fetchone()
            conn.commit()
            return int(new_id)
    except Exception as exc:
        log.warning('similar_jobs: debug log failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def find_and_log(
    conn,
    *,
    triage_id: int,
    enquiry_summary: str,
    client_name: str | None = None,
    exclude_project_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[SimilarJob]:
    """Convenience: run find_similar_jobs, write the debug row, return
    the jobs. Callers that just want the jobs for rendering call
    find_similar_jobs directly."""
    t0 = time.monotonic()
    jobs = find_similar_jobs(
        enquiry_summary,
        client_name=client_name,
        exclude_project_id=exclude_project_id,
        limit=limit,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    log_similarity_debug(conn, triage_id, enquiry_summary, jobs, latency_ms)
    return jobs


__all__ = [
    'SimilarJob',
    'find_similar_jobs',
    'find_and_log',
    'is_similarity_shadow',
    'log_similarity_debug',
    'DEFAULT_LIMIT',
    'DEFAULT_MIN_SCORE',
]
