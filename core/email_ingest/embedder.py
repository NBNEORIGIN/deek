"""
Embedding pipeline for stored emails.

Reads from cairn_email_raw (is_embedded=FALSE, skip_reason IS NULL),
chunks each email body into 500-word windows, embeds each chunk using
the Cairn CodeIndexer embed() method, and writes to claw_code_chunks.

Key design decisions:
    - One DB connection per call; all chunk inserts for a single email
      share that connection and commit together (not per-chunk).
    - Deduplication via content_hash + WHERE NOT EXISTS.
    - Uses file_path='email/{mailbox}/{email_id}' and chunk_type='email'.
    - subject stored in chunk_name; subproject_id='email'.
"""
import hashlib
import json
import logging
import os
from datetime import datetime

from core.email_ingest.db import get_conn, get_db_url

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 1500
EMBED_PROJECT_ID = 'claw'


def _chunk_email(body_text: str, window_words: int = 500, overlap_words: int = 50) -> list[str]:
    """
    Split email body into word-windowed chunks with overlap.
    Returns list of text chunks.
    """
    if not body_text:
        return []
    words = body_text.split()
    if not words:
        return []
    step = max(1, window_words - overlap_words)
    chunks = []
    for start in range(0, len(words), step):
        chunk = ' '.join(words[start:start + window_words])
        if not chunk.strip():
            continue
        if len(chunk) > MAX_CHUNK_CHARS:
            chunk = chunk[:MAX_CHUNK_CHARS]
        chunks.append(chunk)
    return chunks


def _get_indexer():
    """Instantiate a CodeIndexer for embedding only (path is not used)."""
    from core.context.indexer import CodeIndexer
    return CodeIndexer(
        project_id=EMBED_PROJECT_ID,
        codebase_path=os.getenv('CLAW_DATA_DIR', 'D:/claw'),
        db_url=get_db_url(),
    )


def embed_email_batch(batch_size: int = 50) -> dict:
    """
    Embed up to batch_size stored emails into claw_code_chunks.
    Returns summary: {embedded, errors, chunks_written}.

    Safe to call repeatedly — is_embedded flag prevents double-embedding.
    """
    indexer = _get_indexer()
    embedded = 0
    errors = 0
    chunks_written = 0

    with get_conn() as conn:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, mailbox, sender, subject, body_text, received_at
                FROM cairn_email_raw
                WHERE is_embedded = FALSE AND skip_reason IS NULL
                ORDER BY received_at DESC
                LIMIT %s
                """,
                (batch_size,),
            )
            emails = cur.fetchall()

        logger.info('embed_email_batch: %d emails to process', len(emails))

        for row in emails:
            email_id, mailbox, sender, subject, body_text, received_at = row
            chunks = _chunk_email(body_text or '')

            if not chunks:
                # Mark as embedded (no content to embed)
                with conn.cursor() as cur:
                    cur.execute(
                        'UPDATE cairn_email_raw SET is_embedded=TRUE WHERE id=%s',
                        (email_id,),
                    )
                    conn.commit()
                embedded += 1
                continue

            try:
                date_str = received_at.date().isoformat() if received_at else 'unknown'
                file_path = f'email/{mailbox}/{email_id}'

                with conn.cursor() as cur:
                    for chunk_index, chunk in enumerate(chunks):
                        content = (
                            f'Email from {sender} ({date_str})\n'
                            f'Subject: {subject}\n\n{chunk}'
                        )
                        content_hash = hashlib.sha256(content.encode()).hexdigest()

                        # Skip if this exact chunk is already in the vector store
                        cur.execute(
                            'SELECT 1 FROM claw_code_chunks WHERE content_hash=%s AND project_id=%s',
                            (content_hash, EMBED_PROJECT_ID),
                        )
                        if cur.fetchone():
                            continue

                        try:
                            embedding = indexer.embed(content)
                        except Exception as exc:
                            logger.error(
                                'embed failed for email_id=%d chunk=%d: %s',
                                email_id, chunk_index, exc,
                            )
                            raise

                        cur.execute(
                            """
                            INSERT INTO claw_code_chunks
                                (project_id, file_path, chunk_content, chunk_type,
                                 chunk_name, content_hash, embedding, last_modified,
                                 subproject_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                EMBED_PROJECT_ID,
                                f'{file_path}/{chunk_index}',
                                content,
                                'email',
                                (subject or '')[:200],
                                content_hash,
                                embedding,
                                received_at,
                                mailbox,
                            ),
                        )
                        chunks_written += 1

                    cur.execute(
                        'UPDATE cairn_email_raw SET is_embedded=TRUE WHERE id=%s',
                        (email_id,),
                    )

                conn.commit()
                embedded += 1

                if embedded % 50 == 0:
                    logger.info(
                        'embed_email_batch: %d embedded, %d chunks written so far',
                        embedded, chunks_written,
                    )

            except Exception as exc:
                logger.error('Failed to embed email id=%d: %s', email_id, exc, exc_info=True)
                try:
                    conn.rollback()
                except Exception:
                    pass
                errors += 1

    result = {'embedded': embedded, 'errors': errors, 'chunks_written': chunks_written}
    logger.info('embed_email_batch complete: %s', result)
    return result


def get_embed_status() -> dict:
    """Return current embedding progress counts."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_embedded = TRUE AND skip_reason IS NULL) AS embedded,
                    COUNT(*) FILTER (WHERE is_embedded = FALSE AND skip_reason IS NULL) AS pending,
                    COUNT(*) FILTER (WHERE skip_reason IS NOT NULL)                    AS skipped,
                    COUNT(*)                                                            AS total
                FROM cairn_email_raw
                """
            )
            embedded, pending, skipped, total = cur.fetchone()

            cur.execute(
                "SELECT COUNT(*) FROM claw_code_chunks WHERE project_id=%s AND chunk_type='email'",
                (EMBED_PROJECT_ID,),
            )
            chunk_count = cur.fetchone()[0]

    return {
        'embedded': embedded,
        'pending': pending,
        'skipped': skipped,
        'total': total,
        'vector_chunks': chunk_count,
    }
