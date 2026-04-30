"""Machinery-manual search tool.

Phase C of the manual-ingestion work — companion to scripts/ingest_manuals.py
which populates the claw_code_chunks table with chunk_type='manual'. This
file is the read-side: lets the agent query that namespace from chat.

Why a separate namespace from search_wiki?
  Wiki articles are short, curated, narrative documents — "how Phloe deploys",
  "what we decided about the Mitre QR codes". Machinery manuals are long,
  technical, reference material — pages of part numbers, torque specs,
  schematic call-outs. Mixing them dilutes wiki search ("show me the wiki on
  X" returns chapter 12 of the Hulk's print head replacement procedure)
  and dilutes manual search ("how do I clean the Hulk's belt" returns a
  decision log about a different machine). Two namespaces, two tools,
  scoped retrieval — same pattern as search_wiki / retrieve_similar_decisions.

Machine name: NBNE refers to its machinery by nicknames — "The Hulk",
"The Beast", "Rolf", "Mao". The ingest script encodes the machine name
into chunk_name as ``"<machine> · <filename> · chunk-<n>"`` so the
optional ``machine`` filter on this tool becomes a simple ILIKE.
That avoided a schema migration competing with the running ad-sync
on 2026-04-30 (lock contention is real); if we later need exact-match
filtering we'll add a dedicated column then.
"""
from __future__ import annotations

import os
from typing import Any

from .registry import RiskLevel, Tool


def _connect_db():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url, connect_timeout=5)
    except Exception:
        return None


def _embed_query(query: str) -> list[float] | None:
    """Embed the search query — None if no embedder is available."""
    try:
        from core.wiki.embeddings import get_embed_fn
        fn = get_embed_fn()
        if fn is None:
            return None
        return fn(query)
    except Exception:
        return None


def _search_manuals(
    project_root: str,
    query: str,
    machine: str | None = None,
    limit: int = 5,
    **kwargs,
) -> str:
    """Semantic + lexical search over machinery manuals.

    Mirrors search_wiki (deek_tools.py:_search_wiki) but scoped to
    ``chunk_type='manual'``, with an optional machine-name filter that
    matches the chunk_name prefix the ingest script writes.
    """
    try:
        limit = int(limit)
    except Exception:
        limit = 5
    limit = max(1, min(limit, 20))

    machine_clean = (machine or '').strip()

    conn = _connect_db()
    if conn is None:
        return 'search_manuals: DB unreachable (no DATABASE_URL or conn failed).'

    try:
        try:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
        except Exception:
            pass

        rows_out: list[tuple[float, str, str, str]] = []  # (score, path, name, snippet)
        with conn.cursor() as cur:
            # Build the optional machine filter once. The ingest script
            # writes chunk_name as "{machine} · {file} · chunk-{n}",
            # so an ILIKE prefix match with the machine name is the
            # cleanest scope.
            machine_clause = ''
            machine_args: tuple = ()
            if machine_clean:
                machine_clause = ' AND chunk_name ILIKE %s'
                machine_args = (f'{machine_clean} · %',)  # "<machine> · ..."

            embedding = _embed_query(query)
            if embedding is not None:
                try:
                    cur.execute(
                        f'''SELECT file_path, chunk_name,
                                   LEFT(chunk_content, 600),
                                   embedding <=> %s::vector AS distance
                           FROM claw_code_chunks
                           WHERE project_id = 'deek'
                             AND chunk_type = 'manual'
                             AND embedding IS NOT NULL
                             {machine_clause}
                           ORDER BY embedding <=> %s::vector
                           LIMIT %s''',
                        (embedding, *machine_args, embedding, limit),
                    )
                    for path, name, snippet, dist in cur.fetchall():
                        score = 1.0 - float(dist)
                        rows_out.append((score, path, name or path, snippet))
                except Exception:
                    # Semantic path failed (e.g. embedding column nullable,
                    # vector dim mismatch). Fall through to lexical.
                    pass

            if not rows_out:
                cur.execute(
                    f'''SELECT file_path, chunk_name,
                              LEFT(chunk_content, 600)
                       FROM claw_code_chunks
                       WHERE project_id = 'deek'
                         AND chunk_type = 'manual'
                         AND (chunk_content ILIKE %s OR chunk_name ILIKE %s)
                         {machine_clause}
                       ORDER BY indexed_at DESC
                       LIMIT %s''',
                    (f'%{query}%', f'%{query}%', *machine_args, limit),
                )
                for path, name, snippet in cur.fetchall():
                    rows_out.append((0.5, path, name or path, snippet))

        if not rows_out:
            scope = f' for machine={machine_clean}' if machine_clean else ''
            return (
                f'No manual content found matching {query!r}{scope}. '
                f'Either no manual covers it yet (run scripts/ingest_manuals.py '
                f'on a folder of PDFs to populate) or the search is too narrow.'
            )

        scope_label = f' (machine={machine_clean})' if machine_clean else ''
        lines = [f'Top {len(rows_out)} manual results for {query!r}{scope_label}:', '']
        for score, path, name, snippet in rows_out[:limit]:
            lines.append(f'[{score:.2f}] {name}')
            lines.append(f'  path: {path}')
            compact = ' '.join(snippet.split())
            if len(compact) > 500:
                compact = compact[:500] + '...'
            lines.append(f'  {compact}')
            lines.append('')
        return '\n'.join(lines).rstrip()
    finally:
        try:
            conn.close()
        except Exception:
            pass


search_manuals_tool = Tool(
    name='search_manuals',
    description=(
        'Search the NBNE machinery-manuals corpus — PDFs, photos, and '
        'maintenance records ingested via scripts/ingest_manuals.py. '
        'Use this for ANY question about a specific machine: how to '
        'operate it, clean it, troubleshoot it, replace a part, find '
        'a torque spec, look up a part number. NBNE machines have '
        'nicknames (e.g. "The Hulk", "The Beast", "Rolf", "Mao") — '
        'pass the nickname as the `machine` argument to scope the '
        'search to that one machine\'s docs. Without it, searches '
        'across all manuals.\n\n'
        'Arguments: query (free text — what you\'re trying to find), '
        'machine (optional — nickname like "Hulk" or "Beast"), '
        'limit (default 5, max 20).\n\n'
        'Lives in chunk_type=\'manual\' rows of claw_code_chunks, '
        'separate namespace from search_wiki — wiki is for SOPs and '
        'decisions, this is for equipment reference material.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_search_manuals,
    required_permission='search_manuals',
)


__all__ = ['search_manuals_tool']
