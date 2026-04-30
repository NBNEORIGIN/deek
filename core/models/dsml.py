"""DeepSeek DSML markup parser — shared between DeepSeekClient (direct
DeepSeek API) and OpenAIClient (OpenRouter routing DeepSeek under the
hood). Both surfaces hit the same problem: the OpenAI-compatible
``tools`` parameter is sent on every request, but DeepSeek occasionally
ignores it and emits the function call as plain DSML text instead of
returning structured ``tool_calls``. Without parsing, the user sees
raw <｜DSML｜…> tokens in the chat.

On-the-wire variants observed:

  single pipe + function_calls  (DeepSeek V3, original)
      <｜DSML｜function_calls>
        <｜DSML｜invoke name="read_file">
          <｜DSML｜parameter name="file_path" string="true">.env</｜DSML｜parameter>
        </｜DSML｜invoke>
      </｜DSML｜function_calls>

  double pipe + tool_calls      (later DeepSeek model revisions, 2026-04-30)
      <｜｜DSML｜｜tool_calls>
        <｜｜DSML｜｜invoke name="search_wiki">
          <｜｜DSML｜｜parameter name="query" string="true">…</｜｜DSML｜｜parameter>
        </｜｜DSML｜｜invoke>
      </｜｜DSML｜｜tool_calls>

The regex tolerates 1+ pipe characters (full-width ｜ OR ASCII) and
both outer-tag names so future variants are likely to keep parsing
without code changes.
"""
from __future__ import annotations

import re
import uuid

# Match a DSML opener anywhere in the text — fast path before the
# heavier invoke/parameter regexes.
_DSML_OPEN_RE = re.compile(
    r'<[｜|]+\s*DSML\s*[｜|]+\s*(?:tool_calls|function_calls)\s*>',
    re.IGNORECASE,
)
_DSML_INVOKE_RE = re.compile(
    r'<[｜|]+\s*DSML\s*[｜|]+\s*invoke\s+name="([^"]+)"\s*>'
    r'(.*?)'
    r'</[｜|]+\s*DSML\s*[｜|]+\s*invoke\s*>',
    re.DOTALL | re.IGNORECASE,
)
_DSML_PARAM_RE = re.compile(
    r'<[｜|]+\s*DSML\s*[｜|]+\s*parameter\s+name="([^"]+)"[^>]*>'
    r'(.*?)'
    r'</[｜|]+\s*DSML\s*[｜|]+\s*parameter\s*>',
    re.DOTALL | re.IGNORECASE,
)


def has_dsml_markup(text: str) -> bool:
    """Cheap pre-check before the full regex parse."""
    return bool(text) and _DSML_OPEN_RE.search(text) is not None


def parse_dsml_tool_call(text: str) -> tuple[str, dict | None]:
    """Parse a DSML tool call out of response text.

    Returns ``(clean_text_before_dsml, tool_call_dict | None)``. The
    tool_call dict, if present, has ``{name, input, tool_use_id}`` —
    same shape as the structured tool_calls path so the agent loop
    treats them identically.
    """
    if not has_dsml_markup(text):
        return text, None

    open_match = _DSML_OPEN_RE.search(text)
    if not open_match:
        return text, None
    clean_text = text[: open_match.start()].strip()
    after = text[open_match.start():]

    invoke = _DSML_INVOKE_RE.search(after)
    if not invoke:
        return clean_text, None

    tool_name = invoke.group(1).strip()
    params: dict = {}
    for pm in _DSML_PARAM_RE.finditer(invoke.group(2)):
        params[pm.group(1).strip()] = pm.group(2).strip()

    return clean_text, {
        'name': tool_name,
        'input': params,
        'tool_use_id': f'dsml-{uuid.uuid4().hex[:8]}',
    }


__all__ = ['has_dsml_markup', 'parse_dsml_tool_call']
