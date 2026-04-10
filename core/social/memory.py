"""
Cairn Social memory write-back.

When Jo marks a draft variant as published, this module writes the post into
Cairn's memory layer so it becomes searchable via the "Ask" interface and
becomes available as a few-shot example for future drafts.

Per CAIRN_SOCIAL_V2_HANDOFF.md Blocker 2, write-back uses TWO surfaces:

  1. claw_code_chunks (chunk_type='social_post') — semantic-search store,
     embedded via the same pipeline the wiki layer uses
     (core.wiki.embeddings.get_embed_fn). This is what makes the post
     retrievable via the Ask interface.

  2. core.memory.store.MemoryStore.record_decision — chat-history-style
     decision row, equivalent to /memory/write. This is what makes the
     publication visible in the chat history retrieval.

Both writes are best-effort — failure of one does not block the other or the
publish operation itself. The route returns the IDs of whichever wrote
successfully.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

CAIRN_PROJECT = 'claw'
CHUNK_TYPE = 'social_post'


def _build_chunk_content(
    *,
    platform: str,
    pillar: Optional[str],
    post_text: str,
    published_at: datetime,
    published_url: Optional[str],
    source_mode: str,
    brief_or_original: Optional[str],
) -> tuple[str, str]:
    """Build the chunk_content (text body) and chunk_name (title) for the
    claw_code_chunks row. The chunk_content includes the post text plus a
    short header with metadata so semantic search picks up both the content
    and the context.
    """
    header_lines = [
        f"Social post — {platform} ({pillar or 'unspecified pillar'})",
        f"Published: {published_at.isoformat()}",
    ]
    if published_url:
        header_lines.append(f"URL: {published_url}")
    header_lines.append(f"Source mode: {source_mode}")
    if brief_or_original:
        header_lines.append(f"Origin: {brief_or_original[:280]}")
    header = '\n'.join(header_lines)

    chunk_content = f"{header}\n\n---\n\n{post_text.strip()}"

    snippet = ' '.join(post_text.strip().split())[:60]
    chunk_name = f"social/{platform}/{published_at.strftime('%Y-%m-%d')}/{snippet}"
    return chunk_content, chunk_name


def write_published_post_to_chunks(
    *,
    variant_id: int,
    platform: str,
    pillar: Optional[str],
    post_text: str,
    published_at: datetime,
    published_url: Optional[str],
    source_mode: str,
    brief_or_original: Optional[str],
) -> Optional[str]:
    """Insert a published post into claw_code_chunks with chunk_type='social_post'
    and an embedding.

    Returns the synthetic file_path used as the row identifier, or None on
    failure.
    """
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        logger.warning('Cairn Social: DATABASE_URL not set, skipping chunk write-back')
        return None

    try:
        import psycopg2
        from pgvector.psycopg2 import register_vector
    except Exception as exc:
        logger.warning('Cairn Social: pgvector unavailable: %s', exc)
        return None

    # The embedding pipeline lives in core.wiki.embeddings — same one the
    # freshness layer uses (api/main.py:_check_wiki_freshness).
    from core.wiki.embeddings import get_embed_fn
    embed_fn = get_embed_fn()
    if embed_fn is None:
        logger.warning('Cairn Social: no embedding provider available')
        return None

    chunk_content, chunk_name = _build_chunk_content(
        platform=platform,
        pillar=pillar,
        post_text=post_text,
        published_at=published_at,
        published_url=published_url,
        source_mode=source_mode,
        brief_or_original=brief_or_original,
    )

    # Synthetic file_path so freshness checks and lookups work
    file_path = f'social/{platform}/{variant_id}.md'
    content_hash = hashlib.sha256(chunk_content.encode()).hexdigest()

    try:
        embedding = embed_fn(chunk_content[:6000])
    except Exception as exc:
        logger.warning('Cairn Social: embedding failed: %s', exc)
        return None

    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        register_vector(conn)
        with conn.cursor() as cur:
            # Idempotent: if this variant has been re-published, replace.
            cur.execute(
                """
                DELETE FROM claw_code_chunks
                 WHERE project_id = %s AND file_path = %s AND chunk_type = %s
                """,
                (CAIRN_PROJECT, file_path, CHUNK_TYPE),
            )
            cur.execute(
                """
                INSERT INTO claw_code_chunks
                  (project_id, file_path, chunk_content, chunk_type,
                   chunk_name, content_hash, embedding, indexed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector, NOW())
                """,
                (
                    CAIRN_PROJECT,
                    file_path,
                    chunk_content,
                    CHUNK_TYPE,
                    chunk_name,
                    content_hash,
                    embedding,
                ),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning('Cairn Social: claw_code_chunks insert failed: %s', exc)
        return None

    return file_path


def write_published_post_to_decisions(
    *,
    variant_id: int,
    platform: str,
    pillar: Optional[str],
    post_text: str,
    published_url: Optional[str],
    source_mode: str,
) -> Optional[str]:
    """Mirror the publication to the SQLite decisions store via MemoryStore.

    This is equivalent to calling /memory/write — it is what makes the post
    show up in /memory/retrieve and the chat-history view. Returns the
    session_id used, or None on failure.
    """
    try:
        from core.memory.store import MemoryStore
    except Exception as exc:
        logger.warning('Cairn Social: MemoryStore unavailable: %s', exc)
        return None

    data_dir = os.getenv('CLAW_DATA_DIR', './data')
    session_id = f'social_publish_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}'
    store = MemoryStore(CAIRN_PROJECT, data_dir)
    try:
        store.record_decision(
            session_id=session_id,
            decision_type='committed',
            description=(
                f"Published social post on {platform} (pillar: {pillar or 'unspecified'}). "
                f"Variant {variant_id}. "
                f"{('URL: ' + published_url + '. ') if published_url else ''}"
                f"Content: {post_text[:600]}"
            ),
            reasoning=f"source_mode={source_mode}",
            files_affected=[f'social/{platform}/{variant_id}.md'],
            project=CAIRN_PROJECT,
            query=f"social post {platform} {pillar or ''} {post_text[:120]}",
            rejected='',
            model_used='cairn-social',
        )
    except Exception as exc:
        logger.warning('Cairn Social: record_decision failed: %s', exc)
        store.close()
        return None
    finally:
        try:
            store.close()
        except Exception:
            pass

    return session_id
