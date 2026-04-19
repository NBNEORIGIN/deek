"""Salience extraction for memory write-back.

Heuristic + regex + embedding-novelty scoring. Budget: <50ms median per
write, no cloud LLM calls. Only fired on memory-bearing chunk types
(memory, email, wiki, module_snapshot, social_post) — code chunks keep
the default salience=1.0 so retrieval ranking cannot downgrade code.

Signals (each returns 0..1):
  - money:            numeric amounts in £/$/€, log-scaled
  - customer_pushback: conflict / correction language via keyword list
  - outcome_weight:   explicit outcome field (fail/deferred/partial/win)
                       — failures are high-salience by design
  - novelty:          1 − max(cosine) against recent memories
  - toby_flag:        hard flag in metadata for Toby-starred items

Final score: weighted sum, clipped to [0.0, 10.0].

Weights configurable in config/salience.yaml.

See briefs/DEEK_BRIEF_2_IMPRESSIONS_LAYER.md Task 2 and
docs/IMPRESSIONS.md for calibration rationale.
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = Path(
    os.getenv('DEEK_SALIENCE_CONFIG',
              str(_REPO_ROOT / 'config' / 'salience.yaml'))
)

# Memory-bearing chunk types — the extractor is a no-op outside this set.
# Code chunks keep salience=1.0, preserving existing retrieval behaviour
# for the bulk of claw_code_chunks.
MEMORY_CHUNK_TYPES: frozenset[str] = frozenset({
    'memory', 'email', 'wiki', 'module_snapshot', 'social_post',
})


# ── Defaults (used if config/salience.yaml is absent or malformed) ─────
_DEFAULT_WEIGHTS: dict[str, float] = {
    'money': 2.5,
    'customer_pushback': 2.0,
    'outcome_weight': 3.0,
    'novelty': 1.5,
    'toby_flag': 5.0,  # a star from Toby is a strong prior
}
_DEFAULT_BASE = 1.0       # all memories start with some non-zero salience
_DEFAULT_MAX = 10.0
_DEFAULT_MIN = 0.0


@dataclass(frozen=True)
class SalienceResult:
    score: float                 # 0.0..10.0, clipped
    signals: dict[str, float] = field(default_factory=dict)  # raw 0..1 per signal


# ── Signal extractors ──────────────────────────────────────────────────

# Currency amounts: £1,234 / £1.2k / $500 / €3,000.00 / 5,000 GBP etc.
_MONEY_RE = re.compile(
    r'(?<![A-Za-z])'                                      # not inside a word
    r'(?:[£$€]\s*)?'                                      # optional symbol
    r'(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)'             # digits
    r'\s*(k|m|bn|GBP|USD|EUR|\$|£|€)?'                    # optional scale/ISO
    r'(?![A-Za-z])',
    re.IGNORECASE,
)
_SCALE = {'k': 1e3, 'm': 1e6, 'bn': 1e9}


def _parse_amount(num_text: str, suffix: str | None) -> float:
    """Best-effort numeric parse. Returns 0 on failure."""
    try:
        raw = float(num_text.replace(',', '').replace(' ', ''))
    except ValueError:
        return 0.0
    if suffix:
        mult = _SCALE.get(suffix.lower(), 1.0)
        return raw * mult
    return raw


def score_money(text: str) -> float:
    """Return 0..1 based on the largest numeric amount detected.

    Log-scaled so £50 and £50,000 both register without drowning out
    other signals. No currency symbol required — bare numbers count at
    half weight to avoid false positives from timestamps and IDs.
    """
    if not text:
        return 0.0
    max_val = 0.0
    has_symbol = False
    for m in _MONEY_RE.finditer(text):
        num, suffix = m.group(1), m.group(2)
        val = _parse_amount(num, suffix)
        if m.group(0).strip().startswith(('£', '$', '€')) or (suffix and suffix.upper() in {'GBP', 'USD', 'EUR'}):
            has_symbol = True
        if val > max_val:
            max_val = val
    if max_val <= 0:
        return 0.0
    # log10(100) = 2, log10(100_000) = 5. Normalise to 0..1 over £100..£1M.
    score = min(1.0, max(0.0, (math.log10(max(max_val, 1)) - 2.0) / 4.0))
    if not has_symbol:
        score *= 0.5  # bare numbers count half — timestamps / IDs are noise
    return score


_PUSHBACK_KEYWORDS: tuple[str, ...] = (
    # Direct pushback
    'not happy', 'unhappy', 'frustrated', 'disappointed',
    'complaint', 'complained', 'unacceptable', 'refund',
    'dispute', 'disputed', 'chargeback',
    # Correction language
    "that's wrong", 'that is wrong', 'incorrect', 'actually,',
    'correction:', 'to clarify', "you're mistaken", 'misunderstood',
    # Escalation
    'escalate', 'manager', 'supervisor', 'legal',
    # NBNE-specific friction patterns
    'out of spec', 'rework', 'redo', 'not what i asked for',
)


def score_customer_pushback(text: str) -> float:
    """Keyword-based pushback detection. Each hit adds 0.2, capped at 1.0."""
    if not text:
        return 0.0
    lower = text.lower()
    hits = sum(1 for kw in _PUSHBACK_KEYWORDS if kw in lower)
    return min(1.0, hits * 0.2)


# Outcome → weight. Failures and deferrals are high-salience by design;
# a successful, unremarkable write is the baseline.
_OUTCOME_WEIGHTS: dict[str, float] = {
    'fail': 1.0,
    'failed': 1.0,
    'blocked': 0.9,
    'deferred': 0.7,
    'partial': 0.5,
    'rollback': 0.9,
    'win': 0.3,
    'success': 0.2,
    'committed': 0.2,
}


def score_outcome(metadata: dict) -> float:
    """Pull from metadata['outcome']. Unknown outcomes score 0."""
    outcome = str(metadata.get('outcome', '') or '').strip().lower()
    if not outcome:
        return 0.0
    return _OUTCOME_WEIGHTS.get(outcome, 0.0)


def score_novelty(
    text: str,
    embedding_fn: Callable[[str], list[float]] | None,
    recent_embeddings: Iterable[list[float]] | None,
) -> float:
    """1 − max cosine similarity vs recent embeddings.

    Low similarity = high novelty. If no embedding fn or no history,
    return 0 (neutral — do not reward or punish).
    """
    if not text or embedding_fn is None or recent_embeddings is None:
        return 0.0
    # Empty iterable = no basis for novelty judgement (not "fully novel").
    # Materialise to a list first so we can check length without consuming.
    recent_list = list(recent_embeddings)
    if not recent_list:
        return 0.0
    try:
        target = embedding_fn(text)
    except Exception as exc:
        logger.debug('[salience] novelty: embedding_fn failed: %s', exc)
        return 0.0
    if not target:
        return 0.0
    max_cos = 0.0
    for vec in recent_list:
        cos = _cosine(target, vec)
        if cos > max_cos:
            max_cos = cos
    return max(0.0, 1.0 - max_cos)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def score_toby_flag(metadata: dict) -> float:
    """Hard flag — Toby has starred this entry. Binary 0/1."""
    flag = metadata.get('toby_flag') or metadata.get('starred') or False
    return 1.0 if bool(flag) else 0.0


# ── Config loading ─────────────────────────────────────────────────────

def _load_weights() -> tuple[dict[str, float], float, float, float]:
    """Load (weights, base, min, max) from config/salience.yaml.

    Failure is safe — returns the defaults. Config file missing is the
    common case in dev; not an error.
    """
    weights = dict(_DEFAULT_WEIGHTS)
    base = _DEFAULT_BASE
    min_score = _DEFAULT_MIN
    max_score = _DEFAULT_MAX
    if not _CONFIG_PATH.exists():
        return weights, base, min_score, max_score
    try:
        import yaml
        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8')) or {}
        if isinstance(cfg.get('weights'), dict):
            for k, v in cfg['weights'].items():
                if k in weights and isinstance(v, (int, float)):
                    weights[k] = float(v)
        for field_name, var in (
            ('base_score', 'base'), ('min_score', 'min_score'),
            ('max_score', 'max_score'),
        ):
            if isinstance(cfg.get(field_name), (int, float)):
                if field_name == 'base_score':
                    base = float(cfg[field_name])
                elif field_name == 'min_score':
                    min_score = float(cfg[field_name])
                else:
                    max_score = float(cfg[field_name])
    except Exception as exc:
        logger.warning('[salience] config load failed, using defaults: %s', exc)
    return weights, base, min_score, max_score


# ── Public surface ─────────────────────────────────────────────────────

def extract_salience(
    memory_text: str,
    metadata: dict | None = None,
    embedding_fn: Callable[[str], list[float]] | None = None,
    recent_embeddings: Iterable[list[float]] | None = None,
) -> SalienceResult:
    """Return a SalienceResult for a memory write.

    Args:
        memory_text: concatenation of query + decision + rejected + outcome
                     (or the raw text for non-decision memory types).
        metadata: optional dict — should include 'outcome' for decisions,
                  and 'toby_flag' / 'starred' where applicable.
        embedding_fn: optional callable that returns a float vector for
                      a given text. Used by the novelty scorer. When
                      absent, novelty is 0.0 (neutral, not penalised).
        recent_embeddings: optional iterable of recent memory embeddings
                      for novelty comparison. Typically the last 100.

    Returns:
        SalienceResult with a clipped score and the raw 0..1 signals
        dict for auditing.
    """
    metadata = metadata or {}
    # Cast to native Python float at the boundary — recent embeddings
    # arrive from pgvector as numpy float32 arrays and would otherwise
    # propagate np.float32 into the signals dict, which breaks
    # json.dumps downstream.
    signals = {
        'money': float(score_money(memory_text)),
        'customer_pushback': float(score_customer_pushback(memory_text)),
        'outcome_weight': float(score_outcome(metadata)),
        'novelty': float(score_novelty(memory_text, embedding_fn, recent_embeddings)),
        'toby_flag': float(score_toby_flag(metadata)),
    }
    weights, base, min_score, max_score = _load_weights()
    weighted_sum = sum(signals[k] * weights.get(k, 0.0) for k in signals)
    score = float(base + weighted_sum)
    score = max(float(min_score), min(float(max_score), score))
    return SalienceResult(score=score, signals=signals)


__all__ = [
    'MEMORY_CHUNK_TYPES',
    'SalienceResult',
    'extract_salience',
    'score_money',
    'score_customer_pushback',
    'score_outcome',
    'score_novelty',
    'score_toby_flag',
]
