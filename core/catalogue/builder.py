"""
Cairn catalogue builder.

Assembles a snapshot of the full Cairn ecosystem for GET /api/cairn/catalogue:
  - which modules are registered
  - which wiki articles exist and when they were last compiled
  - which context endpoints are reachable
  - pgvector chunk counts per project
  - recompile queue status
  - last audit results and warnings

Result is cached for 60 seconds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CLAW_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS_ROOT = _CLAW_ROOT / "projects"
_WIKI_ROOT = _CLAW_ROOT / "wiki"
_META_DIR = _WIKI_ROOT / "_meta"

# 60-second response cache
_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}

CONTEXT_CHECK_TIMEOUT = 2.0  # seconds


async def build_catalogue() -> dict:
    """Return the catalogue, serving from cache if still fresh."""
    now = time.monotonic()
    if _cache["data"] is not None and now < _cache["expires_at"]:
        return _cache["data"]

    data = await _assemble()
    _cache["data"] = data
    _cache["expires_at"] = now + 60.0
    return data


async def _assemble() -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()

    # Gather all sections concurrently
    modules_task = asyncio.create_task(_build_modules())
    wiki_task = asyncio.create_task(_build_wiki_summary())
    pgvector_task = asyncio.create_task(_build_pgvector_summary())
    queue_task = asyncio.create_task(_build_queue_summary())
    audit_task = asyncio.create_task(_build_audit_summary())

    modules, wiki, pgvector, queue, audit = await asyncio.gather(
        modules_task, wiki_task, pgvector_task, queue_task, audit_task,
        return_exceptions=True,
    )

    def _safe(result: Any, fallback: Any) -> Any:
        return fallback if isinstance(result, Exception) else result

    return {
        "generated_at": generated_at,
        "modules": _safe(modules, []),
        "wiki": _safe(wiki, {}),
        "pgvector": _safe(pgvector, {}),
        "recompile_queue": _safe(queue, {}),
        "audit": _safe(audit, {}),
    }


# ─── Modules ─────────────────────────────────────────────────────────────────

async def _build_modules() -> list[dict]:
    """One entry per project that has a config.json."""
    if not _PROJECTS_ROOT.exists():
        return []

    project_dirs = [
        d for d in sorted(_PROJECTS_ROOT.iterdir())
        if d.is_dir() and not d.name.startswith("_") and (d / "config.json").exists()
    ]

    # Check all context endpoints in parallel
    tasks = [asyncio.create_task(_build_module_entry(d)) for d in project_dirs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if not isinstance(r, Exception)]


async def _build_module_entry(project_dir: Path) -> dict:
    config_path = project_dir / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        config = {}

    name = project_dir.name
    status = config.get("status", "unknown")

    # Wiki article
    wiki_slug = name.lower().replace("_", "-")
    wiki_article_path = _WIKI_ROOT / "modules" / f"{wiki_slug}.md"
    wiki_article = f"wiki/modules/{wiki_slug}.md" if wiki_article_path.exists() else None
    wiki_last_compiled = _wiki_last_compiled(name)

    # Database info from config (no direct connection — info only)
    db_info = _extract_db_info(config)

    # Context endpoint reachability check
    context_endpoint = config.get("context_endpoint")
    endpoint_status, endpoint_last_check = await _check_endpoint(context_endpoint)

    # core.md
    core_md_path = project_dir / "core.md"
    core_md = f"projects/{name}/core.md" if core_md_path.exists() else None

    return {
        "name": name,
        "status": status,
        "wiki_article": wiki_article,
        "wiki_last_compiled": wiki_last_compiled,
        "database": db_info,
        "context_endpoint": context_endpoint,
        "context_endpoint_status": endpoint_status,
        "context_endpoint_last_check": endpoint_last_check,
        "core_md": core_md,
    }


def _wiki_last_compiled(project_name: str) -> Optional[str]:
    """Return the last compiled timestamp for this project's wiki scope, if known."""
    meta = _read_json(_META_DIR / "last_compiled.json")
    if not meta:
        return None
    # Heuristic: map project name to scope
    slug = project_name.lower().replace("_", "-").replace("-intelligence", "")
    # Try exact match on scope keys
    for key, val in meta.items():
        if key == slug or key == project_name:
            return val
    # Fall back to "modules" timestamp for module-level articles
    return meta.get("modules")


def _extract_db_info(config: dict) -> Optional[dict]:
    """Extract DB host/port/name from config if present. No reachability check."""
    db_cfg = config.get("database") or config.get("db")
    if not db_cfg:
        return None
    return {
        "host": db_cfg.get("host"),
        "port": db_cfg.get("port"),
        "name": db_cfg.get("name") or db_cfg.get("database"),
        "reachable": None,  # Not checked here — would require DB credentials
    }


async def _check_endpoint(url: Optional[str]) -> tuple[str, Optional[str]]:
    """HTTP GET the context endpoint with a 2-second timeout."""
    if not url:
        return "not_built", None

    now = datetime.now(timezone.utc).isoformat()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=CONTEXT_CHECK_TIMEOUT) as client:
            resp = await client.get(url)
            if resp.status_code < 500:
                return "live", now
            return "error", now
    except ImportError:
        # httpx not available — fall back to TCP connect
        return await _tcp_check(url, now)
    except Exception:
        return "unreachable", now


async def _tcp_check(url: str, timestamp: str) -> tuple[str, str]:
    """Fallback TCP connect when httpx is unavailable."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=CONTEXT_CHECK_TIMEOUT,
        )
        writer.close()
        return "live", timestamp
    except Exception:
        return "unreachable", timestamp


# ─── Wiki summary ─────────────────────────────────────────────────────────────

async def _build_wiki_summary() -> dict:
    if not _WIKI_ROOT.exists():
        return {}

    by_category: dict[str, int] = {}
    total = 0
    for category_dir in _WIKI_ROOT.iterdir():
        if category_dir.is_dir() and not category_dir.name.startswith("_"):
            count = len(list(category_dir.glob("*.md")))
            if count > 0:
                by_category[category_dir.name] = count
                total += count

    meta = _read_json(_META_DIR / "last_compiled.json") or {}
    last_run = max((v for v in meta.values() if v), default=None)

    # Count stale articles and errors from audit data
    audit = _read_json(_META_DIR / "last_audit.json") or {}
    stale = audit.get("stale_articles", 0)
    errors = audit.get("compilation_errors", 0)

    return {
        "total_articles": total,
        "by_category": by_category,
        "last_compilation_run": last_run,
        "stale_articles": stale,
        "compilation_errors": errors,
    }


# ─── pgvector summary ────────────────────────────────────────────────────────

async def _build_pgvector_summary() -> dict:
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        return {}

    try:
        import psycopg2

        def _query() -> dict:
            conn = psycopg2.connect(db_url, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    # Total chunks
                    cur.execute("SELECT COUNT(*) FROM claw_code_chunks")
                    total = cur.fetchone()[0]

                    # Per-project counts
                    cur.execute(
                        "SELECT project_id, COUNT(*) FROM claw_code_chunks GROUP BY project_id"
                    )
                    by_project = {row[0]: row[1] for row in cur.fetchall()}

                    # By chunk type
                    cur.execute(
                        "SELECT chunk_type, COUNT(*) FROM claw_code_chunks GROUP BY chunk_type"
                    )
                    by_type = {row[0]: row[1] for row in cur.fetchall()}

                    # Last index update
                    cur.execute(
                        "SELECT MAX(indexed_at) FROM claw_code_chunks"
                    )
                    last_update = cur.fetchone()[0]

                return {
                    "total_chunks": total,
                    "by_project": by_project,
                    "by_chunk_type": by_type,
                    "last_index_update": last_update.isoformat() if last_update else None,
                }
            finally:
                conn.close()

        return await asyncio.get_event_loop().run_in_executor(None, _query)
    except Exception as exc:
        logger.warning("[catalogue] pgvector query failed: %s", exc)
        return {"error": str(exc)}


# ─── Recompile queue summary ──────────────────────────────────────────────────

async def _build_queue_summary() -> dict:
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        return {}

    try:
        import psycopg2

        def _query() -> dict:
            conn = psycopg2.connect(db_url, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'pending')    AS pending,
                            COUNT(*) FILTER (WHERE status = 'processing') AS in_progress,
                            COUNT(*) FILTER (WHERE status = 'error')      AS errored,
                            COUNT(*) FILTER (WHERE status = 'completed'
                                             AND completed_at >= NOW() - INTERVAL '1 day')
                                                                          AS completed_today
                        FROM wiki_recompile_queue
                        """
                    )
                    row = cur.fetchone()
                    if not row:
                        return {}
                    return {
                        "pending": row[0],
                        "in_progress": row[1],
                        "errored": row[2],
                        "completed_today": row[3],
                    }
            finally:
                conn.close()

        return await asyncio.get_event_loop().run_in_executor(None, _query)
    except Exception as exc:
        logger.warning("[catalogue] queue query failed: %s", exc)
        return {}


# ─── Audit summary ───────────────────────────────────────────────────────────

async def _build_audit_summary() -> dict:
    audit = _read_json(_META_DIR / "last_audit.json") or {}
    return {
        "last_run": audit.get("last_run"),
        "next_run": audit.get("next_run"),
        "warnings": audit.get("warnings", []),
    }


# ─── Daily audit runner ───────────────────────────────────────────────────────

async def run_daily_audit() -> dict:
    """
    Audit the Cairn ecosystem:
      1. Walk wiki directory — find articles stale vs DB indexed_at
      2. Ping every registered context endpoint
      3. Count pgvector chunks per project — flag zero-chunk projects
      4. Write results to wiki/_meta/last_audit.json

    Called once per day from the main.py scheduled background task.
    Returns the audit result dict.
    """
    from datetime import timedelta
    warnings: list[str] = []
    stale_articles = 0
    compilation_errors = 0
    now = datetime.now(timezone.utc)

    # 1. Stale wiki articles
    db_url = os.getenv("DATABASE_URL", "")
    if db_url and _WIKI_ROOT.exists():
        try:
            import psycopg2

            def _stale_check() -> list[str]:
                conn = psycopg2.connect(db_url, connect_timeout=5)
                stale: list[str] = []
                try:
                    for md_file in _WIKI_ROOT.rglob("*.md"):
                        if md_file.name == "index.md":
                            continue
                        rel = str(md_file.relative_to(_CLAW_ROOT)).replace("\\", "/")
                        mtime = md_file.stat().st_mtime
                        with conn.cursor() as cur:
                            cur.execute(
                                """SELECT indexed_at FROM claw_code_chunks
                                   WHERE project_id = 'claw' AND file_path = %s
                                     AND chunk_type = 'wiki'""",
                                (rel,),
                            )
                            row = cur.fetchone()
                        if not row or (row[0] and mtime > row[0].timestamp()):
                            stale.append(rel)
                finally:
                    conn.close()
                return stale

            stale_paths = await asyncio.get_event_loop().run_in_executor(None, _stale_check)
            if stale_paths:
                stale_articles = len(stale_paths)
                warnings.append(
                    f"{stale_articles} wiki article(s) newer than last embedding — "
                    "queued for recompilation"
                )
                # Queue stale articles for recompilation
                _queue_stale_articles(stale_paths, db_url)
        except Exception as exc:
            logger.warning("[audit] stale check failed: %s", exc)
            warnings.append(f"Stale article check failed: {exc}")

    # 2. Context endpoint reachability
    endpoint_tasks = []
    project_names = []
    if _PROJECTS_ROOT.exists():
        for d in _PROJECTS_ROOT.iterdir():
            if d.is_dir() and not d.name.startswith("_") and (d / "config.json").exists():
                try:
                    cfg = json.loads((d / "config.json").read_text())
                    ep = cfg.get("context_endpoint")
                    if ep:
                        project_names.append(d.name)
                        endpoint_tasks.append(asyncio.create_task(_check_endpoint(ep)))
                except Exception:
                    pass

    if endpoint_tasks:
        ep_results = await asyncio.gather(*endpoint_tasks, return_exceptions=True)
        for proj, result in zip(project_names, ep_results):
            if isinstance(result, Exception) or result[0] == "unreachable":
                warnings.append(f"Module '{proj}' context endpoint unreachable")

    # 3. Zero-chunk projects
    if db_url:
        try:
            import psycopg2

            def _zero_check() -> list[str]:
                conn = psycopg2.connect(db_url, connect_timeout=5)
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT project_id, COUNT(*) FROM claw_code_chunks GROUP BY project_id"
                        )
                        indexed = {row[0]: row[1] for row in cur.fetchall()}
                    zero = []
                    if _PROJECTS_ROOT.exists():
                        for d in _PROJECTS_ROOT.iterdir():
                            if d.is_dir() and not d.name.startswith("_") and (d / "config.json").exists():
                                if indexed.get(d.name, 0) == 0:
                                    zero.append(d.name)
                    return zero
                finally:
                    conn.close()

            zero_projects = await asyncio.get_event_loop().run_in_executor(None, _zero_check)
            for proj in zero_projects:
                warnings.append(f"Module '{proj}' has zero pgvector chunks — index may be broken")
        except Exception as exc:
            logger.warning("[audit] zero-chunk check failed: %s", exc)

    # Write audit results
    next_run = (now + timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0
    ).isoformat()

    result = {
        "last_run": now.isoformat(),
        "next_run": next_run,
        "stale_articles": stale_articles,
        "compilation_errors": compilation_errors,
        "warnings": warnings,
    }

    _META_DIR.mkdir(parents=True, exist_ok=True)
    (_META_DIR / "last_audit.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    logger.info("[audit] Complete — %d warnings", len(warnings))

    # Invalidate catalogue cache so next call reflects new audit data
    _cache["expires_at"] = 0.0

    return result


def _queue_stale_articles(stale_paths: list[str], db_url: str) -> None:
    """Push stale article paths into wiki_recompile_queue (best-effort)."""
    try:
        import psycopg2

        conn = psycopg2.connect(db_url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                for path in stale_paths:
                    # Derive scope from path: wiki/modules/... -> modules
                    parts = path.replace("\\", "/").split("/")
                    scope = parts[1] if len(parts) > 1 else "modules"
                    entity = Path(path).stem
                    cur.execute(
                        """INSERT INTO wiki_recompile_queue (module, scope, entity, status)
                           VALUES ('audit', %s, %s, 'pending')
                           ON CONFLICT DO NOTHING""",
                        (scope, entity),
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("[audit] Could not queue stale articles: %s", exc)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None
