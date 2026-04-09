"""
Admin API routes — operational endpoints for Cairn instance management.

Mounted at /admin/* in the Cairn FastAPI app.

  POST /admin/wiki-sync   — git pull + embed any new/changed wiki articles
  GET  /admin/wiki-sync   — status of last sync (reads watermark file)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)
router = APIRouter(prefix='/admin', tags=['Admin'])

_CLAW_ROOT = Path(__file__).resolve().parents[2]
_WIKI_ROOT = _CLAW_ROOT / 'wiki' / 'modules'
_WATERMARK = _CLAW_ROOT / 'wiki' / '_meta' / 'last_sync.json'


def _read_watermark() -> dict:
    if _WATERMARK.exists():
        try:
            return json.loads(_WATERMARK.read_text())
        except Exception:
            pass
    return {'last_sync': None, 'embedded': 0, 'files': []}


def _write_watermark(data: dict) -> None:
    _WATERMARK.parent.mkdir(parents=True, exist_ok=True)
    _WATERMARK.write_text(json.dumps(data, indent=2))


def _git_pull() -> tuple[bool, str]:
    """Run git pull --ff-only in the repo root. Returns (success, output)."""
    result = subprocess.run(
        ['git', 'pull', '--ff-only', 'origin', 'master'],
        cwd=str(_CLAW_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def _embed_wiki_files(changed_only: bool = True) -> dict:
    """
    Embed wiki/modules/*.md files into claw_code_chunks.

    If changed_only=True, only processes files modified after last_sync watermark.
    Returns summary dict.
    """
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')

    watermark = _read_watermark()
    last_sync_str = watermark.get('last_sync')
    last_sync_ts = (
        datetime.fromisoformat(last_sync_str).timestamp()
        if last_sync_str
        else 0.0
    )

    import psycopg2
    from pgvector.psycopg2 import register_vector
    from core.context.indexer import CodeIndexer

    indexer = CodeIndexer(
        project_id='claw',
        codebase_path=str(_CLAW_ROOT),
        db_url=db_url,
    )

    conn = psycopg2.connect(db_url)
    register_vector(conn)

    embedded = 0
    skipped = 0
    errors = 0
    processed_files: list[str] = []

    for md_file in sorted(_WIKI_ROOT.glob('*.md')):
        # Skip if not modified since last sync
        if changed_only and md_file.stat().st_mtime <= last_sync_ts:
            skipped += 1
            continue

        content = md_file.read_text(encoding='utf-8')
        if len(content.strip()) < 50:
            skipped += 1
            continue

        file_path = str(md_file.relative_to(_CLAW_ROOT)).replace('\\', '/')

        # Split into sections
        sections: list[str] = []
        if '\n## ' in content:
            parts = content.split('\n## ')
            sections.append(parts[0].strip())
            for p in parts[1:]:
                sections.append('## ' + p.strip())
        else:
            words = content.split()
            for i in range(0, len(words), 200):
                sections.append(' '.join(words[i:i + 200]))

        # Delete existing chunks for this file
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM claw_code_chunks WHERE project_id=%s"
                    "  AND file_path LIKE %s AND chunk_type=%s",
                    ('claw', file_path + '%', 'wiki'),
                )
            conn.commit()
        except Exception as exc:
            log.error('Delete failed for %s: %s', file_path, exc)
            conn.rollback()
            errors += 1
            continue

        file_ok = True
        for idx, section in enumerate(sections):
            if not section.strip():
                continue
            try:
                vec = indexer.embed(section[:1500])
                ch = hashlib.sha256(section.encode()).hexdigest()[:16]
                fp = f'{file_path}/{idx}'
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO claw_code_chunks
                          (project_id, file_path, chunk_content, chunk_type,
                           chunk_name, content_hash, embedding, indexed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, NOW())
                        """,
                        ('claw', fp, section[:1500], 'wiki', md_file.stem, ch, vec),
                    )
                conn.commit()
                embedded += 1
            except Exception as exc:
                log.error('Embed failed for %s[%d]: %s', file_path, idx, exc)
                conn.rollback()
                file_ok = False
                errors += 1

        if file_ok:
            processed_files.append(md_file.name)

    conn.close()

    # Update watermark
    now = datetime.now(timezone.utc).isoformat()
    _write_watermark({
        'last_sync': now,
        'embedded': embedded,
        'files': processed_files,
    })

    return {
        'embedded': embedded,
        'skipped': skipped,
        'errors': errors,
        'files': processed_files,
        'timestamp': now,
    }


@router.post('/wiki-sync')
async def wiki_sync():
    """
    Embed any new/changed wiki articles into claw_code_chunks.

    Called by:
      - deploy_wiki.py (Windows) after pushing new articles
      - Hetzner cron (every 4h at :30) — preceded by git pull on the HOST

    NOTE: git pull is intentionally NOT done here. The Cairn container
    has wiki/modules volume-mounted from the host checkout at
    /opt/nbne/cairn/wiki/modules. The cron and deploy_wiki.py handle
    git pull on the host before calling this endpoint.
    """
    import asyncio

    # Embed only files changed since last watermark
    try:
        result = await asyncio.to_thread(_embed_wiki_files, True)
    except Exception as exc:
        log.error('/admin/wiki-sync embed failed: %s', exc, exc_info=True)
        raise HTTPException(500, f'Embed failed: {exc}')

    return {
        'git_pull': 'n/a (handled by host)',
        **result,
    }


@router.get('/wiki-sync')
async def wiki_sync_status():
    """Return the status of the last wiki sync."""
    wm = _read_watermark()
    article_count = len(list(_WIKI_ROOT.glob('*.md'))) if _WIKI_ROOT.exists() else 0
    return {
        'last_sync': wm.get('last_sync'),
        'last_embedded': wm.get('embedded', 0),
        'last_files': wm.get('files', []),
        'articles_on_disk': article_count,
    }
