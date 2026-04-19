"""Graph walk at retrieval time — Brief 3 Phase B.

When a query arrives, extract its entities using the same extractor
that runs at write time, walk 1–2 hops through the entity graph
ranked by `edge_weight × (1 + outcome_signal)`, and collect the
memories linked to visited entities. Those memories are fused into
retrieval as a fourth signal alongside BM25, pgvector episodic, and
pgvector schemas.

Shadow mode: gated by `DEEK_CROSSLINK_SHADOW` (default true). While
shadow, the walk runs and logs a diff of (old top-5) vs (old +
graph-surfaced top-5) to `data/graph_shadow.jsonl`. User-facing
retrieval is unchanged until the flag flips.

See docs/CROSSLINK_GRAPH.md for the mechanism and tuning notes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.memory.entities import extract_entities, load_taxonomy

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = Path(
    os.getenv('DEEK_RETRIEVAL_CONFIG',
              str(_REPO_ROOT / 'config' / 'retrieval.yaml'))
)
_SHADOW_LOG = Path(os.getenv(
    'DEEK_CROSSLINK_SHADOW_LOG',
    str(_REPO_ROOT / 'data' / 'graph_shadow.jsonl'),
))


# ── Defaults ───────────────────────────────────────────────────────────

_DEFAULT_MAX_HOPS = 2
_DEFAULT_2HOP_EDGE_THRESHOLD = 2.0   # only follow 2-hop on weight >= this
_DEFAULT_GRAPH_WEIGHT = 0.15         # contribution vs relevance in rerank
_DEFAULT_MAX_MEMORIES = 10           # cap on graph-surfaced candidates


@dataclass
class GraphCandidate:
    """One memory surfaced by the graph walk.

    graph_score is pre-normalised — the caller must min-max it against
    the other candidates it fuses with. path_entities is included for
    debugging / shadow logging.
    """
    chunk_id: int
    graph_score: float
    path_entities: list[str]   # canonical names visited to reach this memory


def shadow_enabled() -> bool:
    """Default true. Explicit DEEK_CROSSLINK_SHADOW=false flips live."""
    val = (os.getenv('DEEK_CROSSLINK_SHADOW') or 'true').strip().lower()
    return val in ('1', 'true', 'yes', 'on')


def _load_config() -> dict:
    cfg = {
        'graph_max_hops': _DEFAULT_MAX_HOPS,
        'graph_2hop_edge_threshold': _DEFAULT_2HOP_EDGE_THRESHOLD,
        'graph_weight': _DEFAULT_GRAPH_WEIGHT,
        'graph_max_memories': _DEFAULT_MAX_MEMORIES,
    }
    if not _CONFIG_PATH.exists():
        return cfg
    try:
        import yaml
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8')) or {}
        for k in cfg:
            if isinstance(data.get(k), (int, float)):
                cfg[k] = float(data[k]) if isinstance(cfg[k], float) else int(data[k])
    except Exception as exc:
        logger.debug('[graph_walk] config load failed: %s', exc)
    return cfg


# ── Query-side entity extraction ──────────────────────────────────────

def extract_query_entities(query: str) -> list[tuple[str, str]]:
    """Return (type, canonical_name) pairs from a query.

    Same extractor as write-time so the vocabulary matches. Stop
    entities are already filtered by extract_entities.
    """
    if not query:
        return []
    try:
        refs = extract_entities(query, taxonomy=load_taxonomy())
    except Exception as exc:
        logger.debug('[graph_walk] query extract failed: %s', exc)
        return []
    return [(r.type, r.canonical_name) for r in refs]


# ── Graph walk (DB-side) ──────────────────────────────────────────────

def _resolve_entity_ids(conn, pairs: list[tuple[str, str]]) -> list[str]:
    """Turn (type, canonical_name) pairs into entity_nodes UUIDs."""
    if not pairs:
        return []
    try:
        with conn.cursor() as cur:
            # One query with VALUES join — avoids N round-trips.
            cur.execute(
                """
                SELECT n.id::text
                  FROM entity_nodes n
                  JOIN unnest(%s::text[], %s::text[]) AS q(t, c)
                    ON n.type = q.t AND n.canonical_name = q.c
                """,
                ([p[0] for p in pairs], [p[1] for p in pairs]),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as exc:
        logger.debug('[graph_walk] id resolution failed: %s', exc)
        return []


def _neighbours(
    conn, seed_ids: list[str], edge_threshold: float = 0.0,
) -> list[tuple[str, str, float, float]]:
    """Return (source_id, target_id, weight, outcome_signal) for edges
    where at least one endpoint is in seed_ids and weight >= threshold.
    """
    if not seed_ids:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_id::text, target_id::text, weight, outcome_signal
                  FROM entity_edges
                 WHERE (source_id = ANY(%s::uuid[])
                     OR target_id = ANY(%s::uuid[]))
                   AND weight >= %s
                """,
                (seed_ids, seed_ids, edge_threshold),
            )
            return [
                (r[0], r[1], float(r[2] or 1.0), float(r[3] or 0.0))
                for r in cur.fetchall()
            ]
    except Exception as exc:
        logger.debug('[graph_walk] neighbours query failed: %s', exc)
        return []


def _walk(
    conn,
    seed_ids: list[str],
    max_hops: int,
    two_hop_threshold: float,
) -> dict[str, float]:
    """Breadth-first walk. Returns {entity_id: cumulative_score}.

    Score for visiting entity v via path through edges e1, e2, ... is:
        score(v) = Σ (edge_weight * (1 + outcome_signal))
                  over every path that reaches v.

    Constant-floor-positive adjustment (1 + signal) keeps negative
    outcome edges contributing less, but never producing negative
    scores that would mess up min-max normalisation downstream.
    """
    scores: dict[str, float] = {eid: 0.0 for eid in seed_ids}
    if not seed_ids or max_hops < 1:
        return scores
    # Hop 1: all edges with a seed endpoint
    hop1 = _neighbours(conn, seed_ids, edge_threshold=0.0)
    reached: set[str] = set(seed_ids)
    for src, tgt, w, sig in hop1:
        other = tgt if src in reached else src
        score = w * (1.0 + max(-0.9, sig))  # floor so signal doesn't flip sign
        scores[other] = scores.get(other, 0.0) + score
        reached.add(other)

    # Hop 2: edges from the new layer, only if their weight is high enough
    if max_hops >= 2:
        hop1_layer = [e for e in reached if e not in set(seed_ids)]
        hop2 = _neighbours(conn, hop1_layer, edge_threshold=two_hop_threshold)
        for src, tgt, w, sig in hop2:
            for endpoint in (src, tgt):
                if endpoint in reached:
                    continue
                score = w * (1.0 + max(-0.9, sig)) * 0.5  # 2-hop decay
                scores[endpoint] = scores.get(endpoint, 0.0) + score
                reached.add(endpoint)
    return scores


def _memories_for_entities(
    conn,
    entity_scores: dict[str, float],
) -> dict[int, tuple[float, list[str]]]:
    """Return {memory_chunk_id: (graph_score, [entity_canonical_names])}.

    graph_score = Σ (entity_score × co_occurrence_multiplier)  over
    every entity in the memory's entity set that appears in the walk.
    """
    if not entity_scores:
        return {}
    ids = list(entity_scores.keys())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT me.memory_id, me.entity_id::text,
                       n.canonical_name, n.mention_count
                  FROM memory_entities me
                  JOIN entity_nodes n ON n.id = me.entity_id
                 WHERE me.entity_id = ANY(%s::uuid[])
                """,
                (ids,),
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.debug('[graph_walk] memory link query failed: %s', exc)
        return {}

    out: dict[int, tuple[float, list[str]]] = {}
    for memory_id, entity_id, cname, mention_count in rows:
        entity_score = float(entity_scores.get(entity_id, 0.0))
        # Down-weight ubiquitous entities — a mention in one-of-many
        # memories is more informative than a mention in all of them.
        inv_freq = 1.0 / max(1, int(mention_count or 1))
        contribution = entity_score * inv_freq
        if memory_id in out:
            prev_score, prev_path = out[memory_id]
            new_path = list(prev_path)
            if cname not in new_path:
                new_path.append(cname)
            out[memory_id] = (prev_score + contribution, new_path)
        else:
            out[memory_id] = (contribution, [cname])
    return out


# ── Public entry point ───────────────────────────────────────────────

def walk_for_query(query: str) -> list[GraphCandidate]:
    """Top-level entry. Returns [] if DB unreachable, no entities, or
    no graph connections found. Never raises.
    """
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return []
    pairs = extract_query_entities(query)
    if not pairs:
        return []
    cfg = _load_config()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=3)
    except Exception as exc:
        logger.debug('[graph_walk] db connect failed: %s', exc)
        return []
    try:
        seed_ids = _resolve_entity_ids(conn, pairs)
        if not seed_ids:
            return []
        entity_scores = _walk(
            conn, seed_ids,
            max_hops=int(cfg['graph_max_hops']),
            two_hop_threshold=float(cfg['graph_2hop_edge_threshold']),
        )
        memory_scores = _memories_for_entities(conn, entity_scores)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not memory_scores:
        return []
    # Sort by graph_score desc, cap.
    ranked = sorted(
        memory_scores.items(), key=lambda kv: kv[1][0], reverse=True,
    )[:int(cfg['graph_max_memories'])]
    return [
        GraphCandidate(chunk_id=int(cid), graph_score=score, path_entities=path)
        for cid, (score, path) in ranked
    ]


# ── Shadow logging ────────────────────────────────────────────────────

_shadow_lock = threading.Lock()


def log_shadow(query: str, old_top: list[dict], graph_candidates: list[GraphCandidate]) -> None:
    """Append a single JSONL record capturing what the graph would
    have surfaced vs what the retriever returned. Never raises.
    """
    try:
        _SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        old_ids: list = []
        for c in old_top[:5]:
            cid = c.get('chunk_id')
            if cid is None:
                cid = c.get('dedupe_key') or c.get('file')
            old_ids.append(cid)
        record = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'query': query[:200],
            'old_top5': old_ids,
            'graph_top': [
                {
                    'chunk_id': c.chunk_id,
                    'graph_score': round(c.graph_score, 4),
                    'path': c.path_entities,
                }
                for c in graph_candidates[:10]
            ],
        }
        with _shadow_lock, _SHADOW_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, default=str) + '\n')
    except Exception as exc:
        logger.debug('[graph_walk] shadow log failed (non-fatal): %s', exc)


# ── Live-mode fusion ──────────────────────────────────────────────────

def fuse_into(
    existing: list[dict],
    candidates: list[GraphCandidate],
    weight: float | None = None,
) -> list[dict]:
    """Boost existing dicts whose chunk_id is in graph candidates,
    and append any graph-surfaced memories not already present.

    Uses the `impressions_score` field set by the impressions rerank
    (if present) as the base, so live mode composes cleanly with
    Brief 2 Phase A. If not present, falls back to `score`.
    """
    if not candidates:
        return existing
    cfg_weight = _load_config()['graph_weight'] if weight is None else weight
    # Min-max the graph scores so the weight is meaningful.
    # Note the asymmetry with the impressions rerank (which returns 0.0
    # for constant inputs): here "all candidates equally graph-linked"
    # should still get the full boost because they're all signal,
    # unlike salience where constant 1.0 is the default and carries no
    # information.
    scores = [c.graph_score for c in candidates]
    hi = max(scores) if scores else 0.0
    lo = min(scores) if scores else 0.0
    def norm(s: float) -> float:
        if hi - lo < 1e-12:
            return 1.0 if hi > 0 else 0.0
        return (s - lo) / (hi - lo)
    cand_map = {c.chunk_id: (norm(c.graph_score), c) for c in candidates}

    # Boost existing results
    present_ids: set = set()
    for d in existing:
        cid = d.get('chunk_id')
        if cid is None:
            continue
        present_ids.add(cid)
        if cid in cand_map:
            norm_score, cand = cand_map[cid]
            base = d.get('impressions_score', d.get('score', 0.0))
            d['score'] = float(base) + cfg_weight * norm_score
            d['graph_boost'] = cfg_weight * norm_score
            d['graph_path'] = cand.path_entities

    # Append graph-surfaced memories not already in the list.
    appended: list[dict] = []
    for c in candidates:
        if c.chunk_id in present_ids:
            continue
        appended.append({
            'chunk_id': c.chunk_id,
            'file': f'memory/graph-surfaced/{c.chunk_id}',
            'content': '',  # caller can rehydrate if needed
            'chunk_type': 'memory',
            'score': cfg_weight * norm(c.graph_score),
            'graph_boost': cfg_weight * norm(c.graph_score),
            'graph_path': c.path_entities,
            'match_quality': 'graph',
        })

    return existing + appended


__all__ = [
    'GraphCandidate', 'walk_for_query', 'extract_query_entities',
    'fuse_into', 'log_shadow', 'shadow_enabled',
]
