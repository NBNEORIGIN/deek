"""Wiki-write agent tools.

Closes the gap Toby flagged 2026-04-24: Deek had `search_wiki`
(read) but no `write_wiki` — when Toby asked Deek to "write this
to the wiki", Deek had to either silently fail or persist via
the wrong tool (write_crm_memory).

Two write surfaces exist for long-form Deek-authored knowledge:

1. **Drafts** (`data/wiki-drafts/<slug>.md`) — written here.
   Persisted to the volume-mounted data dir so they survive
   container rebuilds. Indexed into ``claw_code_chunks`` with
   ``chunk_type='wiki'`` so they're immediately retrievable via
   ``search_wiki``. NOT in git.

2. **Canonical wiki** (`wiki/modules/<slug>.md`) — git-tracked,
   reviewed, committed via PR. Toby promotes drafts here when
   he's satisfied. Same retrieval path; just durable across
   DB restores.

This tool writes drafts. Promotion to canonical is a manual
step (deliberate — Deek shouldn't auto-edit the curated corpus).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

from .registry import RiskLevel, Tool


logger = logging.getLogger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = Path(os.getenv('DEEK_DATA_DIR') or (_REPO_ROOT / 'data'))
_DRAFTS_DIR = _DATA_DIR / 'wiki-drafts'

MAX_CONTENT_CHARS = 60000


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '-', text or '').strip('-').lower()
    return (s[:max_len].rstrip('-')) or 'untitled'


def _connect_db():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return None
    try:
        return psycopg2.connect(db_url, connect_timeout=5)
    except Exception:
        return None


def _embed_into_chunks(
    *, file_path: str, content: str, chunk_name: str,
) -> tuple[bool, str]:
    """Generate embedding + upsert into claw_code_chunks. Returns
    (ok, detail). Failures are non-fatal — the file write is the
    primary persistence; embedding is the searchability bonus."""
    conn = _connect_db()
    if conn is None:
        return False, 'no DATABASE_URL'
    try:
        from core.wiki.embeddings import get_embed_fn
        embed_fn = get_embed_fn()
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        return False, f'embed_fn import: {exc.__class__.__name__}'

    if not embed_fn:
        try:
            conn.close()
        except Exception:
            pass
        return False, 'no embedding model configured'

    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

    try:
        embedding = embed_fn(content[:6000])
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        return False, f'embed call: {exc.__class__.__name__}'

    try:
        try:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
        except Exception:
            pass
        with conn.cursor() as cur:
            # Upsert: delete-then-insert keeps existing chunk-write
            # patterns simple
            cur.execute(
                """DELETE FROM claw_code_chunks
                    WHERE project_id = 'deek'
                      AND file_path = %s
                      AND chunk_type = 'wiki'""",
                (file_path,),
            )
            cur.execute(
                """INSERT INTO claw_code_chunks
                    (project_id, file_path, chunk_content, chunk_type,
                     chunk_name, content_hash, embedding, indexed_at,
                     salience, salience_signals, last_accessed_at,
                     access_count)
                   VALUES ('deek', %s, %s, 'wiki', %s, %s, %s::vector,
                           NOW(), 5.0,
                           '{"toby_flag": 0.5, "via": "write_wiki_tool"}'::jsonb,
                           NOW(), 0)""",
                (file_path, content, chunk_name, content_hash, embedding),
            )
            conn.commit()
        return True, 'embedded'
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f'db write: {exc.__class__.__name__}'
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _write_wiki_draft(
    project_root: str,
    title: str,
    content: str,
    tags: list[str] | str | None = None,
    **kwargs,
) -> str:
    """Write a Deek-drafted wiki article to the persistent drafts
    directory + index it into claw_code_chunks for immediate
    search_wiki retrieval.

    Files land at ``data/wiki-drafts/<slug>.md`` (volume-mounted
    on Hetzner) — survives container rebuilds. Toby promotes
    selected drafts to ``wiki/modules/`` via a manual git PR.
    """
    title = (title or '').strip()
    content = (content or '').strip()
    if not title:
        return "write_wiki error: 'title' is required."
    if not content:
        return "write_wiki error: 'content' is required."
    if len(content) > MAX_CONTENT_CHARS:
        return (
            f"write_wiki error: content {len(content)} chars exceeds "
            f"max {MAX_CONTENT_CHARS}; trim or split into multiple "
            'articles.'
        )

    # Normalise tags
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(',') if t.strip()]
    elif isinstance(tags, list):
        tag_list = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tag_list = []

    slug = _slugify(title)
    target = _DRAFTS_DIR / f'{slug}.md'
    rel_path = f'data/wiki-drafts/{slug}.md'

    # Build the article body. If the user didn't include a top-level
    # heading, prepend one — that matches the convention used by
    # human-authored wiki articles (the embedding code reads `# X`
    # as the chunk_name).
    body_lines: list[str] = []
    if not content.lstrip().startswith('# '):
        body_lines.append(f'# {title}')
        body_lines.append('')
    body_lines.append(content)
    if tag_list:
        body_lines.append('')
        body_lines.append(f'_tags: {", ".join(tag_list)}_')
    body_lines.append('')
    body_lines.append('---')
    body_lines.append(f'_drafted by Deek via write_wiki tool_')
    full_body = '\n'.join(body_lines)

    # File write
    try:
        _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        # If a file already exists with the same slug, suffix with -2, -3, ...
        if target.exists():
            existing = target.read_text(encoding='utf-8')
            if existing.strip() == full_body.strip():
                return (
                    f'write_wiki: identical draft already exists at '
                    f'{rel_path} — no-op.'
                )
            n = 2
            while True:
                candidate = _DRAFTS_DIR / f'{slug}-{n}.md'
                if not candidate.exists():
                    target = candidate
                    rel_path = f'data/wiki-drafts/{slug}-{n}.md'
                    break
                n += 1
        target.write_text(full_body, encoding='utf-8')
    except Exception as exc:
        return (
            f'write_wiki error: file write failed: '
            f'{exc.__class__.__name__}: {exc}'
        )

    # Embed + index. Failure here means the file exists but isn't
    # search_wiki-discoverable yet — caller can retry or manually
    # run /admin/wiki-sync.
    embed_ok, embed_detail = _embed_into_chunks(
        file_path=rel_path, content=full_body, chunk_name=title,
    )
    embed_summary = (
        'indexed for search_wiki' if embed_ok
        else f'wrote file but indexing failed ({embed_detail}) — '
             'retry via POST /admin/wiki-sync or re-run write_wiki'
    )

    return (
        f'Wrote wiki draft to `{rel_path}`'
        f' (title: "{title}", {len(full_body)} chars'
        + (f', tags: {tag_list}' if tag_list else '')
        + f'). {embed_summary}.\n\n'
        'NOTE: this is a DRAFT — persisted to the data volume + '
        'searchable via search_wiki, but NOT yet in the git-tracked '
        'wiki/modules/ corpus. Toby promotes drafts to canonical '
        'via a manual git PR when ready.'
    )


write_wiki_tool = Tool(
    name='write_wiki',
    description=(
        'Write a long-form Deek-authored wiki article. Use this '
        'when the user asks you to "remember this", "write this '
        'to the wiki", "document this for next time", or when '
        "you've reasoned through a process / decision / lesson "
        'that future sessions will want to retrieve. The draft '
        'lands at data/wiki-drafts/<slug>.md (persistent volume) '
        'and is immediately indexed into claw_code_chunks for '
        'retrieval via search_wiki. Toby promotes drafts to the '
        'canonical wiki/modules/ corpus via a manual git PR when '
        "they're worth keeping. Arguments: title (required, becomes "
        'the article heading + filename slug), content (required, '
        'the markdown body — can include headings, lists, code '
        'blocks; no top-level # required, will be added if absent), '
        'tags (optional list or comma-separated string). Idempotent '
        'on identical content; suffix-disambiguated on title '
        'collision.'
    ),
    risk_level=RiskLevel.SAFE,
    fn=_write_wiki_draft,
    required_permission='write_wiki',
)


__all__ = ['write_wiki_tool', '_write_wiki_draft']
