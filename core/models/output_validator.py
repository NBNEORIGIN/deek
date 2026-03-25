"""
Output Validator — post-response quality checks.

Runs after every model response. All checks are synchronous, pure,
and complete in < 50ms. No LLM calls. No blocking I/O.

Checks:
  CHECK 1 — tool description:  model said "I would call X" but didn't call X
  CHECK 2 — refusal:           model declined to answer
  CHECK 3 — hallucinated file: model referenced a file that doesn't exist
  CHECK 4 — tenant isolation:  phloe ORM query without tenant filter nearby
  CHECK 5 — empty response:    blank or < 20 char response
  CHECK 6 — Python syntax:     written .py file has a syntax error
"""
import ast
import os
import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    # True if escalating to a higher tier may produce a better response.
    escalate: bool = False
    # True if the failure cannot be recovered by escalating (e.g. config error).
    hard_fail: bool = False


# ── CHECK 1 ───────────────────────────────────────────────────────────────────

_TOOL_DESCRIPTION_PHRASES = (
    'i would call',
    "i'll use the",
    'using the edit_file tool',
    'calling read_file',
    'by calling the',
    'i would use the',
    'i need to call',
    'will call the',
)


def check_tool_description(
    response_text: str,
    executed_tool_calls: list[dict],
) -> str | None:
    """
    Fail if the response describes a tool call in text but no tool was executed.
    Returns a failure message or None if the check passes.
    """
    if executed_tool_calls:
        return None  # Tools ran — any description is post-hoc narration, not failure
    text_lower = response_text.lower()
    for phrase in _TOOL_DESCRIPTION_PHRASES:
        if phrase in text_lower:
            return f'CHECK 1: tool description without execution ("{phrase}")'
    return None


# ── CHECK 2 ───────────────────────────────────────────────────────────────────

_REFUSAL_PHRASES = (
    'i cannot',
    "i don't have access",
    "i'm unable to",
    'as an ai',
    'i am unable to',
    'i am not able to',
)


def check_refusal(response_text: str) -> str | None:
    """Fail if the model refused to answer."""
    text_lower = response_text.lower()
    for phrase in _REFUSAL_PHRASES:
        if phrase in text_lower:
            return f'CHECK 2: refusal detected ("{phrase}")'
    return None


# ── CHECK 3 ───────────────────────────────────────────────────────────────────

_FILE_PATH_PATTERN = re.compile(
    r'(?<![`\'"])'          # not already in a quote
    r'([A-Za-z0-9_./\\-]+'  # path characters
    r'\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|html|css|sql))'
    r'(?![`\'"])',           # not followed by closing quote
)


def check_hallucinated_file(
    response_text: str,
    files_in_context: list[str],
    project_root: str = '.',
) -> str | None:
    """
    Fail if the response references a file path that is not in context
    and does not exist on disk.
    Only checks paths that look like relative code paths (not URLs).
    """
    matches = _FILE_PATH_PATTERN.findall(response_text)
    context_set = {os.path.normpath(f) for f in files_in_context}

    for raw_path in matches:
        if '://' in raw_path or raw_path.startswith('http'):
            continue
        norm = os.path.normpath(raw_path)
        if norm in context_set:
            continue
        abs_path = os.path.join(project_root, raw_path)
        if os.path.exists(abs_path) or os.path.exists(raw_path):
            continue
        return f'CHECK 3: hallucinated file path "{raw_path}"'
    return None


# ── CHECK 4 ───────────────────────────────────────────────────────────────────

_ORM_PATTERN = re.compile(r'\.objects\.(filter|all|get|exclude|count)\b')
_TENANT_WINDOW = 200  # chars either side of the ORM call


def check_tenant_isolation(response_text: str, project: str) -> str | None:
    """
    For phloe project: fail if an ORM queryset call appears without
    a tenant reference within 200 characters.
    """
    if project != 'phloe':
        return None
    for m in _ORM_PATTERN.finditer(response_text):
        start = max(0, m.start() - _TENANT_WINDOW)
        end = min(len(response_text), m.end() + _TENANT_WINDOW)
        window = response_text[start:end].lower()
        if 'tenant' not in window:
            return (
                f'CHECK 4: ORM call .objects.{m.group(1)}() '
                f'without tenant filter within {_TENANT_WINDOW} chars'
            )
    return None


# ── CHECK 5 ───────────────────────────────────────────────────────────────────

def check_empty_response(response_text: str) -> str | None:
    """Fail if the response is blank or suspiciously short."""
    if not response_text or len(response_text.strip()) < 20:
        return 'CHECK 5: empty or near-empty response'
    return None


# ── CHECK 6 ───────────────────────────────────────────────────────────────────

def check_python_syntax(written_files: list[str]) -> str | None:
    """
    Fail if any .py file that was just written contains a syntax error.
    Reads the file from disk — only fires when file actually exists.
    """
    for path in written_files:
        if not path.endswith('.py'):
            continue
        try:
            source = open(path, encoding='utf-8').read()
        except OSError:
            continue
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return f'CHECK 6: syntax error in {path}: {exc}'
    return None


# ── Main validator ─────────────────────────────────────────────────────────────

def validate(
    response_text: str,
    executed_tool_calls: list[dict] | None = None,
    files_in_context: list[str] | None = None,
    written_files: list[str] | None = None,
    project: str = '',
    project_root: str = '.',
) -> ValidationResult:
    """
    Run all 6 checks against a model response.

    Args:
        response_text:       The raw text from the model.
        executed_tool_calls: Tools that actually ran this turn.
        files_in_context:    File paths currently injected in context.
        written_files:       File paths written/created this turn.
        project:             Project name (for phloe-specific checks).
        project_root:        Filesystem root for existence checks.

    Returns ValidationResult. All checks run even if an earlier one fails,
    so the full failure list is available for logging.
    """
    executed_tool_calls = executed_tool_calls or []
    files_in_context = files_in_context or []
    written_files = written_files or []

    failures: list[str] = []

    f1 = check_tool_description(response_text, executed_tool_calls)
    if f1:
        failures.append(f1)

    f2 = check_refusal(response_text)
    if f2:
        failures.append(f2)

    f3 = check_hallucinated_file(response_text, files_in_context, project_root)
    if f3:
        failures.append(f3)

    f4 = check_tenant_isolation(response_text, project)
    if f4:
        failures.append(f4)

    f5 = check_empty_response(response_text)
    if f5:
        failures.append(f5)

    f6 = check_python_syntax(written_files)
    if f6:
        failures.append(f6)

    if not failures:
        return ValidationResult(passed=True)

    # Determine if escalation can help or if it's a hard failure
    hard_fail = bool(f4)  # Tenant isolation is a config/logic error — escalation won't fix it
    escalate = not hard_fail and bool(failures)

    return ValidationResult(
        passed=False,
        failures=failures,
        escalate=escalate,
        hard_fail=hard_fail,
    )
