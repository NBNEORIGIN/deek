"""
Task Classifier — pure rule-based tier selection.

Decides which inference tier a prompt needs based on risk level,
prompt content, and project. No LLM calls. No I/O. Runs in < 1ms.

Tier definitions:
  LOCAL    (1) — qwen2.5-coder:7b — free, fast, simple read/search tasks
  DEEPSEEK (2) — deepseek-chat    — cheap API, standard coding tasks
  CLAUDE   (3) — claude-sonnet   — quality-sensitive, multi-file, debug
  OPUS     (4) — claude-opus     — architecture, security, complex reasoning

OpenAI is NOT a numbered tier. It exists as a 429/529 fallback only —
see core/models/router.py for fallback logic.
"""
from dataclasses import dataclass
from enum import IntEnum


class TaskTier(IntEnum):
    LOCAL    = 1
    DEEPSEEK = 2
    CLAUDE   = 3
    OPUS     = 4

    def label(self) -> str:
        return {1: 'Local', 2: 'DeepSeek', 3: 'Claude', 4: 'Opus'}[self.value]


@dataclass
class ClassificationResult:
    tier: TaskTier
    rule: str           # machine-readable rule name
    confidence: float   # 0.0–1.0
    explanation: str    # human-readable one-liner


# Keywords that always require Opus (architecture / security / complex reasoning)
_OPUS_KEYWORDS: frozenset[str] = frozenset({
    'architecture', 'architect', 'security', 'tenant', 'isolation',
    'trade-off', 'tradeoff', 'migration', 'root cause', 'performance review',
    'design', 'refactor', 'restructure', 'overhaul', 'strategic',
})

# Keywords for simple read/explain/edit tasks — safe to route to local model
_LOCAL_VERBS: frozenset[str] = frozenset({
    # Read-only
    'read', 'search', 'list', 'show', 'explain', 'what is', 'what does',
    'how does', 'describe', 'summarize', 'find', 'look up', 'check',
    # Simple edits (typos, renaming, small additions)
    'fix', 'add', 'remove', 'rename', 'update', 'change', 'edit',
    'delete', 'move', 'copy', 'typo', 'comment', 'format', 'indent',
})

# Debug/error keywords — route to Claude not DeepSeek for better reasoning
_DEBUG_KEYWORDS: frozenset[str] = frozenset({
    'debug', 'broken', 'error', 'traceback', 'exception', 'crash',
    'failing', 'not working', 'wrong', 'fix', 'bug',
})

# Phloe ORM patterns that always need tenant isolation awareness
_PHLOE_ORM_KEYWORDS: frozenset[str] = frozenset({
    'queryset', 'filter', '.objects',
})


def classify(
    prompt: str,
    risk_level: str = 'safe',
    project: str = '',
    context_files: int = 0,
) -> ClassificationResult:
    """
    Classify a prompt into a TaskTier using rule-based logic only.

    Args:
        prompt:        The user message / task description.
        risk_level:    'safe' | 'review' | 'destructive'
        project:       Project name (e.g. 'phloe') for project-specific rules.
        context_files: Number of files currently in context.

    Returns ClassificationResult with tier, rule, confidence, explanation.
    Rules are evaluated in priority order — first match wins.
    """
    prompt_lower = prompt.lower()

    # ── Rule 1: DESTRUCTIVE → always Opus ─────────────────────────────────
    if risk_level == 'destructive':
        return ClassificationResult(
            tier=TaskTier.OPUS,
            rule='destructive_risk',
            confidence=1.0,
            explanation='Destructive risk level — routing to Opus for safety',
        )

    # ── Rule 2: Opus keywords → always Opus ───────────────────────────────
    matched_opus = [kw for kw in _OPUS_KEYWORDS if kw in prompt_lower]
    if matched_opus:
        return ClassificationResult(
            tier=TaskTier.OPUS,
            rule='opus_keywords',
            confidence=1.0,
            explanation=f'Opus keyword(s) detected: {", ".join(matched_opus[:3])}',
        )

    # ── Rule 3: Phloe ORM without tenant → Opus ───────────────────────────
    if project == 'phloe':
        orm_match = [kw for kw in _PHLOE_ORM_KEYWORDS if kw in prompt_lower]
        if orm_match:
            return ClassificationResult(
                tier=TaskTier.OPUS,
                rule='phloe_orm_safety',
                confidence=1.0,
                explanation=(
                    f'Phloe ORM keyword: {orm_match[0]} '
                    f'— routing to Opus for tenant safety'
                ),
            )

    # ── Rule 4: REVIEW + complex context → Claude ─────────────────────────
    if risk_level == 'review':
        has_debug = any(kw in prompt_lower for kw in _DEBUG_KEYWORDS)
        if context_files >= 3 or has_debug:
            reason = (
                f'{context_files} files in context'
                if context_files >= 3
                else 'debug/error context'
            )
            return ClassificationResult(
                tier=TaskTier.CLAUDE,
                rule='review_complex',
                confidence=0.9,
                explanation=f'REVIEW risk + {reason}',
            )

        # ── Rule 5: REVIEW, simple → DeepSeek ────────────────────────────
        return ClassificationResult(
            tier=TaskTier.DEEPSEEK,
            rule='default_review',
            confidence=0.8,
            explanation='REVIEW risk, single-file edit',
        )

    # ── Rule 6: SAFE + simple read/explain + few files → Local ───────────
    if risk_level == 'safe':
        has_local_verb = any(verb in prompt_lower for verb in _LOCAL_VERBS)
        if has_local_verb and context_files <= 2:
            return ClassificationResult(
                tier=TaskTier.LOCAL,
                rule='safe_simple_read',
                confidence=0.85,
                explanation='SAFE risk, simple read/explain, few files',
            )

    # ── Rule 7: Default — unclassified SAFE/REVIEW → DeepSeek ────────────
    return ClassificationResult(
        tier=TaskTier.DEEPSEEK,
        rule='default_safe',
        confidence=0.7,
        explanation='Unclassified SAFE task — routing to DeepSeek',
    )


def explain_classification(result: ClassificationResult) -> str:
    """
    Return a human-readable routing decision string.

    Format:  → Tier 2 (DeepSeek): REVIEW risk, single-file edit [rule: default_review]
    """
    return (
        f'→ Tier {result.tier.value} ({result.tier.label()}): '
        f'{result.explanation} '
        f'[rule: {result.rule}]'
    )
