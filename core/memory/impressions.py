"""Impressions-layer rerank + reinforcement.

Called from core/memory/retriever.py after RRF fusion. Shadow-mode
gated by DEEK_IMPRESSIONS_SHADOW env var (default true): when shadow,
the new ordering is computed and logged but the OLD ordering is
returned to the caller.

Reinforcement: on every retrieval that returns a memory-bearing chunk,
bump access_count and last_accessed_at, and nudge salience up by 0.1
(cap 10.0). Run async — must not block the retrieval response.

See briefs/DEEK_BRIEF_2_IMPRESSIONS_LAYER.md Tasks 3 + 4 and
docs/IMPRESSIONS.md.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.memory.salience import MEMORY_CHUNK_TYPES

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = Path(
    os.getenv('DEEK_RETRIEVAL_CONFIG',
              str(_REPO_ROOT / 'config' / 'retrieval.yaml'))
)

# Defaults — loaded from config/retrieval.yaml at rerank time.
_DEFAULT_ALPHA = 0.5   # relevance weight
_DEFAULT_BETA = 0.25   # salience weight
_DEFAULT_GAMMA = 0.25  # recency weight
_DEFAULT_TAU = 72.0    # recency half-life in hours
_DEFAULT_TOP_K = 20    # how many RRF candidates to rerank


def shadow_enabled() -> bool:
    """Default to shadow mode when the env var is unset or truthy.

    Explicit opt-out via DEEK_IMPRESSIONS_SHADOW=false flips to live
    ordering once Toby has reviewed the shadow diff logs.
    """
    val = (os.getenv('DEEK_IMPRESSIONS_SHADOW') or 'true').strip().lower()
    return val in ('1', 'true', 'yes', 'on')


# ── Config loading (file missing = safe, use defaults) ────────────────

def _load_config() -> dict:
    cfg = {
        'alpha': _DEFAULT_ALPHA,
        'beta': _DEFAULT_BETA,
        'gamma': _DEFAULT_GAMMA,
        'tau_hours': _DEFAULT_TAU,
        'top_k': _DEFAULT_TOP_K,
    }
    if not _CONFIG_PATH.exists():
        return cfg
    try:
        import yaml
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8')) or {}
        for k in cfg:
            if isinstance(data.get(k), (int, float)):
                cfg[k] = float(data[k])
    except Exception as exc:
        logger.warning('[impressions] config load failed, defaults: %s', exc)
    return cfg


# ── Core rerank ───────────────────────────────────────────────────────

@dataclass
class RerankDebug:
    """Per-candidate signal breakdown for diagnostics / shadow logging."""
    chunk_id: int | None
    dedupe_key: str
    chunk_type: str
    rel_n: float
    sal_n: float
    rec_n: float
    final: float


def _min_max(xs: list[float]) -> list[float]:
    """Min-max normalise to 0..1. Constant inputs collapse to 0 (neutral)."""
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-12:
        return [0.0] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]


def _hours_since(ts: datetime | str | None) -> float:
    """Hours since ts (or a huge number if missing)."""
    if ts is None:
        return 1e6
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except Exception:
            return 1e6
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return max(0.0, delta.total_seconds() / 3600.0)


def rerank(
    candidates: list[dict],
    rrf_scores: list[float] | None = None,
    config: dict | None = None,
) -> tuple[list[dict], list[RerankDebug]]:
    """Rerank candidates by alpha*rel + beta*sal + gamma*recency.

    Args:
        candidates: list of dicts from the retriever. Each should carry
                    at least {chunk_id, chunk_type, salience,
                    last_accessed_at, dedupe_key, rrf_score (optional)}.
        rrf_scores: optional parallel list of RRF scores. If absent, read
                    from each candidate's 'rrf_score' key, defaulting to
                    the candidate's existing rank order.
        config: optional override dict — otherwise loaded from
                config/retrieval.yaml.

    Returns:
        (reranked_candidates, per-candidate debug list in same order as
        input). The returned candidates are the input dicts, sorted by
        the new final score, each with 'impressions_score' added.
    """
    if not candidates:
        return [], []
    cfg = config or _load_config()
    alpha, beta, gamma = cfg['alpha'], cfg['beta'], cfg['gamma']
    tau = cfg['tau_hours'] or _DEFAULT_TAU

    n = len(candidates)
    # Relevance: RRF scores if given, else fall back to reverse-rank.
    if rrf_scores is None:
        rrf_scores = [c.get('rrf_score', n - i) for i, c in enumerate(candidates)]

    saliences = [float(c.get('salience', 1.0) or 1.0) for c in candidates]
    recencies = [
        math.exp(-_hours_since(c.get('last_accessed_at')) / tau)
        for c in candidates
    ]

    rel_n = _min_max(rrf_scores)
    sal_n = _min_max(saliences)
    rec_n = _min_max(recencies)

    debug: list[RerankDebug] = []
    scored: list[tuple[float, dict, RerankDebug]] = []
    for i, c in enumerate(candidates):
        final = alpha * rel_n[i] + beta * sal_n[i] + gamma * rec_n[i]
        # Salience-signals boost (migration 0010): read the
        # salience_signals JSONB we now decorate chunks with in
        # _attach_impressions_fields. Previously the JSONB was
        # write-only; this is the reader side of that circuit.
        #
        # toby_flag > 0     → strong prior, user-authored correction
        #                     or high-salience memory. +0.15 nudge.
        # via=triage_reply  → feedback loop output from confirmed
        #                     triage actions. +0.05 nudge.
        # via=memory_brief_ → Toby's own memory-brief answers. +0.05.
        #
        # Boosts are conservative — the impressions rerank is already
        # calibrated; this layer mostly breaks ties in favour of
        # human-touched memories.
        signals = c.get('salience_signals') or {}
        signals_boost = 0.0
        try:
            tf = float(signals.get('toby_flag') or 0.0)
        except (TypeError, ValueError):
            tf = 0.0
        if tf > 0:
            signals_boost += 0.15 * min(tf, 1.0)
        via = str(signals.get('via') or '').lower()
        if via.startswith('triage_reply') or via.startswith('memory_brief'):
            signals_boost += 0.05
        final += signals_boost
        c2 = dict(c)
        c2['impressions_score'] = final
        c2['impressions_debug'] = {
            'rel_n': rel_n[i], 'sal_n': sal_n[i], 'rec_n': rec_n[i],
            'alpha': alpha, 'beta': beta, 'gamma': gamma,
            'signals_boost': round(signals_boost, 4),
        }
        d = RerankDebug(
            chunk_id=c.get('chunk_id'),
            dedupe_key=str(c.get('dedupe_key') or c.get('file_path', '')),
            chunk_type=str(c.get('chunk_type', '')),
            rel_n=rel_n[i], sal_n=sal_n[i], rec_n=rec_n[i], final=final,
        )
        debug.append(d)
        scored.append((final, c2, d))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [t[1] for t in scored], [t[2] for t in scored]


# ── Shadow-mode logging ───────────────────────────────────────────────

_SHADOW_LOG = Path(os.getenv(
    'DEEK_IMPRESSIONS_SHADOW_LOG',
    str(_REPO_ROOT / 'data' / 'impressions_shadow.jsonl'),
))
_shadow_lock = threading.Lock()


def log_shadow_comparison(
    query: str,
    old_order: list[dict],
    new_order: list[dict],
    debug: list[RerankDebug],
    top_n: int = 5,
) -> None:
    """Append a single JSONL record capturing old vs new top-N ordering.

    Safe to call from any thread. Never raises — shadow logging must
    not affect the live retrieval path.
    """
    try:
        _SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'query': query[:200],
            'old_top': [
                {
                    'chunk_id': c.get('chunk_id'),
                    'dedupe_key': c.get('dedupe_key') or c.get('file_path', ''),
                    'chunk_type': c.get('chunk_type'),
                }
                for c in old_order[:top_n]
            ],
            'new_top': [
                {
                    'chunk_id': c.get('chunk_id'),
                    'dedupe_key': c.get('dedupe_key') or c.get('file_path', ''),
                    'chunk_type': c.get('chunk_type'),
                    'score': c.get('impressions_score'),
                }
                for c in new_order[:top_n]
            ],
            'debug': [
                {
                    'chunk_id': d.chunk_id,
                    'chunk_type': d.chunk_type,
                    'rel_n': round(d.rel_n, 4),
                    'sal_n': round(d.sal_n, 4),
                    'rec_n': round(d.rec_n, 4),
                    'final': round(d.final, 4),
                }
                for d in debug[:top_n]
            ],
        }
        with _shadow_lock, _SHADOW_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, default=str) + '\n')
    except Exception as exc:
        logger.debug('[impressions] shadow log failed (non-fatal): %s', exc)


# ── Reinforcement (async write-back on retrieval) ─────────────────────

_REINFORCE_LOCK = threading.Lock()


def reinforce_async(chunk_ids: Iterable[int]) -> None:
    """Fire-and-forget salience bump + last_accessed touch.

    Only reinforces memory-bearing chunks so code chunks don't creep
    upward purely from reads. Runs on a daemon thread — any failure is
    logged and swallowed.
    """
    ids = [int(i) for i in chunk_ids if i is not None]
    if not ids:
        return
    t = threading.Thread(
        target=_reinforce_sync, args=(ids,), name='deek-reinforce',
        daemon=True,
    )
    t.start()


def _reinforce_sync(chunk_ids: list[int]) -> None:
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url or not chunk_ids:
        return
    with _REINFORCE_LOCK:
        try:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    # Only reinforce memory-bearing chunks — code chunks
                    # should not gain salience from retrieval hits.
                    cur.execute(
                        """
                        UPDATE claw_code_chunks
                           SET access_count = access_count + 1,
                               last_accessed_at = NOW(),
                               salience = LEAST(10.0, salience + 0.1)
                         WHERE id = ANY(%s::int[])
                           AND chunk_type = ANY(%s::text[])
                        """,
                        (chunk_ids, list(MEMORY_CHUNK_TYPES)),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug('[impressions] reinforcement failed: %s', exc)


__all__ = [
    'RerankDebug', 'rerank', 'shadow_enabled',
    'log_shadow_comparison', 'reinforce_async',
]
