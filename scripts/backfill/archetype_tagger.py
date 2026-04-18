"""
Claude Haiku wrapper for context summarisation and archetype tagging.

Two jobs:
    1. ``summarise(raw_text)``  — distil a raw source record (email
       thread, m_number narrative, xero P&L month) into 3–5 sentences
       that will become ``decisions.context_summary``.
    2. ``tag(summary)``          — pick 2–4 archetype tags from the
       fixed taxonomy. The taxonomy must NOT be extended here —
       changes go through a dedicated re-tagging pass per the brief.

Budget is enforced by ``LLMBudget``. Every call increments
``bulk_used`` — Haiku is cheap but a runaway loop is still worth
catching.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .llm_budget import LLMBudget

log = logging.getLogger(__name__)


# Canonical taxonomy — do NOT extend without a re-tagging pass.
ARCHETYPES = [
    'adversarial',
    'cooperative',
    'time_pressured',
    'information_asymmetric',
    'repeated_game',
    'one_shot',
    'pricing',
    'operational',
]


_SUMMARISE_SYSTEM = (
    'You are a concise business analyst. Your job is to summarise a '
    'historical business record into 3 to 5 sentences that preserve '
    'the structural details of the decision: who was involved, what '
    'was at stake, what constraints applied, and what was ultimately '
    'chosen. Do not add commentary. Do not speculate. Return the '
    'summary only, no preamble.'
)


_TAG_SYSTEM = (
    'You are a taxonomy classifier. Given a short business decision '
    "summary, return a JSON array of 2 to 4 archetype tags from this "
    "EXACT closed list — do not invent new tags:\n\n"
    '  adversarial          — negotiation with an opposing party\n'
    '  cooperative          — collaborative arrangement\n'
    '  time_pressured       — decision had a deadline or was reactive\n'
    '  information_asymmetric — one side knew things the other did not\n'
    '  repeated_game        — ongoing relationship, reputation matters\n'
    '  one_shot             — transactional, no repeat\n'
    '  pricing              — decision was about price, margin or terms\n'
    '  operational          — how to execute, rather than whether to\n\n'
    'Return JSON only, like ["pricing","adversarial"]. No prose.'
)


class ArchetypeTagger:
    """Haiku wrapper. Lazily instantiates the SDK so tests can avoid network."""

    def __init__(
        self,
        budget: LLMBudget,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.budget = budget
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY', '')
        self.model = model or os.getenv(
            'CAIRN_INTEL_BULK_MODEL', 'claude-haiku-4-5-20251001'
        )
        self._client: Any = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    # ── summarisation ───────────────────────────────────────────────────

    def summarise(self, raw_text: str, source_label: str = 'unknown') -> str:
        self.budget.consume_bulk(source=source_label)

        # ── Primary path: local Ollama ────────────────────────────────
        local_model = os.getenv('OLLAMA_CLASSIFIER_MODEL', '').strip()
        if local_model:
            local_out = _call_ollama(
                system=_SUMMARISE_SYSTEM,
                user=raw_text[:8000],
                model=local_model,
                max_tokens=300,
                use_json_format=False,
            )
            if local_out is not None:
                return local_out.strip()

        # ── Fallback: Haiku ───────────────────────────────────────────
        if not self.api_key:
            return ''
        client = self._get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=300,
            system=_SUMMARISE_SYSTEM,
            messages=[{'role': 'user', 'content': raw_text[:8000]}],
        )
        return _first_text(resp).strip()

    # ── tagging ─────────────────────────────────────────────────────────

    def tag(self, summary: str, source_label: str = 'unknown') -> list[str]:
        self.budget.consume_bulk(source=source_label)

        # ── Primary path: local Ollama (no json format — output is a bare array) ─
        local_model = os.getenv('OLLAMA_CLASSIFIER_MODEL', '').strip()
        if local_model:
            local_out = _call_ollama(
                system=_TAG_SYSTEM,
                user=summary[:4000],
                model=local_model,
                max_tokens=120,
                use_json_format=False,
            )
            if local_out is not None:
                tags = _parse_tags(local_out.strip())
                if tags:
                    return tags

        # ── Fallback: Haiku ───────────────────────────────────────────
        if not self.api_key:
            return []
        client = self._get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=120,
            system=_TAG_SYSTEM,
            messages=[{'role': 'user', 'content': summary[:4000]}],
        )
        raw = _first_text(resp).strip()
        return _parse_tags(raw)


# ── Helpers ────────────────────────────────────────────────────────────


def _first_text(response: Any) -> str:
    """Pull the first text block out of an Anthropic response."""
    try:
        for block in response.content:
            if getattr(block, 'type', '') == 'text':
                return block.text
    except Exception:
        pass
    return ''


def _call_ollama(
    system: str, user: str, model: str, max_tokens: int,
    use_json_format: bool = True,
) -> str | None:
    """Call local Ollama /api/chat. Returns text content or None on failure.

    ``use_json_format`` enables Ollama's format=json for callers that
    expect structured JSON. Summarisation should leave this False so
    the model can emit prose; tag parsing is forgiving so False is fine
    there too (the bare array is easily parsed).
    """
    base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')
    payload: dict[str, Any] = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'stream': False,
        'options': {'num_predict': max_tokens, 'temperature': 0.0},
    }
    if use_json_format:
        payload['format'] = 'json'
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f'{base_url}/api/chat', json=payload)
    except Exception as exc:
        log.warning('archetype_tagger: Ollama call failed (%s) — falling back',
                    type(exc).__name__)
        return None
    if r.status_code != 200:
        log.warning('archetype_tagger: Ollama HTTP %d — falling back', r.status_code)
        return None
    try:
        return (r.json().get('message', {}) or {}).get('content', '')
    except Exception:
        return None


def _parse_tags(raw: str) -> list[str]:
    """Parse the tag LLM output into a validated tag list.

    Accepts ``["pricing","adversarial"]`` or ``pricing, adversarial``.
    Silently drops anything not in the canonical taxonomy.
    """
    tags: list[str] = []
    raw = raw.strip()
    if not raw:
        return tags
    # Try JSON first
    parsed: Any = None
    try:
        parsed = json.loads(raw)
    except Exception:
        # Fall back to comma-split
        parsed = [part.strip(' "\'[]') for part in raw.split(',')]
    if not isinstance(parsed, list):
        return tags
    valid = set(ARCHETYPES)
    for item in parsed:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower()
        if cleaned in valid and cleaned not in tags:
            tags.append(cleaned)
    return tags[:4]
