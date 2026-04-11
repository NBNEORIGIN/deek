"""
Chat tool — analyze_enquiry.

Takes a free-text new enquiry (an email, a phone note, a quote
request) and produces a structured strategy brief that synthesises
every retrieval layer Cairn has:

  1. search_crm                — live CRM state for this client
                                  (or similar clients if new)
  2. retrieve_similar_decisions — counterfactual memory
                                  (disputes + b2b_quotes + crm_lessons
                                   + crm_reflections + material_prices)
                                  + related wiki articles baked in
  3. search_wiki                — policy / SOP knowledge that may not
                                  have been surfaced through the
                                  related_wiki cross-link

Output is a markdown strategy document with:

  - archetype classification
  - retrieved evidence (with citations to specific decision_ids and
    wiki file paths — no un-grounded claims)
  - game-theoretic framing (parties, objectives, BATNAs, moves)
  - recommended strategy + concrete next-step actions
  - risk flags
  - confidence rating

The tool is READ-ONLY and SAFE — it retrieves, reasons, and
recommends. It never writes anywhere. The user retains decision
authority; the tool's opening line explicitly says "recommendation,
not decision".

Design guardrails
-----------------

- **Mandatory citations**: every factual claim in the output must
  be traceable to a retrieval source. The prompt enforces this and
  results without any retrieval context fail loudly with a
  "no precedent — reasoning from principles only" confidence stamp.
- **Typed confidence**: strong_precedent | weak_precedent |
  reasoning_only. The user sees which signals were available.
- **No hidden authority**: the output header always labels the
  document as a recommendation, and ends with "what's your BATNA?"
  so the model never feels authoritative.

Model tier
----------

Uses Claude Sonnet for the synthesis step — Haiku's reasoning is
too shallow for multi-hop strategic framing. The retrieval steps
(search_crm, retrieve_similar_decisions, search_wiki) run first
and their outputs are passed into the Sonnet prompt as grounded
context.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .registry import Tool, RiskLevel


log = logging.getLogger(__name__)


CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
ANTHROPIC_SONNET_MODEL_ENV = 'CLAUDE_MODEL'
DEFAULT_SONNET_MODEL = 'claude-sonnet-4-6'


_ANALYSIS_SYSTEM = (
    'You are a senior business strategist at NBNE, a Northumberland '
    'signage manufacturer. A new enquiry has arrived. Your job is to '
    'analyse it against NBNE\'s historical decision memory and '
    'produce a STRATEGIC BRIEF with concrete recommendations the '
    'operator can act on today.\n\n'
    'You will receive:\n'
    '  - The raw enquiry text\n'
    '  - CRM_CONTEXT: relevant live records (projects, clients, '
    'materials, lessons) from search_crm\n'
    '  - DECISION_CONTEXT: counterfactual memory matches from '
    'retrieve_similar_decisions (disputes, b2b_quotes, crm_lessons, '
    'crm_reflections, material_prices)\n'
    '  - WIKI_CONTEXT: policy and process articles from search_wiki\n\n'
    'Rules:\n'
    '1. GROUND EVERY CLAIM. When you state something about a past '
    'decision, cite the decision_id. When you state a policy, cite '
    'the wiki file_path. If you cannot cite, you MUST say '
    '"reasoning from principles, no precedent found" explicitly.\n'
    '2. Classify the archetype from the closed 8-tag taxonomy: '
    'adversarial, cooperative, time_pressured, information_asymmetric, '
    'repeated_game, one_shot, pricing, operational. Pick 1-4.\n'
    '3. Apply game-theoretic framing: list the parties, each party\'s '
    'likely objective, each party\'s BATNA (walk-away), and the '
    'expected sequence of moves. Be concrete.\n'
    '4. Identify the information asymmetry: what does NBNE know that '
    'the enquirer does not, and vice versa.\n'
    '5. Recommend a strategy with 2-5 concrete next-step actions. '
    'For each action, cite the retrieval source that supports it.\n'
    '6. Flag risks explicitly — scope creep, payment risk, planning '
    'dependencies, material availability, margin erosion, reputational '
    'exposure, time sink. Cite supporting evidence.\n'
    '7. End with a CONFIDENCE STAMP: "strong_precedent" (multiple '
    'matching past decisions), "weak_precedent" (one partial match), '
    '"reasoning_only" (no relevant history — apply principles '
    'carefully).\n'
    '8. The document is a RECOMMENDATION. Start with '
    '"**Recommendation, not decision.**" Close with '
    '"**What\'s your BATNA here?**"\n\n'
    'Return a markdown document. No code fences. No preamble.'
)


def _search_crm_safe(query: str, limit: int = 5) -> str:
    """Call the search_crm tool directly (bypassing the chat agent)."""
    try:
        from .crm_tools import _search_crm
        return _search_crm(project_root='', query=query, limit=limit)
    except Exception as exc:
        return f'(search_crm unavailable: {exc})'


def _retrieve_similar_safe(query: str, limit: int = 6) -> str:
    """Call retrieve_similar_decisions directly."""
    try:
        from .intel_tools import _retrieve_similar_decisions
        return _retrieve_similar_decisions(
            project_root='', query=query, limit=limit,
        )
    except Exception as exc:
        return f'(retrieve_similar_decisions unavailable: {exc})'


def _search_wiki_safe(query: str, limit: int = 5) -> str:
    """Call search_wiki directly."""
    try:
        from .cairn_tools import _search_wiki
        return _search_wiki(project_root='', query=query, limit=limit)
    except Exception as exc:
        return f'(search_wiki unavailable: {exc})'


def _analyze_enquiry(
    project_root: str,
    enquiry: str,
    focus: str | None = None,
    **kwargs,
) -> str:
    """Tool entry point."""
    if not enquiry or not enquiry.strip():
        return 'analyze_enquiry: enquiry text is required'

    enquiry = enquiry.strip()
    # If the caller supplies a focus hint (e.g. "pricing", "dispute"),
    # append it to the retrieval queries to bias matches.
    focus_suffix = f' — focus: {focus}' if focus and focus.strip() else ''

    anthropic_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not anthropic_key:
        return (
            'analyze_enquiry: ANTHROPIC_API_KEY is not set — cannot run '
            'the synthesis step. Retrieval is available individually '
            'via search_crm / retrieve_similar_decisions / search_wiki.'
        )

    sonnet_model = os.getenv(ANTHROPIC_SONNET_MODEL_ENV, DEFAULT_SONNET_MODEL)

    # Compose retrieval queries from the enquiry text. Short enough
    # to embed well, long enough to carry archetype signal.
    query_text = (enquiry + focus_suffix)[:500]

    crm_context = _search_crm_safe(query_text, limit=5)
    decision_context = _retrieve_similar_safe(query_text, limit=6)
    wiki_context = _search_wiki_safe(query_text, limit=5)

    synthesis_input = (
        f'ENQUIRY:\n{enquiry}\n\n'
        f'---\n'
        f'CRM_CONTEXT (live records matching the enquiry):\n{crm_context}\n\n'
        f'---\n'
        f'DECISION_CONTEXT (past decisions + related wiki, from cairn_intel):\n'
        f'{decision_context}\n\n'
        f'---\n'
        f'WIKI_CONTEXT (policy and process articles):\n{wiki_context}\n\n'
        f'---\n'
        'Produce the strategic brief now.'
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        resp = client.messages.create(
            model=sonnet_model,
            max_tokens=2500,
            system=_ANALYSIS_SYSTEM,
            messages=[{'role': 'user', 'content': synthesis_input}],
        )
    except Exception as exc:
        return (
            f'analyze_enquiry: synthesis step failed ({exc}). '
            f'Retrieval ran — you can see results via search_crm, '
            f'retrieve_similar_decisions, and search_wiki separately.'
        )

    brief = _first_text(resp).strip()
    if not brief:
        return (
            'analyze_enquiry: synthesis step returned empty output. '
            'Retrieval ran but Sonnet did not produce a brief.'
        )

    # Bolt on a provenance footer so the caller can see which tools
    # fed into the brief and how much each contributed.
    footer = (
        '\n\n---\n'
        '_Provenance:_\n'
        f'- search_crm: {len(crm_context)} chars\n'
        f'- retrieve_similar_decisions: {len(decision_context)} chars\n'
        f'- search_wiki: {len(wiki_context)} chars\n'
        f'- synthesis model: {sonnet_model}'
    )
    return brief + footer


def _first_text(resp: Any) -> str:
    try:
        for block in resp.content:
            if getattr(block, 'type', '') == 'text':
                return block.text
    except Exception:
        pass
    return ''


analyze_enquiry_tool = Tool(
    name='analyze_enquiry',
    description=(
        'Analyse a new enquiry against NBNE\'s historical decision '
        'memory and return a structured strategy brief with citations. '
        'Use this when the user asks "how should we handle this quote", '
        '"what should I say to this client", "is this a good '
        'opportunity", or pastes in a new enquiry / email / quote '
        'request and wants a recommendation. '
        'The tool runs search_crm + retrieve_similar_decisions + '
        'search_wiki internally to gather evidence, then uses Claude '
        'Sonnet to synthesise a markdown brief covering archetype, '
        'game-theoretic framing (parties, objectives, BATNAs), risks, '
        'concrete actions with citations, and a confidence stamp. '
        'Arguments: enquiry (required, free text — paste the full '
        'enquiry), focus (optional hint, e.g. "pricing" or "dispute"). '
        'Output is a RECOMMENDATION, not a decision — the final '
        'authority stays with the user. Cite decision_ids and wiki '
        'file_paths from the brief if you reference specifics.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_analyze_enquiry,
    required_permission='analyze_enquiry',
)
