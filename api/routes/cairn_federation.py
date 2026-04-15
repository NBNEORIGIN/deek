"""
Cairn module federation routes — mounted at /api/cairn/*.

Implements the snapshot-pattern federation described in CAIRN_MODULES.md:

  - Modules expose GET /api/cairn/snapshot returning a markdown summary of
    their current live state (open orders, stock, in-flight jobs, etc.)
  - Cairn polls each registered module on a fixed interval, pulls the
    markdown snapshot, and ingests it into claw_code_chunks with
    chunk_type='module_snapshot', file_path='snapshots/{module}.md'.
  - The retrieval layer can then surface live module state alongside wiki
    articles and code chunks whenever a question touches a module.

Routes:
  GET  /api/cairn/ingest-health    — freshness diagnostic for chunks + emails
  GET  /api/cairn/modules          — list registered modules + last-seen stamps
  POST /api/cairn/ingest-snapshot  — accept a snapshot payload (markdown)
  POST /api/cairn/poll-modules     — trigger an immediate poll of all modules

Background task:
  _snapshot_poll_loop() runs from api/main.py lifespan and polls every
  CAIRN_SNAPSHOT_INTERVAL_MINUTES (default 15). Registry file is
  deploy/modules.json at the repo root. If the registry file is missing
  the loop is a no-op; no modules are polled.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cairn", tags=["cairn-federation"])

_CAIRN_ROOT = Path(__file__).resolve().parents[2]
_REGISTRY_PATH = _CAIRN_ROOT / "deploy" / "modules.json"


# ── Registry loading ────────────────────────────────────────────────────────


def _load_registry() -> list[dict[str, Any]]:
    """Read deploy/modules.json. Missing file is not an error — returns []."""
    if not _REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        modules = data.get("modules", [])
        if not isinstance(modules, list):
            return []
        return [m for m in modules if isinstance(m, dict) and m.get("name")]
    except Exception as exc:
        logger.error("[cairn-federation] registry load failed: %s", exc)
        return []


# ── DB helpers ──────────────────────────────────────────────────────────────


def _get_conn():
    import psycopg2
    from pgvector.psycopg2 import register_vector

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(dsn, connect_timeout=5)
    try:
        register_vector(conn)
    except Exception:
        # pgvector extension may not yet be registered on fresh DBs; ignore
        pass
    return conn


def _write_snapshot_chunk(
    module: str,
    content: str,
    generated_at: datetime,
) -> dict[str, Any]:
    """
    Upsert a module snapshot into claw_code_chunks.

    One row per module — we DELETE any prior snapshot for the module's
    file_path before inserting, so retrieval always surfaces the latest.
    """
    from core.wiki.embeddings import get_embed_fn

    embed_fn = get_embed_fn()
    if not embed_fn:
        raise RuntimeError("no embedding provider available")

    file_path = f"snapshots/{module}.md"
    chunk_name = f"{module} live snapshot"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Cap embedding input at 6000 chars to match wiki freshness behaviour
    embedding = embed_fn(content[:6000])

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM claw_code_chunks
                   WHERE project_id = 'claw'
                     AND file_path = %s
                     AND chunk_type = 'module_snapshot'""",
                (file_path,),
            )
            cur.execute(
                """INSERT INTO claw_code_chunks
                   (project_id, file_path, chunk_content, chunk_type, chunk_name,
                    content_hash, embedding, indexed_at)
                   VALUES (%s, %s, %s, 'module_snapshot', %s, %s, %s::vector, %s)""",
                (
                    "claw",
                    file_path,
                    content,
                    chunk_name,
                    content_hash,
                    embedding,
                    generated_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "module": module,
        "file_path": file_path,
        "content_hash": content_hash,
        "generated_at": generated_at.isoformat(),
        "bytes": len(content),
    }


# ── Models ──────────────────────────────────────────────────────────────────


class SnapshotPayload(BaseModel):
    module: str = Field(..., min_length=1, max_length=100)
    snapshot_md: str = Field(..., min_length=1)
    generated_at: str | None = None


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/ingest-health")
async def ingest_health(_: bool = Depends(verify_api_key)) -> dict[str, Any]:
    """
    Freshness diagnostic for Cairn's indexed content.

    Returns the max indexed_at for each chunk_type on the 'claw' project,
    total chunk counts, and the raw email store totals (which live in
    cairn_email_raw rather than claw_code_chunks).
    """
    out: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_reachable": False,
        "chunks_by_type": {},
        "module_snapshots": [],
        "email_raw": None,
    }

    try:
        conn = _get_conn()
    except Exception as exc:
        out["error"] = f"db unreachable: {exc}"
        return out

    try:
        out["db_reachable"] = True
        with conn.cursor() as cur:
            # Bound the diagnostic — this is a health check, not a report.
            cur.execute("SET LOCAL statement_timeout = 8000")
            cur.execute(
                """SELECT chunk_type, COUNT(*), MAX(indexed_at)
                   FROM claw_code_chunks
                   WHERE project_id = 'claw'
                   GROUP BY chunk_type
                   ORDER BY chunk_type"""
            )
            for ct, cnt, last in cur.fetchall():
                out["chunks_by_type"][ct] = {
                    "count": cnt,
                    "last_indexed_at": last.isoformat() if last else None,
                }

            cur.execute(
                """SELECT file_path, indexed_at
                   FROM claw_code_chunks
                   WHERE project_id = 'claw' AND chunk_type = 'module_snapshot'
                   ORDER BY indexed_at DESC"""
            )
            out["module_snapshots"] = [
                {
                    "file_path": row[0],
                    "indexed_at": row[1].isoformat() if row[1] else None,
                }
                for row in cur.fetchall()
            ]

            # Email raw table may not exist on fresh installs
            try:
                cur.execute(
                    """SELECT COUNT(*), MAX(received_at), MAX(created_at)
                       FROM cairn_email_raw"""
                )
                row = cur.fetchone()
                out["email_raw"] = {
                    "count": row[0],
                    "last_received_at": row[1].isoformat() if row[1] else None,
                    "last_ingested_at": row[2].isoformat() if row[2] else None,
                }
            except Exception as exc:
                out["email_raw"] = {"error": str(exc)}
    finally:
        conn.close()

    return out


@router.get("/context")
async def cairn_context(_: bool = Depends(verify_api_key)) -> dict[str, Any]:
    """Cairn's own context snapshot for cross-module federation.

    Currently surfaces the delegation block (cost-discipline aggregates
    from ``cairn_delegation_log``). Additional blocks (ingest freshness,
    wiki coverage) can be added alongside.
    """
    from core.delegation.context import build_delegation_context

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "delegation": build_delegation_context(),
    }


@router.get("/modules")
async def list_modules(_: bool = Depends(verify_api_key)) -> dict[str, Any]:
    """
    List registered modules and their last-seen snapshot timestamps.

    Combines the static registry with the live claw_code_chunks state, so
    callers can see both "what modules are we supposed to be polling" and
    "when did each last successfully land a snapshot".
    """
    registry = _load_registry()
    seen: dict[str, str | None] = {}

    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT file_path, indexed_at
                       FROM claw_code_chunks
                       WHERE project_id = 'claw' AND chunk_type = 'module_snapshot'"""
                )
                for path, last in cur.fetchall():
                    # file_path is "snapshots/{module}.md"
                    name = Path(path).stem
                    seen[name] = last.isoformat() if last else None
        finally:
            conn.close()
    except Exception as exc:
        logger.error("[cairn-federation] list_modules DB error: %s", exc)

    modules_out = []
    for entry in registry:
        name = entry["name"]
        modules_out.append(
            {
                "name": name,
                "snapshot_url": entry.get("snapshot_url"),
                "interval_minutes": entry.get("interval_minutes", 15),
                "enabled": entry.get("enabled", True),
                "last_snapshot_at": seen.get(name),
            }
        )

    return {
        "registry_path": str(_REGISTRY_PATH),
        "registry_exists": _REGISTRY_PATH.exists(),
        "modules": modules_out,
    }


@router.post("/ingest-snapshot")
async def ingest_snapshot(
    payload: SnapshotPayload,
    _: bool = Depends(verify_api_key),
) -> dict[str, Any]:
    """
    Accept a module snapshot and embed it into claw_code_chunks.

    Used by the internal poll loop and (optionally) by modules that want to
    push their own snapshots on demand rather than wait for the next poll.
    """
    if not payload.snapshot_md.strip():
        raise HTTPException(status_code=400, detail="snapshot_md is empty")

    if payload.generated_at:
        try:
            stamp = datetime.fromisoformat(payload.generated_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="generated_at must be ISO-8601 if provided",
            )
    else:
        stamp = datetime.now(timezone.utc)

    module = payload.module.strip().lower().replace("/", "_").replace("\\", "_")

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _write_snapshot_chunk(module, payload.snapshot_md, stamp),
        )
    except Exception as exc:
        logger.error("[cairn-federation] ingest failed for %s: %s", module, exc)
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}")

    return {"status": "ok", **result}


@router.post("/poll-modules")
async def poll_modules_once(
    _: bool = Depends(verify_api_key),
) -> dict[str, Any]:
    """Trigger an immediate poll of all registered modules (synchronous)."""
    result = await _poll_all_modules()
    return result


# ── Poll logic ──────────────────────────────────────────────────────────────


async def _fetch_and_ingest(
    module_entry: dict[str, Any],
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Fetch a single module snapshot and write it to chunks."""
    name = module_entry["name"]
    url = module_entry.get("snapshot_url")
    if not url:
        return {"name": name, "status": "skipped", "reason": "no snapshot_url"}

    headers: dict[str, str] = {}
    auth_env = module_entry.get("auth_header_env")
    if auth_env:
        token = os.getenv(auth_env, "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await client.get(url, headers=headers, timeout=10.0)
    except Exception as exc:
        return {"name": name, "status": "error", "reason": f"fetch failed: {exc}"}

    if resp.status_code != 200:
        return {
            "name": name,
            "status": "error",
            "reason": f"HTTP {resp.status_code}",
        }

    content_type = resp.headers.get("content-type", "")
    snapshot_md: str
    generated_at_str: str | None = None

    if "application/json" in content_type:
        try:
            data = resp.json()
        except Exception as exc:
            return {"name": name, "status": "error", "reason": f"bad json: {exc}"}

        # Two accepted JSON shapes:
        # 1. {"module":..., "snapshot_md":..., "generated_at":...}
        # 2. arbitrary module snapshot JSON — serialised as a fenced block
        if isinstance(data, dict) and "snapshot_md" in data:
            snapshot_md = str(data["snapshot_md"])
            generated_at_str = data.get("generated_at")
        else:
            serialised = json.dumps(data, indent=2, default=str)
            snapshot_md = (
                f"# {name} live snapshot\n\n"
                f"Pulled from `{url}` at "
                f"{datetime.now(timezone.utc).isoformat()}\n\n"
                f"```json\n{serialised[:8000]}\n```\n"
            )
    else:
        snapshot_md = resp.text

    if not snapshot_md.strip():
        return {"name": name, "status": "skipped", "reason": "empty body"}

    # Parse generated_at if present
    stamp: datetime
    if generated_at_str:
        try:
            stamp = datetime.fromisoformat(generated_at_str.replace("Z", "+00:00"))
        except Exception:
            stamp = datetime.now(timezone.utc)
    else:
        stamp = datetime.now(timezone.utc)

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _write_snapshot_chunk(name, snapshot_md, stamp),
        )
    except Exception as exc:
        return {"name": name, "status": "error", "reason": f"write failed: {exc}"}

    return {
        "name": name,
        "status": "ok",
        "bytes": result["bytes"],
        "generated_at": result["generated_at"],
    }


async def _poll_all_modules() -> dict[str, Any]:
    """Poll every enabled module in the registry, once."""
    registry = _load_registry()
    if not registry:
        return {"status": "no_registry", "polled": 0, "results": []}

    enabled = [m for m in registry if m.get("enabled", True)]
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        for entry in enabled:
            try:
                res = await _fetch_and_ingest(entry, client)
            except Exception as exc:
                res = {"name": entry.get("name"), "status": "error", "reason": str(exc)}
            results.append(res)
            logger.info("[cairn-federation] polled %s: %s", entry.get("name"), res.get("status"))

    return {
        "status": "ok",
        "polled": len(results),
        "results": results,
    }


async def snapshot_poll_loop() -> None:
    """
    Background loop started from api/main.py lifespan.

    Polls every CAIRN_SNAPSHOT_INTERVAL_MINUTES (default 15). Sleeps first
    so Cairn startup isn't blocked on module availability.
    """
    interval_min = int(os.getenv("CAIRN_SNAPSHOT_INTERVAL_MINUTES", "15"))
    if interval_min <= 0:
        logger.info("[cairn-federation] snapshot poll disabled (interval=0)")
        return

    logger.info("[cairn-federation] snapshot poll loop started (%d min)", interval_min)
    while True:
        try:
            await asyncio.sleep(interval_min * 60)
            result = await _poll_all_modules()
            logger.info(
                "[cairn-federation] poll complete: %d modules, %d ok",
                result.get("polled", 0),
                sum(1 for r in result.get("results", []) if r.get("status") == "ok"),
            )
        except asyncio.CancelledError:
            logger.info("[cairn-federation] snapshot poll loop cancelled")
            raise
        except Exception as exc:
            logger.error("[cairn-federation] poll loop iteration failed: %s", exc)
