"""
Chat-facing tool for the cairn_intel counterfactual memory module.

Exposes ``retrieve_similar_decisions`` — a SAFE, read-only tool that
answers "have we been here before?" questions by embedding the user's
query and returning the top structurally similar past decisions along
with their outcomes and lessons.
"""
from __future__ import annotations

import os
from typing import Any

from .registry import Tool, RiskLevel


# ── Internal helpers ────────────────────────────────────────────────────


def _format_result(result: dict) -> list[str]:
    """Render a single decision row as human-readable lines for the chat."""
    lines: list[str] = []
    sim = result.get('similarity')
    source_type = result.get('source_type', 'unknown')
    decided_at = result.get('decided_at') or 'unknown date'
    tags = ', '.join(result.get('archetype_tags') or []) or '(no tags)'
    sim_txt = f"{sim:.2f}" if isinstance(sim, (int, float)) else 'n/a'

    lines.append(
        f"[{sim_txt}] {source_type} — {decided_at} — tags: {tags}"
    )
    lines.append(f"  id: {result.get('decision_id')}")
    summary = (result.get('context_summary') or '').strip()
    if len(summary) > 400:
        summary = summary[:400] + '...'
    if summary:
        lines.append(f"  context: {summary}")

    chosen = (result.get('chosen_path') or '').strip()
    if len(chosen) > 300:
        chosen = chosen[:300] + '...'
    if chosen:
        lines.append(f"  chose: {chosen}")

    rejected = result.get('rejected_paths') or []
    if isinstance(rejected, list) and rejected:
        for rp in rejected[:3]:
            path = (rp.get('path') or '').strip() if isinstance(rp, dict) else str(rp)
            reason = rp.get('reason', '') if isinstance(rp, dict) else ''
            if path:
                if reason:
                    lines.append(f"  rejected: {path} — {reason}")
                else:
                    lines.append(f"  rejected: {path}")

    outcome = result.get('outcome')
    if outcome:
        result_txt = (outcome.get('actual_result') or '').strip()
        if len(result_txt) > 300:
            result_txt = result_txt[:300] + '...'
        if result_txt:
            lines.append(f"  outcome: {result_txt}")
        lesson = (outcome.get('lesson') or '').strip()
        if lesson:
            if len(lesson) > 400:
                lesson = lesson[:400] + '...'
            model = outcome.get('lesson_model') or 'llm'
            lines.append(f"  lesson ({model}): {lesson}")

    lines.append('')
    return lines


def _retrieve_similar_decisions(
    project_root: str,
    query: str,
    limit: int = 5,
    sources: list[str] | None = None,
    **kwargs,
) -> str:
    """Tool entry point called by the chat agent loop."""
    try:
        limit = int(limit)
    except Exception:
        limit = 5
    limit = max(1, min(limit, 10))

    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return 'Counterfactual memory unavailable: DATABASE_URL not set'

    try:
        from core.wiki.embeddings import get_embed_fn
        embed_fn = get_embed_fn()
    except Exception as exc:
        return f'Counterfactual memory unavailable: embedder error ({exc})'
    if embed_fn is None:
        return (
            'Counterfactual memory unavailable: no embedding provider. '
            'Start Ollama with nomic-embed-text, or set OPENAI_API_KEY.'
        )

    try:
        from core.intel.memory import CounterfactualMemory
        memory = CounterfactualMemory(db_url=db_url, embed_fn=embed_fn)
        results = memory.retrieve_similar(
            query=query,
            top_k=limit,
            include_sources=sources,
        )
    except Exception as exc:
        return f'Counterfactual memory error: {exc}'

    if not results:
        return (
            f"No structurally similar past decisions found for: {query}\n\n"
            "The counterfactual memory module may not yet be seeded with "
            "historical data — the backfill importer populates it from "
            "disputes, b2b quotes, emails, manufacture, xero and amazon."
        )

    lines = [
        f"Top {len(results)} similar past decisions for: {query}",
        '',
    ]
    for result in results:
        lines.extend(_format_result(result))

    # Related wiki cross-links — every retrieve_similar call now
    # carries the top N wiki articles by the same query embedding.
    # Emit them once at the end so the chat agent doesn't have to
    # hop through search_wiki + read_file separately.
    related_wiki: list[dict] = []
    if results:
        related_wiki = results[0].get('related_wiki') or []
    if related_wiki:
        lines.append('')
        lines.append(f'Related wiki articles (by same query embedding):')
        for w in related_wiki:
            sim = w.get('similarity')
            sim_txt = f"{sim:.3f}" if isinstance(sim, (int, float)) else 'n/a'
            lines.append(
                f"  [{sim_txt}] {w.get('title') or w.get('file_path')}"
            )
            lines.append(f"    path: {w.get('file_path')}")
            excerpt = (w.get('excerpt') or '').strip()
            if excerpt:
                compact = ' '.join(excerpt.split())
                if len(compact) > 220:
                    compact = compact[:220] + '...'
                lines.append(f'    {compact}')
            lines.append('')

    return '\n'.join(lines).rstrip()


# ── Tool registration object ────────────────────────────────────────────


retrieve_similar_decisions_tool = Tool(
    name='retrieve_similar_decisions',
    description=(
        'Retrieve past decisions structurally similar to a new situation. '
        'Use this when the user asks "have we been here before", "what '
        'did we decide last time", or when any question concerns pricing, '
        'disputes, quotes, production scheduling, or client relationships '
        'where past outcomes are relevant. Runs a cosine-similarity '
        'search over the cairn_intel.decisions embedding column, joined '
        'with the latest outcome and lesson per decision. '
        'Arguments: query (free text describing the new situation), '
        'limit (default 5, max 10), sources (optional list to filter — '
        'dispute, b2b_quote, email, m_number, xero, amazon, principle). '
        'Returns ranked decisions with context, chosen path, rejected '
        'alternatives, actual outcome, and the extracted lesson.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_retrieve_similar_decisions,
    required_permission='retrieve_similar_decisions',
)
