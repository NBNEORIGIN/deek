"""Conversational reply normaliser — maps free-form prose replies
back onto structured answer blocks via a local LLM call.

When a user replies to a Deek-generated email (memory brief or
triage digest) in natural language — without keeping the
``--- Q<n> (category) ---`` delimiters, without using the
TRUE/FALSE/YES/NO/USE/EDIT/REJECT shortcuts — we need to figure
out which question each fragment of prose addresses and classify
the user's verdict.

That's an LLM-shaped task, so we send it to local Qwen via Ollama
(Tailscale → deek-gpu). Zero external cost. Shadow-mode gated
for the first week so Toby can review the normaliser's output
before it starts mutating memory.

Public API:

    normalise_conversational_reply(
        body: str,
        questions: list[ConversationalQuestion],
        *,
        kind: str = 'brief',          # 'brief' | 'triage'
    ) -> list[NormalisedAnswer] | None

Returns ``None`` when:
  * Ollama is unreachable or errors
  * the model returns malformed JSON
  * body is empty

On success returns one NormalisedAnswer per input question (some
may have verdict='empty' if the user didn't address that Q).
Callers convert these into ParsedAnswer instances.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx


logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = 'http://localhost:11434'
DEFAULT_MODEL = 'qwen2.5:7b-instruct'
DEFAULT_TIMEOUT = 30.0

# Verdicts the brief parser already knows about. Triage adds
# select_candidate + edit + text to this set.
_BRIEF_VERDICTS = frozenset({'affirm', 'deny', 'correct', 'empty'})
_TRIAGE_VERDICTS = frozenset({
    'affirm', 'deny', 'select_candidate', 'edit', 'text', 'empty',
})


# ── Public types ─────────────────────────────────────────────────────

@dataclass
class ConversationalQuestion:
    """The minimum shape the normaliser needs to map a prose reply.

    ``category`` is the same category string the structured parser
    uses (belief_audit, salience_calibration, match_confirm, etc).
    ``prompt`` is a 1-2 sentence human-readable version of the
    question; doesn't need to be the exact outgoing email text.
    ``extra`` is free-form context the model should know about —
    e.g. the 3 match_confirm candidates, or the draft reply for
    reply_approval.
    """
    q_number: int
    category: str
    prompt: str
    extra: str = ''


@dataclass
class NormalisedAnswer:
    q_number: int
    category: str
    verdict: str           # one of _BRIEF_VERDICTS | _TRIAGE_VERDICTS
    correction_text: str = ''
    edited_text: str = ''
    free_text: str = ''
    selected_candidate_index: int | None = None
    # Confidence the LLM assigned (0-1). Sub-0.3 answers are
    # downgraded to verdict='empty' on the caller side if desired.
    confidence: float = 1.0


# ── Shadow-mode gate ────────────────────────────────────────────────

def is_conversational_shadow() -> bool:
    """Default: shadow-on. The normaliser runs but its output is
    logged only — the caller treats the reply as unanswered until
    the cutover cron flips this off (scheduled 2026-05-06)."""
    raw = (os.getenv('DEEK_CONVERSATIONAL_REPLY_SHADOW') or 'true').strip().lower()
    return raw in {'true', '1', 'yes', 'on'}


# ── Prompt construction ──────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a reply normaliser. Given a list of questions a system asked a human, and the human's free-form email reply, extract one structured answer per question.

Output a single JSON object with one key, "answers", whose value is a list of objects. Each object has:

  q_number          — integer matching the question list
  category          — string, copy from the question
  verdict           — one of: affirm | deny | correct | empty | select_candidate | edit | text
  correction_text   — free text: any context, reasoning, or correction the user gave. Empty string if none.
  edited_text       — (reply_approval only) the user's rewritten version of the draft reply, if they offered one
  free_text         — (notes / open_ended / text verdict) the free-form content they want remembered
  selected_candidate_index — (match_confirm only) 1/2/3 if they named a specific candidate, else null
  confidence        — float 0-1, how sure you are of this mapping

Rules:
1. NEVER invent content the user didn't say. If they didn't address a question, verdict='empty' and all text fields are "".
2. "Yes/confirmed/true/right" → affirm. "No/false/wrong" → deny (for brief categories) or text (for triage match_confirm if they explicitly named a different project).
3. For triage reply_approval: "use/send/looks good" → affirm. "reject/don't send/no" → deny. Any rewrite they provided → edit with edited_text.
4. For triage match_confirm: "yes it's #1" or "confirm" → affirm. "no that's wrong" → deny. "actually it's #2" → select_candidate with selected_candidate_index=2. Naming a completely different project → text with free_text.
5. For project_folder: any path-like string → text with free_text=<that path>. "skip" / no mention → empty.
6. For notes / open_ended: any relevant note → text with free_text=<the note>. Otherwise empty.
7. If the user's reply addresses multiple questions in one sentence ("yes and definitely important"), split the attribution sensibly.
8. Output ONLY the JSON object. No prose, no markdown fences, no commentary.
"""


def _build_user_prompt(
    body: str,
    questions: list[ConversationalQuestion],
    kind: str,
) -> str:
    lines = [
        f'KIND: {kind}',
        '',
        'QUESTIONS:',
    ]
    for q in questions:
        lines.append(f'  Q{q.q_number} ({q.category}): {q.prompt}')
        if q.extra:
            for xline in q.extra.splitlines():
                lines.append(f'    {xline}')
    lines.append('')
    lines.append("USER'S REPLY:")
    lines.append(body.strip())
    lines.append('')
    lines.append('Output JSON now.')
    return '\n'.join(lines)


# ── LLM call + parse ─────────────────────────────────────────────────

def _call_ollama(
    system: str, user: str, *, base_url: str, model: str, timeout: float,
) -> str | None:
    """Single-shot chat call. Returns raw content text or None on
    any failure. Never raises.
    """
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'stream': False,
        'options': {
            'temperature': 0.1,   # deterministic-ish — we want a faithful map
            'num_ctx': 8192,
        },
        'format': 'json',         # ollama's JSON-mode hint
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f'{base_url}/api/chat', json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning('[conversational] ollama call failed: %s', exc)
        return None
    return (data.get('message') or {}).get('content') or None


def _parse_model_json(content: str) -> dict | None:
    """Best-effort JSON extract. Strips ```json fences if the model
    ignored the 'no markdown' rule."""
    if not content:
        return None
    text = content.strip()
    # Strip fenced blocks
    fence = re.match(r'^```(?:json)?\s*(.+?)\s*```$', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try finding the first balanced {...}
        brace = re.search(r'\{.*\}', text, re.DOTALL)
        if brace:
            try:
                return json.loads(brace.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _validate_answer(
    raw: dict, allowed_verdicts: frozenset[str],
) -> NormalisedAnswer | None:
    """Coerce one raw dict into a NormalisedAnswer. Returns None if
    missing required fields."""
    try:
        q_num = int(raw.get('q_number'))
    except (TypeError, ValueError):
        return None
    category = str(raw.get('category') or '').strip()
    verdict = str(raw.get('verdict') or '').strip().lower()
    if not category or verdict not in allowed_verdicts:
        return None

    sel = raw.get('selected_candidate_index')
    try:
        sel_idx = int(sel) if sel is not None else None
    except (TypeError, ValueError):
        sel_idx = None
    if sel_idx is not None and not (1 <= sel_idx <= 3):
        sel_idx = None

    try:
        conf = float(raw.get('confidence', 1.0))
    except (TypeError, ValueError):
        conf = 1.0

    return NormalisedAnswer(
        q_number=q_num,
        category=category,
        verdict=verdict,
        correction_text=str(raw.get('correction_text') or '').strip(),
        edited_text=str(raw.get('edited_text') or '').strip(),
        free_text=str(raw.get('free_text') or '').strip(),
        selected_candidate_index=sel_idx,
        confidence=max(0.0, min(1.0, conf)),
    )


def normalise_conversational_reply(
    body: str,
    questions: list[ConversationalQuestion],
    *,
    kind: str = 'brief',
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[NormalisedAnswer] | None:
    """Map a free-form prose reply onto structured answer blocks.

    Returns None on any error (caller falls back to the existing
    "treat whole body as one open_ended answer" path). Returns an
    empty list if the model legitimately concluded the user
    addressed none of the questions.
    """
    if not body or not body.strip() or not questions:
        return None
    base = (base_url or os.getenv('OLLAMA_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
    mdl = model or os.getenv('OLLAMA_CONVERSATIONAL_MODEL') or DEFAULT_MODEL

    allowed = _BRIEF_VERDICTS if kind == 'brief' else _TRIAGE_VERDICTS

    user_prompt = _build_user_prompt(body, questions, kind)
    content = _call_ollama(
        _SYSTEM_PROMPT, user_prompt,
        base_url=base, model=mdl, timeout=timeout,
    )
    if content is None:
        return None
    parsed = _parse_model_json(content)
    if not isinstance(parsed, dict):
        logger.warning('[conversational] model returned non-JSON')
        return None
    raw_answers = parsed.get('answers') or []
    if not isinstance(raw_answers, list):
        logger.warning('[conversational] model returned non-list answers')
        return None

    out: list[NormalisedAnswer] = []
    for raw in raw_answers:
        if not isinstance(raw, dict):
            continue
        ans = _validate_answer(raw, allowed)
        if ans is None:
            continue
        out.append(ans)
    return out


# ── Audit logging (shadow mode) ──────────────────────────────────────

def log_conversational_shadow(
    conn,
    *,
    source: str,                     # 'brief' | 'triage'
    reference_id: str,               # run_id / triage_id as string
    raw_body: str,
    normalised: list[NormalisedAnswer] | None,
    applied: bool,
) -> int | None:
    """Insert a row in cairn_intel.conversational_reply_shadow so
    Toby can audit the normaliser's output during the shadow period.
    Never raises.
    """
    try:
        payload = {
            'source': source,
            'reference_id': reference_id,
            'answers': [a.__dict__ for a in (normalised or [])],
        }
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cairn_intel.conversational_reply_shadow
                    (source, reference_id, raw_body, normalised,
                     applied, created_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s, NOW())
                   RETURNING id""",
                (source, reference_id, raw_body[:4000],
                 json.dumps(payload), applied),
            )
            (new_id,) = cur.fetchone()
            conn.commit()
            return int(new_id)
    except Exception as exc:
        logger.warning('[conversational] shadow log failed: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


__all__ = [
    'ConversationalQuestion',
    'NormalisedAnswer',
    'normalise_conversational_reply',
    'is_conversational_shadow',
    'log_conversational_shadow',
]
