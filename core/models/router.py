"""
Model routing — selects which inference provider to use per request.

Tier priority (lowest cost first):
  Tier 1 — Ollama (local, free)     CLAW_FORCE_API=false + Ollama reachable
  Tier 2 — DeepSeek (cheap API)     DEEPSEEK_API_KEY set + DEEPSEEK_ENABLED=true
  Tier 3 — Claude Sonnet (default)  ANTHROPIC_API_KEY set
  Tier 4 — Claude Opus (premium)    Architecture / security keywords only
  OpenAI — 429/529 fallback only    NOT a numbered tier; never routed to directly

Routing summary table:
  ┌────────────────────────────────────────┬──────────────────────┐
  │ Condition                               │ Provider             │
  ├────────────────────────────────────────┼──────────────────────┤
  │ CLAW_FORCE_API=false + Ollama          │ Ollama (local)       │
  │ DEEPSEEK_API_KEY + not Opus kw         │ DeepSeek V3          │
  │ Opus keywords present                   │ Claude Opus          │
  │ Default                                 │ Claude Sonnet        │
  │ Claude 429/529 + DeepSeek avail         │ DeepSeek (fallback)  │
  │ Claude 429/529 + no DeepSeek           │ OpenAI gpt-4o        │
  │ CLAW_TIER4_PROJECTS contains project   │ Tier 4 minimum       │
  └────────────────────────────────────────┴──────────────────────┘

Note: CLAW_FORCE_API=true bypasses Ollama only — DeepSeek and Claude
are both still available when this flag is set.
"""
import os
from dataclasses import dataclass
from enum import Enum

from core.models.task_classifier import classify, TaskTier


class ModelChoice(str, Enum):
    LOCAL    = 'local'
    DEEPSEEK = 'deepseek'
    API      = 'api'          # Claude (Sonnet or Opus)


@dataclass(frozen=True)
class RoutingDecision:
    choice: ModelChoice
    desired_tier: TaskTier
    actual_tier: TaskTier
    use_opus: bool
    rule: str
    confidence: float
    explanation: str


# RTX 3050 8GB runs Qwen 7B well up to ~6k context tokens
LOCAL_CONTEXT_LIMIT = 6000

# Legacy keyword sets kept for backwards compatibility with any direct callers
OPUS_KEYWORDS = {
    'architect', 'architecture', 'design decision',
    'security review', 'performance review', 'why is this',
    'root cause', 'fundamentally', 'approach to',
    'best way to structure', 'trade off', 'trade-off',
}
COMPLEX_KEYWORDS = {
    'architect', 'design', 'strategy', 'why', 'explain',
    'review', 'security', 'performance', 'refactor',
    'analyse', 'analyze', 'assess', 'compare', 'decide',
    'plan', 'structure', 'approach',
}


def route(
    task: str,
    context_tokens: int,
    project_config: dict,
    risk_level: str = 'safe',
    # Extended parameters (Feature 1b):
    project: str = '',
    files_in_context: int = 0,
    force_tier: int | None = None,
) -> ModelChoice:
    """Compatibility wrapper for callers that only need the provider choice."""
    return route_decision(
        task=task,
        context_tokens=context_tokens,
        project_config=project_config,
        risk_level=risk_level,
        project=project,
        files_in_context=files_in_context,
        force_tier=force_tier,
    ).choice


def route_decision(
    task: str,
    context_tokens: int,
    project_config: dict,
    risk_level: str = 'safe',
    project: str = '',
    files_in_context: int = 0,
    force_tier: int | None = None,
) -> RoutingDecision:
    """
    Full model routing decision.

    Args:
        task:             The user message / prompt text.
        context_tokens:   Estimated token count of the full context.
        project_config:   Project-level config dict (may include force_model).
        risk_level:       Current tool's risk level ('safe', 'review', 'destructive').
        project:          Project name for project-specific routing rules.
        files_in_context: Number of files currently in context window.
        force_tier:       Override to a specific tier number (1–4).

    Returns RoutingDecision with the selected provider and resolved tier.
    """
    force_api = os.getenv('CLAW_FORCE_API', 'true').lower() == 'true'

    # ── CLAW_TIER4_PROJECTS: certain projects always use Tier 4 minimum ───
    tier4_projects_raw = os.getenv('CLAW_TIER4_PROJECTS', '')
    tier4_projects = {p.strip() for p in tier4_projects_raw.split(',') if p.strip()}
    if project and project in tier4_projects:
        return RoutingDecision(
            choice=ModelChoice.API,
            desired_tier=TaskTier.OPUS,
            actual_tier=TaskTier.OPUS,
            use_opus=True,
            rule='tier4_project',
            confidence=1.0,
            explanation=f'Project {project} requires Tier 4 minimum',
        )

    # ── Explicit force_tier override ──────────────────────────────────────
    if force_tier is not None:
        desired_tier = TaskTier(force_tier)
        choice = _resolve_tier(desired_tier, force_api, context_tokens)
        use_opus = desired_tier == TaskTier.OPUS and choice == ModelChoice.API
        return RoutingDecision(
            choice=choice,
            desired_tier=desired_tier,
            actual_tier=_actual_tier(choice, use_opus),
            use_opus=use_opus,
            rule='force_tier',
            confidence=1.0,
            explanation=f'Forced to Tier {desired_tier.value}',
        )

    # ── Project-level config override ─────────────────────────────────────
    forced_model = project_config.get('force_model')
    if forced_model == 'api':
        return RoutingDecision(
            choice=ModelChoice.API,
            desired_tier=TaskTier.CLAUDE,
            actual_tier=TaskTier.CLAUDE,
            use_opus=False,
            rule='project_force_api',
            confidence=1.0,
            explanation='Project config forces Claude API routing',
        )
    if forced_model == 'deepseek':
        choice = ModelChoice.DEEPSEEK if _deepseek_available() else ModelChoice.API
        return RoutingDecision(
            choice=choice,
            desired_tier=TaskTier.DEEPSEEK,
            actual_tier=_actual_tier(choice, False),
            use_opus=False,
            rule='project_force_deepseek',
            confidence=1.0,
            explanation='Project config forces DeepSeek when available',
        )
    if forced_model == 'local':
        return RoutingDecision(
            choice=ModelChoice.LOCAL,
            desired_tier=TaskTier.LOCAL,
            actual_tier=TaskTier.LOCAL,
            use_opus=False,
            rule='project_force_local',
            confidence=1.0,
            explanation='Project config forces local routing',
        )

    # ── Classify desired tier using task classifier ────────────────────────
    classification = classify(
        prompt=task,
        risk_level=risk_level,
        project=project,
        context_files=files_in_context,
    )
    desired_tier = classification.tier

    # ── Promote to next available tier ────────────────────────────────────
    choice = _resolve_tier(desired_tier, force_api, context_tokens)
    use_opus = desired_tier == TaskTier.OPUS and choice == ModelChoice.API
    return RoutingDecision(
        choice=choice,
        desired_tier=desired_tier,
        actual_tier=_actual_tier(choice, use_opus),
        use_opus=use_opus,
        rule=classification.rule,
        confidence=classification.confidence,
        explanation=classification.explanation,
    )


def _resolve_tier(
    desired: TaskTier,
    force_api: bool,
    context_tokens: int,
) -> ModelChoice:
    """
    Walk up from the desired tier to the nearest available tier.
    Returns ModelChoice for the first available tier >= desired.

    CLAW_MAX_TIER (env int, default 4) caps the tier ceiling — useful to force
    all traffic through DeepSeek/OpenRouter and prevent Claude API spend.
    Set CLAW_MAX_TIER=2 to cap at DeepSeek for all non-destructive tasks.
    """
    try:
        max_tier = TaskTier(int(os.getenv('CLAW_MAX_TIER', '4')))
    except (ValueError, KeyError):
        max_tier = TaskTier.OPUS

    effective_desired = min(desired, max_tier)

    for tier in TaskTier:
        if tier < effective_desired:
            continue
        if tier > max_tier:
            break
        choice = _tier_available(tier, force_api, context_tokens)
        if choice is not None:
            return choice

    # Final fallback: cheapest available provider
    if _deepseek_available():
        return ModelChoice.DEEPSEEK
    if os.getenv('ANTHROPIC_API_KEY', ''):
        return ModelChoice.API
    # No provider configured — return API and let the caller handle auth failure
    return ModelChoice.API


def _tier_available(
    tier: TaskTier,
    force_api: bool,
    context_tokens: int,
) -> 'ModelChoice | None':
    """
    Return the ModelChoice for a tier if it is currently available,
    or None if the tier cannot be used.
    """
    if tier == TaskTier.LOCAL:
        if not force_api and context_tokens <= LOCAL_CONTEXT_LIMIT:
            return ModelChoice.LOCAL
        return None

    if tier == TaskTier.DEEPSEEK:
        if _deepseek_available():
            return ModelChoice.DEEPSEEK
        return None

    # Tier 3 (Claude Sonnet) and Tier 4 (Claude Opus) both use ModelChoice.API.
    # route_decision() carries the Sonnet-vs-Opus distinction for callers
    # that need the full routing result.
    # OpenRouter can serve Claude models via OpenAI-compatible API — treat it
    # as a valid source for Tier 3/4 when ANTHROPIC_API_KEY is absent.
    if tier in (TaskTier.CLAUDE, TaskTier.OPUS):
        if os.getenv('ANTHROPIC_API_KEY', ''):
            return ModelChoice.API
        if os.getenv('OPENROUTER_API_KEY', ''):
            return ModelChoice.API  # agent.py will route via openrouter_claude_*
        return None

    return None


def _tier_to_model_choice(tier: int, task: str) -> ModelChoice:
    """Map an explicit tier number to a ModelChoice."""
    if tier == 1:
        return ModelChoice.LOCAL
    if tier == 2:
        return ModelChoice.DEEPSEEK
    # 3 or 4 both map to API. route_decision() resolves Opus metadata.
    return ModelChoice.API


def _actual_tier(choice: ModelChoice, use_opus: bool) -> TaskTier:
    """Return the effective tier after availability promotion."""
    if choice == ModelChoice.LOCAL:
        return TaskTier.LOCAL
    if choice == ModelChoice.DEEPSEEK:
        return TaskTier.DEEPSEEK
    return TaskTier.OPUS if use_opus else TaskTier.CLAUDE


def _pick_api_tier(task: str) -> ModelChoice:
    """Sub-routing within API tier: DeepSeek vs Claude."""
    if _deepseek_available():
        from core.models.task_classifier import _OPUS_KEYWORDS
        if not any(kw in task.lower() for kw in _OPUS_KEYWORDS):
            return ModelChoice.DEEPSEEK
    return ModelChoice.API


def _deepseek_available() -> bool:
    """True when DeepSeek is configured and not explicitly disabled."""
    key = os.getenv('DEEPSEEK_API_KEY', '')
    enabled = os.getenv('DEEPSEEK_ENABLED', 'true').lower() == 'true'
    return bool(key) and enabled


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4
