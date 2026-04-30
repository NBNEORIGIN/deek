"""Machinery-manuals admin API.

Companion to scripts/ingest_manuals.py — same parsing + chunking +
embedding pipeline, but exposed as HTTP endpoints so Toby and Jo can
upload manuals through the /admin/manuals web UI without needing
local Python or DB credentials.

Files land at ``/app/data/manuals/<Machine>/<sanitised-filename>``
(volume-mounted from ``/opt/nbne/manuals/`` on Hetzner). The volume
is durable — survives container rebuilds, included in Ark snapshots
of /opt/nbne/.

Auth: every endpoint requires X-API-Key (verify_api_key). The Next.js
proxy layer adds JWT cookie auth + an ADMIN-role check on top, same
pattern as /api/deek/admin/* in routes/users.py.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.middleware.auth import verify_api_key

log = logging.getLogger(__name__)
router = APIRouter(prefix='/manuals', tags=['Machinery manuals'])

# Volume root inside the container. Override with DEEK_MANUALS_DIR for
# dev — production binds /app/data/manuals to /opt/nbne/manuals on host.
MANUALS_DIR = Path(os.getenv('DEEK_MANUALS_DIR') or '/app/data/manuals')
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file


def _sanitise_machine(name: str) -> str:
    """Strip whitespace + drop characters that don't belong on a path.
    Preserves case so "Hulk" reads naturally; only filters truly unsafe
    chars like ../ and null bytes."""
    n = (name or '').strip().strip('"').strip("'")
    if not n:
        return '_unsorted'
    # Collapse anything path-traversal-y to underscores
    n = re.sub(r'[\\/\x00:*?<>|"]', '_', n)
    return n[:80] or '_unsorted'


def _sanitise_filename(name: str) -> str:
    """Same idea for file basenames. Allow spaces and dots and dashes."""
    n = Path(name or 'upload').name  # strip any directory parts
    n = re.sub(r'[\\/\x00:*?<>|"]', '_', n)
    n = n.strip()
    return n[:200] or 'upload'


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


@router.post('/upload')
async def manuals_upload(
    file: UploadFile = File(...),
    machine: str = Form(...),
    _: bool = Depends(verify_api_key),
):
    """Save the file to ``/app/data/manuals/<Machine>/`` and ingest it
    inline. Returns the number of chunks added to claw_code_chunks.

    Idempotent: re-uploading the same file replaces existing chunks
    (the ingest pipeline keys on file_path + chunk_name + chunk_type
    and does delete-then-insert).
    """
    machine_clean = _sanitise_machine(machine)
    filename = _sanitise_filename(file.filename or 'upload')

    machine_dir = MANUALS_DIR / machine_clean
    _ensure_dir(machine_dir)

    # Read into memory with a size guard. We don't stream because the
    # ingest functions take a path on disk anyway.
    blob = await file.read()
    size = len(blob)
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f'file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit',
        )
    if size == 0:
        raise HTTPException(status_code=400, detail='empty file')

    target = machine_dir / filename
    target.write_bytes(blob)

    # Hand off to the existing ingest pipeline. We import inside the
    # handler so any failure to import (e.g. missing pillow-heif on
    # a dev machine) returns a clean 500 rather than crashing module
    # load and breaking the whole API.
    try:
        from scripts.ingest_manuals import _process_file, _connect_db, _embed_fn
    except Exception as exc:
        log.error('[manuals/upload] ingest import failed: %s', exc)
        raise HTTPException(status_code=500, detail=f'ingest unavailable: {exc}')

    try:
        conn = _connect_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'DB unreachable: {exc}')
    try:
        try:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
        except Exception:
            pass
        embed_fn = _embed_fn()
        stat = _process_file(
            target, MANUALS_DIR, machine_clean,
            skip_images=False,
            dry_run=False,
            conn=conn,
            embed_fn=embed_fn,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if stat.get('error'):
        # Keep the file on disk so the operator can investigate, but
        # surface the failure to the UI.
        raise HTTPException(status_code=500, detail=stat['error'])

    return {
        'ok': True,
        'machine': machine_clean,
        'filename': filename,
        'size': size,
        'chunks': stat.get('chunks', 0),
        'embedded': stat.get('embedded', 0),
        'skipped': stat.get('skipped') or None,
        'path_on_disk': str(target.relative_to(MANUALS_DIR)),
    }


@router.get('/machines')
async def manuals_machines(_: bool = Depends(verify_api_key)):
    """List distinct machine names — both from the disk layout (folders
    that exist under MANUALS_DIR) and from already-indexed chunks. The
    union is what the upload UI's dropdown should offer."""
    on_disk: list[str] = []
    if MANUALS_DIR.exists():
        on_disk = sorted(
            p.name for p in MANUALS_DIR.iterdir()
            if p.is_dir() and not p.name.startswith('.')
        )

    indexed: list[str] = []
    try:
        from scripts.ingest_manuals import _connect_db
        conn = _connect_db()
        try:
            with conn.cursor() as cur:
                # chunk_name pattern is "<machine> · <file> · chunk-NNN"
                # so split on the first ' · ' to extract the machine.
                cur.execute(
                    """SELECT DISTINCT split_part(chunk_name, ' · ', 1) AS machine
                       FROM claw_code_chunks
                       WHERE project_id = 'deek'
                         AND chunk_type = 'manual'
                         AND chunk_name IS NOT NULL
                       ORDER BY machine"""
                )
                indexed = [r[0] for r in cur.fetchall() if r[0]]
        finally:
            conn.close()
    except Exception as exc:
        log.warning('[manuals/machines] indexed lookup failed: %s', exc)

    # Union, preserving order: disk-first (canonical, even if no chunks
    # yet), then indexed-only ones we don't have folders for.
    seen = set(on_disk)
    extras = [m for m in indexed if m not in seen]
    return {'machines': on_disk + extras}


@router.get('/list')
async def manuals_list(_: bool = Depends(verify_api_key)):
    """List ingested manuals with their chunk counts and last-indexed
    timestamps. Used by the admin page to show what's there."""
    try:
        from scripts.ingest_manuals import _connect_db
        conn = _connect_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'DB unreachable: {exc}')
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                       split_part(chunk_name, ' · ', 1) AS machine,
                       file_path,
                       COUNT(*) AS chunks,
                       MAX(indexed_at)::timestamp(0) AS last_indexed
                   FROM claw_code_chunks
                   WHERE project_id = 'deek'
                     AND chunk_type = 'manual'
                     AND chunk_name IS NOT NULL
                   GROUP BY machine, file_path
                   ORDER BY last_indexed DESC NULLS LAST,
                            machine, file_path"""
            )
            rows = [
                {
                    'machine': r[0],
                    'file_path': r[1],
                    'chunks': r[2],
                    'last_indexed': r[3].isoformat() if r[3] else None,
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()
    return {'manuals': rows}


@router.delete('/by-path')
async def manuals_delete(
    file_path: str,
    _: bool = Depends(verify_api_key),
):
    """Delete one manual: removes its rows from claw_code_chunks AND
    deletes the file from disk. Use this when a manual is wrong / out
    of date and you don't want it influencing search any more.

    file_path is the relative path under MANUALS_DIR, e.g. "Hulk/manual.pdf".
    Sent as a query param so the URL stays simple — multipart DELETE
    bodies are awkward across HTTP libraries.
    """
    fp = (file_path or '').strip()
    if not fp or '..' in fp or fp.startswith('/'):
        raise HTTPException(status_code=400, detail='invalid file_path')

    try:
        from scripts.ingest_manuals import _connect_db
        conn = _connect_db()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'DB unreachable: {exc}')
    deleted_chunks = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM claw_code_chunks
                    WHERE project_id = 'deek'
                      AND chunk_type = 'manual'
                      AND file_path = %s""",
                (fp,),
            )
            deleted_chunks = cur.rowcount
            conn.commit()
    finally:
        conn.close()

    target = MANUALS_DIR / fp
    file_existed = target.exists()
    if file_existed:
        try:
            target.unlink()
        except Exception as exc:
            log.warning('[manuals/delete] unlink failed for %s: %s', target, exc)

    return {
        'ok': True,
        'file_path': fp,
        'deleted_chunks': deleted_chunks,
        'file_removed': file_existed,
    }
