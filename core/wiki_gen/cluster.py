"""
Seed-topic cluster retrieval and wiki article generation from bulk email corpus.

For each seed topic:
  1. retrieve_email_chunks_for_topic() — pgvector cosine search on email chunks
  2. deduplicate_chunks()              — deprioritise heavily-used email IDs
  3. Call Claude Sonnet with cluster prompt
  4. quality_check() two-tier gate
  5. write_wiki_article() if passed
  6. log_generation()
"""
import logging
import time

from pgvector.psycopg2 import register_vector

from core.wiki_gen.db import get_conn
from core.wiki_gen.generator import (
    get_embedding,
    call_claude,
    classify_module,
    quality_check,
    write_wiki_article,
    log_generation,
)

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.55   # raised from 0.45 — stronger semantic match required
MIN_QUALIFYING_CHUNKS = 5      # minimum chunks above threshold to proceed

SEED_TOPICS = [
    # Pricing and commercial
    'fascia sign pricing quote estimate',
    'channel letters pricing aluminium',
    'post-mounted sign pricing',
    'vinyl wrap pricing',
    'hourly rate labour cost',

    # Fabrication how-tos
    'how to make fascia sign installation',
    'how to install post sign ground anchor',
    'aluminium composite panel cutting routing',
    'powder coating paint finish preparation',
    'LED illuminated sign wiring installation',

    # Machines and equipment
    'Mimaki printer setup maintenance',
    'Mutoh printer white ink',
    'Roland cutter plotter settings',
    'ROLF machine operation',
    'laminator settings media',

    # Client and project management
    'planning permission sign council',
    'sign survey site visit measurements',
    'proof approval client sign off',
    'installation day checklist',
    'snagging punch list',

    # Lessons learnt and common problems
    'sign fading weather damage warranty',
    'colour match pantone vinyl',
    'substrate warping buckling',
    'adhesion failure substrate prep',
    'common mistakes errors',

    # Supplier and procurement
    'aluminium composite panel supplier',
    'vinyl media supplier order',
    'LED module strip supplier',
    'acrylic sheet supplier',
    'fixings anchors supplier',
]

_CLUSTER_WIKI_PROMPT = """You are writing a wiki article for NBNE's internal Cairn knowledge base.
NBNE is a sign fabrication and print company in Alnwick, Northumberland.

The following are excerpts from real NBNE business emails related to the topic: "{topic}"

Email excerpts:
{chunks}

Write a practical wiki article that:
- Synthesises the key knowledge from these emails
- Focuses on NBNE-specific practices, pricing, suppliers, and lessons
- Is written as authoritative internal guidance, not generic advice
- Includes specific figures, product names, and supplier names where they appear in the source emails
- Flags any contradictions or outdated information found in the emails

Format: Markdown. Maximum 1000 words. Article title should be descriptive and specific
(e.g. "Fascia Sign Pricing — NBNE Standard Rates and Calculation Method").
No preamble. Start with the title as an H1.
"""


def retrieve_email_chunks_for_topic(topic: str, limit: int = 20) -> list[dict]:
    """
    Retrieve top N email chunks most similar to the topic.
    Returns empty list if fewer than MIN_QUALIFYING_CHUNKS meet SIMILARITY_THRESHOLD
    — caller should skip article generation for this topic.
    """
    topic_embedding = get_embedding(topic)

    with get_conn() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    chunk_content,
                    file_path,
                    subproject_id,
                    1 - (embedding <=> %s::vector) AS similarity
                FROM claw_code_chunks
                WHERE project_id = 'claw'
                  AND chunk_type = 'email'
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (topic_embedding, topic_embedding, limit),
            )
            rows = cur.fetchall()

    chunks = []
    for row in rows:
        chunk_id, chunk_content, file_path, subproject_id, similarity = row
        # email_id is encoded in file_path: email/{mailbox}/{email_id}/{chunk_index}
        email_id = None
        parts = (file_path or '').split('/')
        if len(parts) >= 3 and parts[0] == 'email':
            try:
                email_id = int(parts[2])
            except (ValueError, IndexError):
                pass
        chunks.append({
            'id': chunk_id,
            'content': chunk_content,
            'file_path': file_path,
            'similarity': float(similarity),
            'email_id': email_id,
        })

    qualifying = [c for c in chunks if c['similarity'] >= SIMILARITY_THRESHOLD]
    if len(qualifying) < MIN_QUALIFYING_CHUNKS:
        logger.info(
            'Topic "%s": only %d chunks above threshold (%.2f) — skipping',
            topic, len(qualifying), SIMILARITY_THRESHOLD,
        )
        return []

    return chunks


def deduplicate_chunks(chunks: list[dict], used_ids: set[int]) -> list[dict]:
    """
    Deprioritise chunks from emails already used as primary sources.
    Returns up to 15 fresh + 5 previously-used chunks (max 20).
    """
    primary = [c for c in chunks if c['email_id'] not in used_ids]
    secondary = [c for c in chunks if c['email_id'] in used_ids]
    return (primary[:15] + secondary[:5])[:20]


def _format_chunks_for_prompt(chunks: list[dict]) -> str:
    """Format chunk list into a numbered context block for the prompt."""
    lines = []
    for i, chunk in enumerate(chunks, start=1):
        lines.append(f'--- Excerpt {i} (similarity: {chunk["similarity"]:.2f}) ---')
        lines.append(chunk['content'].strip())
        lines.append('')
    return '\n'.join(lines)


def _get_completed_topics() -> set[str]:
    """
    Return topics that already have a passing wiki article in the generation log.
    These are skipped in run_cluster_generation() to avoid re-running every 20 min.
    Topics with failed or insufficient-data outcomes are eligible for retry.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT topic
                FROM cairn_wiki_generation_log
                WHERE source_type = 'cluster'
                  AND quality_passed = TRUE
                """
            )
            return {row[0] for row in cur.fetchall()}


def run_cluster_generation(
    topics: list[str] | None = None,
    sleep_between: float = 1.0,
    force: bool = False,
) -> dict:
    """
    Run wiki article generation for all seed topics (or a subset).
    Returns a summary of results.

    Topics that already have a passing article in cairn_wiki_generation_log
    are skipped unless force=True. This prevents the scheduled task from
    re-processing all 35 topics every 20 minutes.
    """
    if topics is None:
        topics = SEED_TOPICS

    # Already-processed gate: skip topics with a passing article
    completed = set() if force else _get_completed_topics()
    if completed:
        logger.info(
            'Skipping %d already-completed topics (pass force=True to override)',
            len(completed & set(topics)),
        )

    used_email_ids: set[int] = set()
    results = {
        'topics_attempted': 0,
        'topics_skipped_completed': len(completed & set(topics)),
        'topics_skipped_no_data': 0,
        'topics_skipped_spam': 0,
        'articles_generated': 0,
        'articles_failed_quality': 0,
        'total_tokens': 0,
        'articles': [],
    }

    for topic in topics:
        # Skip topics already done
        if topic in completed:
            continue

        results['topics_attempted'] += 1
        logger.info('Cluster generation: "%s"', topic)

        # Retrieve relevant email chunks
        chunks = retrieve_email_chunks_for_topic(topic)
        if not chunks:
            results['topics_skipped_no_data'] += 1
            log_generation(
                source_type='cluster',
                topic=topic,
                source_email_ids=[],
                article_title='',
                wiki_filename=None,
                quality_passed=False,
                quality_reason='insufficient_data',
                chunk_count=0,
                tokens_used=0,
            )
            continue

        # Deduplicate against already-used email IDs
        chunks = deduplicate_chunks(chunks, used_email_ids)
        source_email_ids = [c['email_id'] for c in chunks if c['email_id']]

        # Generate article
        prompt = _CLUSTER_WIKI_PROMPT.format(
            topic=topic,
            chunks=_format_chunks_for_prompt(chunks),
        )
        try:
            article_text, gen_tokens = call_claude(prompt, max_tokens=2048)
        except Exception as exc:
            logger.error('Generation failed for topic "%s": %s', topic, exc)
            continue

        # Extract title from first H1 line
        lines = article_text.strip().splitlines()
        raw_title = lines[0].lstrip('#').strip() if lines else topic
        article_title = raw_title or topic

        # Fast spam pre-check on title — avoids calling quality_check at all
        from core.wiki_gen.generator import is_spam_article
        spam, spam_reason = is_spam_article(article_title, article_text)
        if spam:
            logger.info(
                'Pre-check spam rejected "%s": %s', article_title, spam_reason
            )
            results['topics_skipped_spam'] += 1
            log_generation(
                source_type='cluster',
                topic=topic,
                source_email_ids=source_email_ids,
                article_title=article_title,
                wiki_filename=None,
                quality_passed=False,
                quality_reason=spam_reason,
                chunk_count=len(chunks),
                tokens_used=gen_tokens,
            )
            continue

        # Quality gate (passes title through so gate doesn't re-extract it)
        passed, reason, qa_tokens = quality_check(article_text, title=article_title)
        total_tokens = gen_tokens + qa_tokens
        results['total_tokens'] += total_tokens

        if not passed:
            logger.warning('Quality gate failed for "%s": %s', article_title, reason)
            results['articles_failed_quality'] += 1
            log_generation(
                source_type='cluster',
                topic=topic,
                source_email_ids=source_email_ids,
                article_title=article_title,
                wiki_filename=None,
                quality_passed=False,
                quality_reason=reason,
                chunk_count=len(chunks),
                tokens_used=total_tokens,
            )
            continue

        # Write to disk + embed
        module = classify_module(article_title, article_text)
        try:
            wiki_path = write_wiki_article(article_title, article_text, module)
            import os
            wiki_filename = os.path.basename(wiki_path)
        except Exception as exc:
            logger.error('write_wiki_article failed for "%s": %s', article_title, exc)
            continue

        # Update used IDs for deduplication in subsequent topics
        used_email_ids.update(eid for eid in source_email_ids if eid)

        log_generation(
            source_type='cluster',
            topic=topic,
            source_email_ids=source_email_ids,
            article_title=article_title,
            wiki_filename=wiki_filename,
            quality_passed=True,
            quality_reason=reason,
            chunk_count=len(chunks),
            tokens_used=total_tokens,
        )

        results['articles_generated'] += 1
        results['articles'].append({'topic': topic, 'title': article_title, 'module': module})
        logger.info('Generated: "%s" [%s] — %d tokens', article_title, module, total_tokens)

        if sleep_between:
            time.sleep(sleep_between)

    logger.info(
        'Cluster generation complete: %d generated, %d failed quality, '
        '%d skipped (no data), %d total tokens',
        results['articles_generated'],
        results['articles_failed_quality'],
        results['topics_skipped_no_data'],
        results['total_tokens'],
    )
    return results
