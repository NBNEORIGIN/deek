import os
from enum import Enum


class ModelChoice(str, Enum):
    LOCAL = 'local'
    API = 'api'


# Keywords that strongly suggest API model needed
COMPLEX_KEYWORDS = {
    'architect', 'design', 'strategy', 'why', 'explain',
    'review', 'security', 'performance', 'refactor',
    'analyse', 'analyze', 'assess', 'compare', 'decide',
    'plan', 'structure', 'approach',
}

# Keywords that are clearly local-model territory
SIMPLE_KEYWORDS = {
    'fix', 'add', 'update', 'rename', 'move', 'delete',
    'format', 'lint', 'test', 'import', 'comment',
    'docstring', 'type hint', 'variable', 'typo',
}

# RTX 3050 8GB runs Qwen 7B well up to ~6k context tokens
LOCAL_CONTEXT_LIMIT = 6000


def route(
    task: str,
    context_tokens: int,
    project_config: dict,
) -> ModelChoice:
    """
    Decide whether to use local Qwen or Claude API.

    Priority order:
    1. Project config override (operator can force a model)
    2. Context too large for local model → API
    3. Complex keywords → API
    4. Simple keywords → Local
    5. Default → Local (save API costs)
    """
    forced_model = project_config.get('force_model')
    if forced_model == 'api':
        return ModelChoice.API
    if forced_model == 'local':
        return ModelChoice.LOCAL

    if context_tokens > LOCAL_CONTEXT_LIMIT:
        return ModelChoice.API

    task_lower = task.lower()

    if any(kw in task_lower for kw in COMPLEX_KEYWORDS):
        return ModelChoice.API

    if any(kw in task_lower for kw in SIMPLE_KEYWORDS):
        return ModelChoice.LOCAL

    return ModelChoice.LOCAL


def estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return len(text) // 4
