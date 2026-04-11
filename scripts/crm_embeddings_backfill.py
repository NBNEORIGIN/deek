"""
Backfill crm_embeddings in the cairn-db/crm database.

Ports the text extraction logic from D:/crm/lib/cairn-indexer.ts into
Python and drives it from Cairn's infrastructure, so we don't need
a TypeScript runtime on the Hetzner host. Called after the
pg_dump + pg_restore cutover from nbne_crm → cairn-db/crm wiped the
embeddings table.

Usage (inside the Hetzner cairn-api container):

    docker exec deploy-cairn-api-1 \\
        env CRM_DATABASE_URL="postgresql://cairn:PASSWORD@cairn-db:5432/crm" \\
            OPENAI_API_KEY="sk-..." \\
        python /app/scripts/crm_embeddings_backfill.py

Or from the dev box against any cairn-db/crm reachable by psycopg2:

    python scripts/crm_embeddings_backfill.py

Environment variables:
    CRM_DATABASE_URL   Required. Points at cairn-db/crm.
    OPENAI_API_KEY     Required. Passed to openai client.
    CRM_BACKFILL_BATCH Optional. Embeddings per OpenAI request. Default 50.
    CRM_BACKFILL_LIMIT Optional. Max rows per source_type. Default None
                       (all rows). Use --limit for faster smoke tests.

Idempotent — upserts on (source_type, source_id). Safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json


# ── Text extractors (ports of lib/cairn-indexer.ts) ────────────────────


def extract_project_text(row: dict) -> str:
    """Mirror of extractProjectText in lib/cairn-indexer.ts."""
    parts = [
        row.get('name'),
        f"Client: {row['clientName']}" if row.get('clientName') else None,
        f"Status: {row['status']}" if row.get('status') else None,
        f"Value: £{row['estimatedValue']}" if row.get('estimatedValue') is not None else None,
        row.get('notes'),
        row.get('keyInfo'),
    ]
    return '. '.join(p for p in parts if p)


def extract_client_text(row: dict) -> str:
    """Mirror of extractClientText."""
    name = row.get('businessName') or row.get('clientName') or row.get('name') or ''
    parts = [
        name,
        f"Email: {row['email']}" if row.get('email') else None,
        f"Phone: {row['phone']}" if row.get('phone') else None,
        f"Address: {row['address']}" if row.get('address') else None,
        (
            f"Project types: {', '.join(row['projectTypes'])}"
            if row.get('projectTypes') else None
        ),
        row.get('notes'),
    ]
    return '. '.join(p for p in parts if p)


def extract_material_text(row: dict) -> str:
    """Mirror of extractMaterialText (no supplier join for simplicity)."""
    parts = [
        row.get('name'),
        f"Category: {row['category']}" if row.get('category') else None,
        (
            f"Cost: £{row['unitCost']}/{row['unit']}"
            if row.get('unitCost') is not None and row.get('unit') else None
        ),
    ]
    return '. '.join(p for p in parts if p)


def extract_lesson_text(row: dict) -> str:
    """Mirror of extractLessonText for LessonLearned rows."""
    parts = [
        row.get('title'),
        f"Category: {row['category']}" if row.get('category') else None,
        row.get('description'),
        f"What went wrong: {row['whatWentWrong']}" if row.get('whatWentWrong') else None,
        f"Root cause: {row['rootCause']}" if row.get('rootCause') else None,
        f"Fix: {row['correctiveAction']}" if row.get('correctiveAction') else None,
        f"Rule: {row['preventativeRule']}" if row.get('preventativeRule') else None,
    ]
    return '. '.join(p for p in parts if p)


# ── Source definitions — one entry per source_type we backfill ─────────


SOURCES: list[dict] = [
    {
        'source_type': 'project',
        'table': '"Project"',
        'columns': 'id, name, "clientName", status, "estimatedValue", notes, "keyInfo"',
        'where': 'archived = FALSE',
        'extract': extract_project_text,
        'metadata': lambda row: {
            'project_name': row.get('name'),
            'client': row.get('clientName'),
            'stage': row.get('status'),
            'value': (
                float(row['estimatedValue'])
                if row.get('estimatedValue') is not None else None
            ),
        },
    },
    {
        'source_type': 'client',
        'table': '"Client"',
        'columns': 'id, "clientName", "businessName", email, phone, address, "projectTypes", notes',
        'where': '1=1',
        'extract': lambda row: extract_client_text({
            'clientName': row.get('clientName'),
            'businessName': row.get('businessName'),
            'email': row.get('email'),
            'phone': row.get('phone'),
            'address': row.get('address'),
            'projectTypes': row.get('projectTypes'),
            'notes': row.get('notes'),
        }),
        'metadata': lambda row: {
            'name': row.get('businessName') or row.get('clientName'),
            'email': row.get('email'),
        },
    },
    {
        'source_type': 'material',
        'table': '"Material"',
        'columns': 'id, name, category, "unitCost", unit',
        'where': '1=1',
        'extract': extract_material_text,
        'metadata': lambda row: {
            'name': row.get('name'),
            'category': row.get('category'),
            'unit_cost': (
                float(row['unitCost'])
                if row.get('unitCost') is not None else None
            ),
        },
    },
    {
        'source_type': 'kb',
        'table': '"LessonLearned"',
        'columns': (
            'id, title, description, category, '
            '"whatWentWrong", "rootCause", "correctiveAction", "preventativeRule"'
        ),
        'where': '1=1',
        'extract': extract_lesson_text,
        'metadata': lambda row: {
            'title': row.get('title'),
            'category': row.get('category'),
        },
    },
]


# ── Embedding client (OpenAI text-embedding-3-small at 768 dims) ───────


def embed_batch(client: Any, texts: list[str]) -> list[list[float]]:
    """Batch-embed via OpenAI text-embedding-3-small at 768 dimensions.

    768 matches the pgvector column shape on crm_embeddings AND the
    rest of the Cairn stack (claw_code_chunks, cairn_intel.decisions).
    """
    # Strip empties — OpenAI rejects empty strings
    cleaned = [t if t.strip() else '(empty)' for t in texts]
    resp = client.embeddings.create(
        model='text-embedding-3-small',
        input=cleaned,
        dimensions=768,
    )
    return [item.embedding for item in resp.data]


def format_vector(vec: list[float]) -> str:
    """psycopg2 + pgvector ::vector cast needs string form '[0.1,0.2,...]'."""
    return '[' + ','.join(f'{x:.6f}' for x in vec) + ']'


# ── Main loop ──────────────────────────────────────────────────────────


def process_source(
    conn,
    openai_client,
    source: dict,
    batch_size: int,
    limit: int | None,
) -> dict:
    """Backfill one source_type into crm_embeddings."""
    table = source['table']
    where = source['where']
    columns = source['columns']
    extract = source['extract']
    metadata_fn = source['metadata']
    source_type = source['source_type']

    limit_clause = f' LIMIT {limit}' if limit else ''
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT {columns} FROM {table} WHERE {where} ORDER BY id{limit_clause}'
        )
        rows = cur.fetchall()
        col_names = [desc[0] for desc in cur.description]

    total = len(rows)
    written = 0
    skipped = 0
    print(f'[{source_type}] {total} candidate rows')

    for batch_start in range(0, total, batch_size):
        batch_rows = rows[batch_start: batch_start + batch_size]
        dicts = [dict(zip(col_names, row)) for row in batch_rows]

        # Extract text for each row; skip empty-content rows entirely
        texts: list[str] = []
        valid_dicts: list[dict] = []
        for d in dicts:
            text = extract(d).strip()
            if not text:
                skipped += 1
                continue
            texts.append(text)
            valid_dicts.append(d)

        if not texts:
            continue

        embeddings = embed_batch(openai_client, texts)

        with conn.cursor() as cur:
            for d, text, vec in zip(valid_dicts, texts, embeddings):
                cur.execute(
                    """
                    INSERT INTO crm_embeddings
                        (source_type, source_id, content, embedding, metadata, updated_at)
                    VALUES (%s, %s, %s, %s::vector, %s, NOW())
                    ON CONFLICT (source_type, source_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    """,
                    (
                        source_type,
                        d['id'],
                        text,
                        format_vector(vec),
                        Json(metadata_fn(d)),
                    ),
                )
            conn.commit()
        written += len(texts)
        print(
            f'  [{source_type}] batch {batch_start // batch_size + 1} — '
            f'+{len(texts)} written (running total {written}/{total})'
        )

    return {'total': total, 'written': written, 'skipped': skipped}


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Backfill crm_embeddings for the Cairn CRM hybrid search layer',
    )
    parser.add_argument(
        '--source',
        choices=[s['source_type'] for s in SOURCES] + ['all'],
        default='all',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Max rows per source (smoke testing)',
    )
    parser.add_argument(
        '--batch',
        type=int,
        default=int(os.getenv('CRM_BACKFILL_BATCH', '50')),
    )
    args = parser.parse_args()

    dsn = os.getenv('CRM_DATABASE_URL', '')
    if not dsn:
        print('CRM_DATABASE_URL is not set', file=sys.stderr)
        return 1

    openai_key = os.getenv('OPENAI_API_KEY', '')
    if not openai_key:
        print('OPENAI_API_KEY is not set', file=sys.stderr)
        return 1

    try:
        import openai
    except ImportError:
        print('openai package not installed', file=sys.stderr)
        return 1
    openai_client = openai.OpenAI(api_key=openai_key)

    print(f'[crm-backfill] connecting to {dsn.split("@")[-1]}')
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        total_written = 0
        total_skipped = 0
        start = time.time()
        for source in SOURCES:
            if args.source != 'all' and args.source != source['source_type']:
                continue
            result = process_source(
                conn=conn,
                openai_client=openai_client,
                source=source,
                batch_size=args.batch,
                limit=args.limit,
            )
            total_written += result['written']
            total_skipped += result['skipped']
        elapsed = time.time() - start
        print()
        print(f'[crm-backfill] done in {elapsed:.1f}s — '
              f'written={total_written} skipped={total_skipped}')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
