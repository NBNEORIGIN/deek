"""Dream candidate filter + scoring — Brief 4 Phase A Tasks 3 + 4.

The value of dream state is in the filter, not the generator. Four
hard gates run in order; candidates failing any are dropped with a
signal breakdown stored in `filter_signals` for diagnostics.

Gates:
    1. Grounding — >= 3 source memories cited, and the cited memories
       must actually mention key terms from the candidate text (no
       LLM — fast text check).
    2. Specificity — candidate text must not substring-match any
       entry in config/dream/anti_pattern_list.yaml.
    3. Actionability — candidate must reference at least one of:
       entity, channel, price/currency, decision keyword, timeframe.
    4. Duplication — candidate text cosine < 0.85 against existing
       schemas (Brief 2) and recent rejected dream candidates.

Survivors are scored:
    score = 0.4 * confidence
          + 0.2 * min(1.0, source_memory_count / 10)
          + 0.2 * entity_type_diversity
          + 0.2 * actionability_score

Top-K by score go to morning surfacing; others persist in
`dream_candidates` but aren't surfaced.

The filter is DB-dependent only for the duplication gate; the other
gates work on in-memory data so they can be unit-tested without
infrastructure.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANTI_PATTERN_PATH = Path(
    os.getenv('DEEK_DREAM_ANTI_PATTERNS',
              str(_REPO_ROOT / 'config' / 'dream' / 'anti_pattern_list.yaml'))
)

MIN_SOURCE_MEMORIES = 3
DUPLICATION_COSINE_THRESHOLD = 0.85
MAX_SURVIVORS_SCORED = 20


# ── Anti-pattern list loader ──────────────────────────────────────────

_anti_pattern_cache: list[str] | None = None


def _load_anti_patterns() -> list[str]:
    global _anti_pattern_cache
    if _anti_pattern_cache is not None:
        return _anti_pattern_cache
    patterns: list[str] = []
    if _ANTI_PATTERN_PATH.exists():
        try:
            import yaml
            data = yaml.safe_load(_ANTI_PATTERN_PATH.read_text(encoding='utf-8')) or {}
            raw = data.get('anti_patterns') or []
            patterns = [str(p).lower().strip() for p in raw if p]
        except Exception as exc:
            logger.warning('[dream] anti-pattern load failed: %s', exc)
    _anti_pattern_cache = patterns
    return patterns


def _reload_anti_patterns() -> None:
    """Force reload from disk. Used by tests."""
    global _anti_pattern_cache
    _anti_pattern_cache = None


# ── Gate 1: Grounding ─────────────────────────────────────────────────

# Stop words excluded from the key-term extraction — they'd match
# anything and defeat the grounding check.
_STOP_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
    'has', 'have', 'had', 'do', 'does', 'did', 'of', 'in', 'on',
    'at', 'to', 'for', 'with', 'by', 'from', 'that', 'this',
    'these', 'those', 'it', 'its', 'and', 'or', 'but', 'not',
    'as', 'can', 'will', 'would', 'should', 'could', 'may',
    'might', 'they', 'them', 'their', 'our', 'we', 'you',
    'nbne', 'deek',  # stop-entity names leaking through
})


def _key_terms(text: str, min_len: int = 4) -> list[str]:
    """Pull content words >= min_len chars, lowercased, deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in re.findall(r'[A-Za-z0-9]+', text.lower()):
        if len(tok) < min_len or tok in _STOP_WORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def grounding_check(
    candidate_text: str,
    cited_memory_ids: list[int],
    id_to_content: dict[int, str],
    min_term_coverage: float = 0.3,
) -> tuple[bool, dict]:
    """Return (passed, signals_dict).

    Passes if:
      - >= MIN_SOURCE_MEMORIES cited
      - All cited IDs have text in id_to_content
      - At least min_term_coverage of the candidate's key terms appear
        somewhere in the cited memory text
    """
    signals = {'gate': 'grounding'}
    if len(cited_memory_ids) < MIN_SOURCE_MEMORIES:
        signals['reason'] = f'only {len(cited_memory_ids)} sources'
        return False, signals

    missing = [i for i in cited_memory_ids if i not in id_to_content]
    if missing:
        signals['reason'] = f'cited unknown memory ids: {missing[:3]}'
        return False, signals

    terms = _key_terms(candidate_text)
    if not terms:
        signals['reason'] = 'candidate has no key terms'
        return False, signals

    combined = ' '.join(id_to_content[i] for i in cited_memory_ids).lower()
    hits = sum(1 for t in terms if t in combined)
    coverage = hits / len(terms)
    signals['term_coverage'] = round(coverage, 3)
    signals['terms_checked'] = len(terms)
    if coverage < min_term_coverage:
        signals['reason'] = (
            f'coverage {coverage:.2f} < {min_term_coverage:.2f}'
        )
        return False, signals
    return True, signals


# ── Gate 2: Specificity ───────────────────────────────────────────────

def specificity_check(candidate_text: str) -> tuple[bool, dict]:
    """Reject if the text contains any anti-pattern phrase."""
    signals = {'gate': 'specificity'}
    text = candidate_text.lower()
    for pat in _load_anti_patterns():
        if pat in text:
            signals['reason'] = f'matched anti-pattern: {pat!r}'
            signals['matched'] = pat
            return False, signals
    return True, signals


# ── Gate 3: Actionability ─────────────────────────────────────────────

# Cheap rule-based check. Expanded iteratively as the filter learns
# what "actionable" means in NBNE's context.
_ACTIONABILITY_CUES = (
    # Currency / price
    r'[£$€]\s*\d', r'\b\d+\s*(?:gbp|usd|eur|k|m)\b',
    # Timeframes
    r'\b\d+\s*(?:day|week|month|year|hour)', r'\bwithin \d',
    r'\btomorrow\b', r'\btoday\b', r'\bnext week\b', r'\bbefore\b',
    # Decision / action verbs
    r'\bprioritise\b', r'\bescalate\b', r'\bdefer\b', r'\bapprove\b',
    r'\breject\b', r'\border\b', r'\bquote\b', r'\bship\b',
    r'\binstall\b', r'\breplace\b', r'\breview\b', r'\bnegotiate\b',
    # Channel references
    r'\bemail\b', r'\bphone\b', r'\bamazon\b', r'\betsy\b',
    r'\bebay\b', r'\bgoogle ads\b', r'\bcrm\b',
    # Specific entities surfacing
    r'\bm\d{4,5}\b',  # M-number
)


def actionability_check(candidate_text: str) -> tuple[bool, dict]:
    """Reject if no actionability cue found."""
    signals: dict = {'gate': 'actionability'}
    text = candidate_text.lower()
    matched: list[str] = []
    for pat in _ACTIONABILITY_CUES:
        if re.search(pat, text):
            matched.append(pat)
    signals['matched_count'] = len(matched)
    if not matched:
        signals['reason'] = 'no actionability cue'
        return False, signals
    return True, signals


# ── Gate 4: Duplication (DB-dependent) ────────────────────────────────

def duplication_check(
    candidate_text: str,
    embedding_fn,  # Callable[[str], list[float]] | None
    existing_embeddings: list[list[float]],
    threshold: float = DUPLICATION_COSINE_THRESHOLD,
) -> tuple[bool, dict]:
    """Reject if cosine >= threshold against any existing embedding.

    `existing_embeddings` is already fetched by the caller (schemas +
    recent rejected dream candidates) — keeps this function DB-free
    for unit testing.
    """
    signals: dict = {'gate': 'duplication'}
    if not existing_embeddings or embedding_fn is None:
        signals['note'] = 'no existing embeddings to compare'
        return True, signals
    try:
        v = embedding_fn(candidate_text[:2000])
    except Exception as exc:
        signals['note'] = f'embed failed: {exc}'
        return True, signals
    if not v:
        signals['note'] = 'empty embedding'
        return True, signals
    import math as _math
    def cos(a, b):
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = _math.sqrt(sum(x * x for x in a))
        nb = _math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    max_sim = 0.0
    for existing in existing_embeddings:
        s = cos(v, existing)
        if s > max_sim:
            max_sim = s
    signals['max_similarity'] = round(max_sim, 3)
    if max_sim >= threshold:
        signals['reason'] = f'duplicate (sim={max_sim:.3f})'
        return False, signals
    return True, signals


# ── Scoring ───────────────────────────────────────────────────────────

def _entity_type_diversity(
    source_entity_ids: list[str],
    entity_id_to_type: dict[str, str],
) -> float:
    types = set()
    for eid in source_entity_ids:
        t = entity_id_to_type.get(eid)
        if t:
            types.add(t)
    if len(types) >= 3:
        return 1.0
    if len(types) == 2:
        return 0.6
    if len(types) == 1:
        return 0.3
    return 0.0


def compute_score(
    confidence: float,
    source_memory_count: int,
    entity_diversity: float,
    actionability_ok: bool,
) -> float:
    return (
        0.4 * confidence
        + 0.2 * min(1.0, source_memory_count / 10.0)
        + 0.2 * entity_diversity
        + 0.2 * (1.0 if actionability_ok else 0.0)
    )


# ── Orchestrator ──────────────────────────────────────────────────────

def _fetch_existing_embeddings(conn, window_days: int = 30) -> list[list[float]]:
    """Pull active schema embeddings + embeddings of recently-rejected
    dream candidates (for dedupe).
    """
    embeddings: list[list[float]] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT embedding FROM schemas "
                "WHERE status = 'active' AND embedding IS NOT NULL"
            )
            for (vec,) in cur.fetchall():
                try:
                    if vec is not None:
                        embeddings.append([float(x) for x in list(vec)])
                except Exception:
                    continue
    except Exception as exc:
        logger.debug('[dream] existing embeddings fetch failed: %s', exc)
    return embeddings


def _memory_contents_map(
    conn, memory_ids: list[int],
) -> dict[int, str]:
    if not memory_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, chunk_content FROM claw_code_chunks "
            "WHERE id = ANY(%s::int[])",
            (memory_ids,),
        )
        return {int(r[0]): str(r[1] or '') for r in cur.fetchall()}


def _entity_types_for_memories(
    conn, memory_ids: list[int],
) -> dict[str, str]:
    """Return {entity_id: type} for all entities linked to these memories."""
    if not memory_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT e.id::text, e.type
                 FROM entity_nodes e
                 JOIN memory_entities me ON me.entity_id = e.id
                WHERE me.memory_id = ANY(%s::int[])""",
            (memory_ids,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def filter_and_score(
    raw_candidates: list,  # list[RawCandidate] — avoiding circular import
    max_surface: int = 5,
) -> list[dict]:
    """Run all four gates, score survivors, return top-K as dicts ready
    for write_candidates(). Non-DB, non-LLM-except-embedding-fn.
    """
    if not raw_candidates:
        return []
    # DB needed for dup-check embeddings, memory content, entity types.
    try:
        import psycopg2
        conn = psycopg2.connect(os.getenv('DATABASE_URL', ''), connect_timeout=5)
    except Exception as exc:
        logger.warning('[dream] filter: db connect failed: %s', exc)
        return []
    try:
        # Pre-fetch dedupe basis and per-memory content/types.
        existing_embs = _fetch_existing_embeddings(conn)
        all_cited: set[int] = set()
        for rc in raw_candidates:
            for i in rc.source_memory_ids:
                all_cited.add(int(i))
        id_to_content = _memory_contents_map(conn, list(all_cited))
        entity_id_to_type = _entity_types_for_memories(conn, list(all_cited))

        # Embedding fn — reuse the wiki one.
        try:
            from core.wiki.embeddings import get_embed_fn
            embed_fn = get_embed_fn()
        except Exception:
            embed_fn = None

        survivors: list[dict] = []
        for rc in raw_candidates:
            signals: dict = {'gates': {}}

            ok, g1 = grounding_check(
                rc.candidate_text or '', rc.source_memory_ids, id_to_content,
            )
            signals['gates']['grounding'] = g1
            if not ok:
                continue

            ok, g2 = specificity_check(rc.candidate_text or '')
            signals['gates']['specificity'] = g2
            if not ok:
                continue

            ok, g3 = actionability_check(rc.candidate_text or '')
            signals['gates']['actionability'] = g3
            if not ok:
                continue

            ok, g4 = duplication_check(
                rc.candidate_text or '',
                lambda t: embed_fn(t) if embed_fn else [],
                existing_embs,
            )
            signals['gates']['duplication'] = g4
            if not ok:
                continue

            # Entity ids for this candidate's sources
            src_entity_ids: list[str] = []
            seen_entity: set[str] = set()
            for mid in rc.source_memory_ids:
                for eid, _typ in _load_mem_entities(conn, mid):
                    if eid not in seen_entity:
                        seen_entity.add(eid)
                        src_entity_ids.append(eid)

            diversity = _entity_type_diversity(src_entity_ids, entity_id_to_type)
            score = compute_score(
                confidence=rc.confidence,
                source_memory_count=len(rc.source_memory_ids),
                entity_diversity=diversity,
                actionability_ok=True,
            )

            survivors.append({
                'candidate_text': rc.candidate_text,
                'candidate_type': rc.candidate_type,
                'source_memory_ids': list(rc.source_memory_ids),
                'source_entity_ids': src_entity_ids,
                'confidence': rc.confidence,
                'filter_signals': signals,
                'score': score,
            })
    finally:
        conn.close()

    # Rank; keep only top-K for surfacing (others still persisted).
    survivors.sort(key=lambda c: c['score'], reverse=True)
    return survivors[:max_surface] if max_surface > 0 else survivors


def _load_mem_entities(conn, memory_id: int) -> list[tuple[str, str]]:
    """(entity_id, type) for one memory. Small per-row call — fine for
    small candidate batches."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT e.id::text, e.type
                 FROM entity_nodes e
                 JOIN memory_entities me ON me.entity_id = e.id
                WHERE me.memory_id = %s""",
            (memory_id,),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


__all__ = [
    'grounding_check', 'specificity_check', 'actionability_check',
    'duplication_check', 'compute_score', 'filter_and_score',
]
