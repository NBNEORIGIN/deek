"""
CRM chat tool — live hybrid search over the NBNE CRM knowledge layer.

The CRM at ``crm.nbnesigns.co.uk`` maintains its own pgvector HNSW +
tsvector index (``crm_embeddings`` table) covering projects, clients,
materials, quotes, emails and LessonLearned rows. Instead of
duplicating all that data into ``cairn_intel``, this tool proxies a
live hybrid-search HTTP call into the CRM's own ``/api/cairn/search``
endpoint and formats the results for the chat loop.

Auth:
    Server-to-server via a Bearer token matching ``CAIRN_API_KEY`` —
    same token on both sides, enforced by the CRM's middleware.ts.
    See NBNEORIGIN/crm commit 3d052cd.

Config:
    CAIRN_API_KEY    — shared bearer (required; empty disables the tool)
    CRM_BASE_URL     — default https://crm.nbnesigns.co.uk

Source types the tool can filter by (mirrors
``D:/crm/lib/cairn-indexer.ts``):

    project   — Pipeline items (Project Prisma model)
    client    — Contact records (Client + ClientBusiness)
    material  — Stock / COGS (Material)
    kb        — LessonLearned (structured post-mortems)
    quote     — Quote content
    email     — Indexed email subjects + bodies

Matches the shape of the existing ``search_wiki`` / ``search_emails``
tools in cairn_tools.py so the chat loop handles it identically.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from .registry import Tool, RiskLevel


CRM_DEFAULT_BASE_URL = 'https://crm.nbnesigns.co.uk'
CRM_SEARCH_PATH = '/api/cairn/search'
CRM_REQUEST_TIMEOUT = 10.0  # seconds


def _search_crm(
    project_root: str,
    query: str,
    limit: int = 5,
    types: list[str] | str | None = None,
    **kwargs,
) -> str:
    """Tool entry point.

    ``types`` can be a list like ``['kb', 'project']`` or a single
    comma-separated string like ``"kb,project"`` — the CRM endpoint
    accepts a comma-separated ``types=`` query param.
    """
    try:
        limit = int(limit)
    except Exception:
        limit = 5
    limit = max(1, min(limit, 20))

    token = os.getenv('CAIRN_API_KEY', '').strip()
    if not token:
        return (
            'CRM search unavailable: CAIRN_API_KEY is not set. '
            'This tool calls the CRM server-to-server with a shared '
            'token — set CAIRN_API_KEY in the cairn-api env to enable.'
        )

    base_url = os.getenv('CRM_BASE_URL', CRM_DEFAULT_BASE_URL).rstrip('/')

    # Normalise `types` into a CSV string the CRM endpoint expects.
    types_csv: str | None = None
    if isinstance(types, list):
        types_csv = ','.join(t.strip() for t in types if isinstance(t, str) and t.strip())
    elif isinstance(types, str) and types.strip():
        types_csv = types.strip()

    params: dict[str, Any] = {'q': query, 'limit': limit}
    if types_csv:
        params['types'] = types_csv

    try:
        with httpx.Client(timeout=CRM_REQUEST_TIMEOUT) as client:
            response = client.get(
                f'{base_url}{CRM_SEARCH_PATH}',
                params=params,
                headers={'Authorization': f'Bearer {token}'},
            )
    except httpx.TimeoutException:
        return f'CRM search timed out after {CRM_REQUEST_TIMEOUT:.0f}s — CRM may be slow or unreachable.'
    except Exception as exc:
        return f'CRM search failed: {type(exc).__name__}: {exc}'

    if response.status_code == 401:
        return (
            'CRM search unauthorized: the Bearer token in CAIRN_API_KEY '
            'was rejected by the CRM middleware. Check that the token '
            'on this side matches the CAIRN_API_KEY in the CRM container.'
        )
    if response.status_code == 429:
        return 'CRM search rate-limited (429) — retry in a moment.'
    if response.status_code >= 500:
        return f'CRM search: server error {response.status_code} from {base_url}.'
    if response.status_code != 200:
        return f'CRM search: unexpected HTTP {response.status_code} from {base_url}.'

    try:
        data = response.json()
    except Exception as exc:
        return f'CRM search: could not parse response body ({exc})'

    results = data.get('results') or []
    if not results:
        return (
            f'No CRM results for: {query}\n'
            f'Searched source types: {types_csv or "all (project, client, material, kb, quote, email)"}'
        )

    lines = [f'Top {len(results)} CRM results for: {query}', '']
    for result in results:
        score = result.get('score')
        source_type = result.get('source_type', 'unknown')
        content = (result.get('content') or '').strip()
        metadata = result.get('metadata') or {}
        method = result.get('retrieval_method', '')

        score_txt = f'{score:.3f}' if isinstance(score, (int, float)) else 'n/a'
        header = f'[{score_txt}] {source_type}'
        if method:
            header += f' ({method})'
        lines.append(header)

        # Pull a few useful metadata fields to surface alongside the content
        meta_bits: list[str] = []
        for key in ('project_name', 'client', 'stage', 'value', 'title', 'category', 'name', 'subject', 'from'):
            val = metadata.get(key)
            if val not in (None, '', []):
                meta_bits.append(f'{key}: {val}')
        if meta_bits:
            lines.append('  ' + ' | '.join(meta_bits))

        compact = ' '.join(content.split())
        if len(compact) > 350:
            compact = compact[:350] + '...'
        if compact:
            lines.append(f'  {compact}')
        lines.append('')

    return '\n'.join(lines).rstrip()


search_crm_tool = Tool(
    name='search_crm',
    description=(
        'Search the NBNE CRM knowledge base — pipeline projects, client '
        'records, materials / stock, quotes, emails, and structured '
        'LessonLearned post-mortems. Runs a live hybrid pgvector + '
        'tsvector search against the CRM at crm.nbnesigns.co.uk. '
        'Use this whenever the user asks about a specific client, a '
        'project status, a quote history, what materials we use, or '
        '"have we learned this before?" style questions. '
        'Arguments: query (free text), limit (default 5, max 20), '
        "types (optional list/CSV — 'project', 'client', 'material', "
        "'kb' for lessons, 'quote', 'email'). "
        'Results are ranked by Reciprocal Rank Fusion over cosine and '
        'BM25 scores — higher is better. Always fresh; no cache lag. '
        "For 'have we been here before' questions about historical "
        'decisions, prefer retrieve_similar_decisions first (which '
        'pulls from the counterfactual memory spanning disputes, '
        'b2b_quotes, principles, and CRM lessons), then search_crm '
        'for live client/project context.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_search_crm,
    required_permission='search_crm',
)
