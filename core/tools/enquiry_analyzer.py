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

Plus a fixed RATE_CARD block loaded from
``wiki/modules/nbne-rate-card.md`` — always in context so the
analyzer can quote current NBNE rates without having to discover
them through retrieval.

Output is a markdown strategy document with:

  - archetype classification
  - retrieved evidence (with citations to specific decision_ids and
    wiki file paths — no un-grounded claims)
  - game-theoretic framing (parties, objectives, BATNAs, moves)
  - recommended strategy + concrete next-step actions
  - risk flags
  - confidence rating

Output length is CALIBRATED to the job's estimated value:
  - Under £500:  terse brief (300-500 chars, 2-3 actions, suggested
                 copy for message-bearing signage, no tables/games)
  - £500-£5000:  mid brief (800-1200 chars, 3-5 actions, lightweight
                 archetype + precedent analysis)
  - £5000+:      full brief (2500-3500 chars, archetype, game theory,
                 tiered options, precedent chains, risks)

Precedents passed to the synthesis step are annotated with a
value-size match signal so Sonnet can down-weight precedents that
are much bigger or smaller than the current enquiry.

The tool is READ-ONLY and SAFE — it retrieves, reasons, and
recommends. It never writes anywhere. The user retains decision
authority; the tool's opening line explicitly says "recommendation,
not decision".
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .registry import Tool, RiskLevel


log = logging.getLogger(__name__)


CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
ANTHROPIC_SONNET_MODEL_ENV = 'CLAUDE_MODEL'
DEFAULT_SONNET_MODEL = 'claude-sonnet-4-6'

RATE_CARD_FILES = (
    # In-container path (production on Hetzner)
    '/app/wiki/modules/nbne-rate-card.md',
    # Dev-box path (D:/claw) — resolved relative to this file
    str(Path(__file__).parent.parent.parent / 'wiki' / 'modules' / 'nbne-rate-card.md'),
)
RATE_CARD_MAX_CHARS = 6000


# Archetypes that imply message-bearing signage — the prompt will
# mandate a SUGGESTED COPY section for enquiries matching these.
MESSAGE_BEARING_KEYWORDS = (
    'a-board', 'a board', 'pavement sign', 'sandwich board',
    'poster', 'banner', 'event sign', 'fascia', 'shopfront',
    'shop front', 'shop sign', 'window sign', 'menu board',
    'sandwich sign', 'flag', 'wayfinding',
)


# Keyword heuristics for job size classification when no explicit
# £ amount is present in the enquiry. The tool uses these (plus any
# £ amounts found) to decide an initial job size bucket, which
# determines both the max_tokens cap and the prompt template used.
SMALL_JOB_KEYWORDS = (
    'a-board', 'a board', 'pavement sign', 'sandwich board',
    'insert', 'panel', 'print', 'poster', 'banner only',
    'sticker', 'vinyl sign', 'pub sign', 'menu board',
    'no parking', 'reserved', 'one sign', 'single sign',
    'small sign', 'name plate', 'door sign', 'room number',
    'direction sign', 'a-frame',
)

LARGE_JOB_KEYWORDS = (
    'fascia', 'shopfront', 'shop front', 'illuminated',
    'acm', 'dibond', 'built-up letters', 'three-dimensional',
    'installation', 'project', 'full signage', 'package',
    'rebrand', 'new premises', 'multi-site', 'commercial',
    'tender', 'industrial', 'nationwide', 'roll out',
    'large format', 'height', 'at height', 'mewp', 'scaffold',
    'planning permission', 'advertisement consent',
)


_COMMON_GROUNDING_RULES = (
    'GROUNDING RULES (non-negotiable):\n'
    '- GROUND EVERY CLAIM. Cite decision_id for past decisions, '
    'file_path for wiki, and "RATE_CARD" when quoting a rate.\n'
    '- **NEVER invent a rate that is not in the RATE_CARD.** If a '
    'rate is TBC or missing, the action is "ask Toby for a firm '
    'quote on X" — do NOT guess a number.\n'
    '- Prefer precedents marked VALUE_MATCH: same_order. If you must '
    'cite an out-of-band precedent, explicitly label it as "weak '
    'precedent — larger/smaller job shape".\n'
    '- Do not cite a precedent that is more than 2x larger or '
    'smaller than this job unless you flag the shape mismatch in '
    'the citation.\n'
    '- Always include the planning permission / advertisement '
    'consent flag if fascia, shopfront, or conservation area is '
    'mentioned.\n'
    '- Always include the at-height labour surcharge flag if the '
    'install requires work above standing-ladder reach (fascia, '
    'first-floor, overhead, pole-mounted, etc).\n'
    '- For archetype tagging use the closed 8-tag taxonomy only: '
    'adversarial, cooperative, time_pressured, information_asymmetric, '
    'repeated_game, one_shot, pricing, operational.\n'
    '- The document is a RECOMMENDATION. Open with '
    '"**Recommendation, not decision.**" Close with a pointed '
    'question for the operator.\n'
    'Return a markdown document. No code fences. No preamble.'
)


_SMALL_JOB_SYSTEM = (
    'You are a senior business strategist at NBNE, a Northumberland '
    'signage manufacturer. A small quote enquiry has arrived '
    '(estimated job value < £500). Produce a TIGHT brief — no tables, '
    'no game theory, no archetype classification, no long precedent '
    'chains. Clients deserve fast, friendly, priced responses.\n\n'
    'OUTPUT TEMPLATE — use these exact headings and stay within the '
    'character budget:\n\n'
    '**Recommendation, not decision.**\n\n'
    '_Estimated value: £X–£Y — [one-sentence rationale referencing the '
    'RATE_CARD line you used]._\n\n'
    '### Next step\n'
    '_2-3 numbered actions, each one line, citing a rate/precedent._\n\n'
    '### Suggested copy for the client (mandatory for A-boards, '
    'posters, fascia text, wayfinding)\n'
    '_3-5 lines of concrete draft text the operator could paste into '
    'a client email. Format: short lines, ALL CAPS for key phrases, '
    'bullet separators (•) for lists of features. Example shape:_\n'
    '```\n'
    'PUB OPEN TO EVERYONE\n'
    'NO SIGN IN REQUIRED\n'
    'FAMILIES WELCOME • CHILDREN UNTIL 9PM\n'
    'POOL • DARTS • CHEAP DRINKS\n'
    '```\n'
    '(Adapt the shape to what the client said they want to convey. '
    'For non-message signage, replace this section with one sentence '
    'saying "no customer-facing copy required".)\n\n'
    '### Bottom line\n'
    '_One sentence — "quote £X + VAT now" or "ask Y before quoting".'
    '_\n\n'
    'Character budget for the entire brief: 400–700 chars of content '
    '(headings + prose). This is a HARD ceiling. No extra sections, '
    'no flags section, no provenance notes. The provenance footer is '
    'added automatically — do not emit it yourself.\n\n'
    + _COMMON_GROUNDING_RULES
)


_MID_JOB_SYSTEM = (
    'You are a senior business strategist at NBNE, a Northumberland '
    'signage manufacturer. A mid-size quote enquiry has arrived '
    '(estimated £500–£5,000). Produce a concise brief with '
    'full structure but disciplined length.\n\n'
    'OUTPUT TEMPLATE — use these exact headings:\n\n'
    '**Recommendation, not decision.**\n\n'
    '_Estimated value: £X–£Y — [rationale referencing RATE_CARD + '
    'any same_order precedent]._\n\n'
    '### Archetype\n'
    '_One line, 1-3 tags from the closed taxonomy._\n\n'
    '### Recommended actions\n'
    '_3-5 numbered actions, each with a cited source._\n\n'
    '### Risks\n'
    '_2-3 bullet flags, each grounded in a specific precedent or '
    'wiki rule._\n\n'
    '### Suggested copy for the client (mandatory for message-bearing '
    'signage — A-boards, posters, fascia with text, wayfinding)\n'
    '_3-5 lines of concrete draft text in the short-line / ALL CAPS '
    '/ bullet separator format. Example shape:_\n'
    '```\n'
    'HEADLINE MESSAGE IN CAPS\n'
    'Supporting line\n'
    'FEATURE • FEATURE • FEATURE\n'
    '```\n\n'
    '### Strategic posture\n'
    '_One sentence._\n\n'
    '### Bottom line\n'
    '_One sentence with a concrete next step and a pointed question '
    'for the operator._\n\n'
    'Character budget: 800–1500 chars. Firm ceiling. No game theory, '
    'no tiered tables.\n\n'
    + _COMMON_GROUNDING_RULES
)


_LARGE_JOB_SYSTEM = (
    'You are a senior business strategist at NBNE, a Northumberland '
    'signage manufacturer. A large quote enquiry has arrived '
    '(estimated £5,000+ or a material commercial relationship at '
    'stake). Produce a full strategic brief with game-theoretic '
    'framing and precedent analysis.\n\n'
    'OUTPUT TEMPLATE — use these exact headings:\n\n'
    '**Recommendation, not decision.**\n\n'
    '_Estimated value: £X–£Y — [rationale referencing RATE_CARD + '
    'same_order precedents]._\n\n'
    '### Archetype\n'
    '_1-4 tags from the closed taxonomy with one-sentence rationale '
    'each._\n\n'
    '### Game-theoretic framing\n'
    '_Parties and their objectives. NBNE BATNA. Client BATNA. '
    'Expected sequence of moves. Information asymmetry — what NBNE '
    'knows that the client doesn\'t, and vice versa._\n\n'
    '### Tiered quote recommendation\n'
    '_Low / mid / high options with material + labour breakdown, each '
    'line citing RATE_CARD or a same_order precedent._\n\n'
    '### Recommended actions\n'
    '_5 numbered actions, each with a cited source._\n\n'
    '### Risks\n'
    '_3-5 flags with grounded reasoning._\n\n'
    '### Suggested copy for the client (mandatory for message-bearing '
    'signage)\n'
    '_3-5 lines of concrete draft text in the short-line / ALL CAPS '
    '/ bullet separator format._\n\n'
    '### Strategic posture\n'
    '_One paragraph explaining the overall stance._\n\n'
    '### Bottom line\n'
    '_Close with a pointed question — "What\'s your BATNA here?" for '
    'negotiations, "What does your gut say about the client\'s '
    'seriousness?" for ambiguous cases, etc._\n\n'
    'Character budget: 2500–3500 chars. Use the space — this is the '
    'brief that actually warrants the full form.\n\n'
    + _COMMON_GROUNDING_RULES
)


def _classify_job_size(
    enquiry: str,
    explicit_value_low: float | None,
    explicit_value_high: float | None,
) -> str:
    """Return 'small' / 'mid' / 'large' based on enquiry heuristics.

    Priority:
    1. Explicit £ amounts in the enquiry text (most reliable)
    2. Large-job keywords (fascia, illuminated, commercial, etc)
    3. Small-job keywords (pavement sign, insert, poster, etc)
    4. Default to 'mid'
    """
    # Rule 1 — explicit value
    if explicit_value_high is not None and explicit_value_high > 0:
        if explicit_value_high < 500:
            return 'small'
        if explicit_value_high < 5000:
            return 'mid'
        return 'large'

    lowered = (enquiry or '').lower()

    # Rule 2 — large-job keywords bias up first (illuminated fascia
    # beats "one sign" in terms of dominant signal)
    large_hits = sum(1 for kw in LARGE_JOB_KEYWORDS if kw in lowered)
    if large_hits >= 2:
        return 'large'

    # Rule 3 — small-job keywords
    small_hits = sum(1 for kw in SMALL_JOB_KEYWORDS if kw in lowered)
    if small_hits >= 1 and large_hits == 0:
        return 'small'

    if large_hits >= 1:
        return 'large'

    # Rule 4 — default to mid
    return 'mid'


_MAX_TOKENS_BY_SIZE = {
    'small': 600,
    'mid': 1400,
    'large': 3000,
}


_SYSTEM_PROMPT_BY_SIZE = {
    'small': _SMALL_JOB_SYSTEM,
    'mid': _MID_JOB_SYSTEM,
    'large': _LARGE_JOB_SYSTEM,
}


def _load_rate_card() -> str:
    """Read the canonical rate card from disk.

    Tries the in-container path first (/app/wiki/modules/...) and
    falls back to the dev-box path resolved from this file. If
    neither works, returns a short stub so the analyzer still runs
    without the rate card block.
    """
    for path in RATE_CARD_FILES:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if len(content) > RATE_CARD_MAX_CHARS:
                content = content[:RATE_CARD_MAX_CHARS] + '\n\n...[truncated]'
            return content
        except FileNotFoundError:
            continue
        except Exception as exc:
            log.warning('analyze_enquiry: rate card load failed at %s: %s', path, exc)
            continue
    return (
        '# NBNE Rate Card (not found)\n\n'
        '**Labour and graphic design rate**: £40/hour ex VAT.\n\n'
        '_Rate card file not loaded — other rates are unknown. '
        'If you need to quote anything beyond the hourly rate, '
        'the analyzer should flag it as "ask Toby for a firm '
        'quote" rather than guessing._'
    )


def _estimate_enquiry_value(enquiry: str) -> tuple[float | None, float | None]:
    """Extract a very rough value hint from the enquiry text.

    Looks for any £ amount mentioned (budget statements, previous
    quotes, etc). Returns (low, high) estimate if found, else
    (None, None). This is a crude first pass — the synthesis step
    does a better job with full context, but this hint helps the
    retrieve_similar call size-match precedents.
    """
    if not enquiry:
        return None, None
    matches = re.findall(r'£\s?([\d,]+(?:\.\d+)?)', enquiry)
    if not matches:
        return None, None
    numbers: list[float] = []
    for m in matches:
        try:
            numbers.append(float(m.replace(',', '')))
        except ValueError:
            continue
    if not numbers:
        return None, None
    return min(numbers), max(numbers)


def _annotate_decision_context_with_value_match(
    decision_context: str,
    enquiry_value_low: float | None,
    enquiry_value_high: float | None,
) -> str:
    """Post-process the decision context to add VALUE_MATCH markers.

    The CRM reflection records carry crm_metadata.value in their
    raw_source_ref — when Sonnet sees the DECISION_CONTEXT block,
    we want each decision tagged with same_order / bigger_job /
    smaller_job so it can prefer size-matched precedents.

    Since we don't have direct DB access to the raw_source_ref at
    this layer (we have the already-formatted tool output string),
    we look for £ amounts in the formatted text and annotate based
    on proximity to the enquiry's estimated value. Best-effort and
    silently returns the original text if no annotations are needed.
    """
    if enquiry_value_low is None or enquiry_value_high is None:
        return decision_context
    midpoint = (enquiry_value_low + enquiry_value_high) / 2.0
    if midpoint <= 0:
        return decision_context

    # For each line mentioning a £ amount, classify it and append
    # a compact size-match tag.
    lines = decision_context.splitlines()
    annotated: list[str] = []
    for line in lines:
        amounts = re.findall(r'£\s?([\d,]+(?:\.\d+)?)', line)
        if not amounts:
            annotated.append(line)
            continue
        try:
            values = [float(a.replace(',', '')) for a in amounts]
        except ValueError:
            annotated.append(line)
            continue
        if not values:
            annotated.append(line)
            continue
        top = max(values)
        if top <= 0:
            annotated.append(line)
            continue
        ratio = top / midpoint
        if 0.5 <= ratio <= 2.0:
            tag = ' [VALUE_MATCH: same_order]'
        elif ratio > 2.0:
            tag = f' [VALUE_MATCH: bigger_job ({ratio:.1f}x)]'
        else:
            tag = f' [VALUE_MATCH: smaller_job ({ratio:.2f}x)]'
        annotated.append(line + tag)
    return '\n'.join(annotated)


def _is_message_bearing_signage(enquiry: str) -> bool:
    """Heuristic: does the enquiry mention message-bearing signage?"""
    lowered = enquiry.lower()
    return any(kw in lowered for kw in MESSAGE_BEARING_KEYWORDS)


# ── Retrieval wrappers ─────────────────────────────────────────────────


def _search_crm_safe(query: str, limit: int = 5) -> str:
    try:
        from .crm_tools import _search_crm
        return _search_crm(project_root='', query=query, limit=limit)
    except Exception as exc:
        return f'(search_crm unavailable: {exc})'


def _retrieve_similar_safe(query: str, limit: int = 6) -> str:
    try:
        from .intel_tools import _retrieve_similar_decisions
        return _retrieve_similar_decisions(
            project_root='', query=query, limit=limit,
        )
    except Exception as exc:
        return f'(retrieve_similar_decisions unavailable: {exc})'


def _search_wiki_safe(query: str, limit: int = 5) -> str:
    try:
        from .cairn_tools import _search_wiki
        return _search_wiki(project_root='', query=query, limit=limit)
    except Exception as exc:
        return f'(search_wiki unavailable: {exc})'


# ── Tool entry point ───────────────────────────────────────────────────


def _analyze_enquiry(
    project_root: str,
    enquiry: str,
    focus: str | None = None,
    **kwargs,
) -> str:
    if not enquiry or not enquiry.strip():
        return 'analyze_enquiry: enquiry text is required'

    enquiry = enquiry.strip()
    focus_suffix = f' — focus: {focus}' if focus and focus.strip() else ''

    anthropic_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not anthropic_key:
        return (
            'analyze_enquiry: ANTHROPIC_API_KEY is not set — cannot run '
            'the synthesis step. Retrieval is available individually '
            'via search_crm / retrieve_similar_decisions / search_wiki.'
        )

    sonnet_model = os.getenv(ANTHROPIC_SONNET_MODEL_ENV, DEFAULT_SONNET_MODEL)

    # Compose retrieval queries from the enquiry text.
    query_text = (enquiry + focus_suffix)[:500]

    crm_context = _search_crm_safe(query_text, limit=5)
    decision_context_raw = _retrieve_similar_safe(query_text, limit=6)
    wiki_context = _search_wiki_safe(query_text, limit=5)

    # Improvement B — classify job size from the enquiry to pick the
    # right system prompt template AND cap the max_tokens budget.
    # Hard cap enforces brevity at the model level when prompt
    # instructions alone aren't sufficient.
    enquiry_low, enquiry_high = _estimate_enquiry_value(enquiry)
    job_size = _classify_job_size(enquiry, enquiry_low, enquiry_high)
    system_prompt = _SYSTEM_PROMPT_BY_SIZE[job_size]
    max_tokens_budget = _MAX_TOKENS_BY_SIZE[job_size]

    # Improvement D — annotate retrieved decisions with VALUE_MATCH
    # markers so Sonnet can prefer precedents in the same order of
    # magnitude as the current enquiry. Uses the same rough value
    # estimate the classifier derived.
    decision_context = _annotate_decision_context_with_value_match(
        decision_context_raw,
        enquiry_low,
        enquiry_high,
    )

    # Improvement A — always inject the rate card as fixed context.
    rate_card = _load_rate_card()

    # Improvement C — hint to the model that this is message-bearing
    # so it definitely produces a SUGGESTED COPY section. This is
    # belt-and-braces on top of the system prompt instruction.
    message_bearing_hint = ''
    if _is_message_bearing_signage(enquiry):
        message_bearing_hint = (
            '\n\nNOTE: This enquiry is for message-bearing signage '
            '(A-board, pavement sign, poster, fascia, wayfinding, etc). '
            'The SUGGESTED COPY section is MANDATORY — emit 3-5 lines '
            'of concrete draft text using the short-line / ALL CAPS / '
            'bullet separator format shown in the template. Do NOT '
            'replace this with prose design advice.'
        )

    synthesis_input = (
        f'JOB SIZE CLASSIFICATION: {job_size} '
        f'(enquiry hints: '
        f'explicit_value_low={enquiry_low}, '
        f'explicit_value_high={enquiry_high})\n\n'
        f'---\n'
        f'ENQUIRY:\n{enquiry}\n\n'
        f'---\n'
        f'RATE_CARD (authoritative NBNE rates — cite these, never invent numbers):\n'
        f'{rate_card}\n\n'
        f'---\n'
        f'CRM_CONTEXT (live records matching the enquiry):\n{crm_context}\n\n'
        f'---\n'
        f'DECISION_CONTEXT (past decisions + related wiki, from cairn_intel, '
        f'annotated with VALUE_MATCH markers):\n'
        f'{decision_context}\n\n'
        f'---\n'
        f'WIKI_CONTEXT (policy and process articles):\n{wiki_context}\n'
        f'{message_bearing_hint}\n\n'
        f'---\n'
        f'Produce the strategic brief using the OUTPUT TEMPLATE from '
        f'your system prompt. The brief is for a **{job_size}** job — '
        f'stay within the character budget. Do not add sections that '
        f'are not in the template.'
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=anthropic_key)
        resp = client.messages.create(
            model=sonnet_model,
            max_tokens=max_tokens_budget,
            system=system_prompt,
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

    # Provenance footer
    provenance = (
        '\n\n---\n'
        '_Provenance:_\n'
        f'- job_size: {job_size} (max_tokens={max_tokens_budget})\n'
        f'- search_crm: {len(crm_context)} chars\n'
        f'- retrieve_similar_decisions: {len(decision_context)} chars '
        f'(value-annotated)\n'
        f'- search_wiki: {len(wiki_context)} chars\n'
        f'- rate_card: {len(rate_card)} chars '
        f'(from wiki/modules/nbne-rate-card.md)\n'
        f'- synthesis model: {sonnet_model}'
    )

    # Strict-verbatim wrapper. The chat agent consumes this whole
    # string as a tool result and uses it to write its next response.
    # The inline instruction tells the model NOT to re-wrap the brief
    # with commentary, risks footers, or summary sections — all of
    # which were observed in prior runs where the agent felt free to
    # elaborate on the analyzer's output. The sentinel markers make
    # it easy for the model to see where the verbatim content starts
    # and ends.
    return (
        '⚠️ STRICT VERBATIM OUTPUT — DO NOT MODIFY OR APPEND ⚠️\n\n'
        'The strategic brief between the sentinel markers below has '
        'been produced by the analyzer with a size-calibrated '
        'template, rate-card grounding, and precedent filtering. '
        'Return it to the user ESSENTIALLY VERBATIM. Specifically:\n'
        '- Do NOT add a "Risks / Notes" section. The analyzer already '
        'handled risks inline if the job size warranted it.\n'
        '- Do NOT add a summary or "Recommended Next Steps" footer. '
        'The analyzer has a Bottom Line section that already serves '
        'that purpose.\n'
        '- Do NOT re-phrase, re-structure, or re-order the sections.\n'
        '- Do NOT add a closing "good luck!" or similar pleasantry.\n'
        '- You MAY prefix with one short intro line (max 12 words) '
        'like "Here\'s the analyzer brief:" — nothing more.\n'
        '- Emit everything between the sentinel markers exactly as '
        'written, including the Provenance footer.\n\n'
        '<<<ANALYZER_BRIEF_START>>>\n'
        + brief + provenance + '\n'
        '<<<ANALYZER_BRIEF_END>>>'
    )


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
        '\n\n'
        '**FIRE THIS TOOL WHENEVER the user:**\n'
        '- Asks "analyse", "analyze", "assess", "review", "look at", '
        '"what do you think of", "how should we handle", "what should '
        'I do with", "help me respond to", or "break down" an enquiry, '
        'email, quote request, or client message\n'
        '- Pastes anything that looks like a client enquiry (sender + '
        'subject line + body, or a quote request, or a description of '
        'what a client wants) and asks for advice, a recommendation, '
        'a strategy, a response, or a quote\n'
        '- Asks "is this a good opportunity", "should we take this", '
        '"what are the risks", or any pricing / scoping question about '
        'a specific client situation\n'
        '\n'
        'DO NOT try to synthesise your own brief using search_crm + '
        'retrieve_similar_decisions + search_wiki separately when the '
        'user has asked to analyse / assess / review an enquiry — the '
        'analyzer already runs all three retrievals internally AND '
        'loads the rate card AND calibrates output length to job '
        'value AND produces suggested response copy in the correct '
        'format. Your own synthesis will miss the rate card, produce '
        'invented numbers, and skip the suggested copy section.\n'
        '\n'
        'The tool runs search_crm + retrieve_similar_decisions + '
        'search_wiki internally, loads NBNE\'s rate card from '
        'wiki/modules/nbne-rate-card.md, classifies job size '
        '(small/mid/large), picks a size-appropriate brief template, '
        'then uses Claude Sonnet to synthesise a markdown brief with '
        'archetype, concrete actions, suggested response copy, '
        'precedent citations, risks, and a bottom-line. Every claim '
        'grounded in a cited decision_id, wiki file_path, or '
        'RATE_CARD line; the analyzer never invents rates.\n'
        '\n'
        'Arguments: enquiry (required, free text — paste the FULL '
        'enquiry including sender/subject/body if available), focus '
        '(optional hint like "pricing" or "dispute"). Output is a '
        'RECOMMENDATION, not a decision — final authority stays with '
        'the user. Return the analyzer output essentially verbatim '
        'with only a brief one-line intro — do NOT re-write or '
        're-synthesise the brief.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_analyze_enquiry,
    required_permission='analyze_enquiry',
)
