"""Delegation cost-discipline aggregates from ``cairn_delegation_log``.

Read-only helper used by ``GET /api/cairn/context`` to surface delegation
spend and outcome rates across all cairn_delegate calls. Never raises —
callers treat this as a dashboard feed; missing DB or missing table
returns the zero-state dict.

Produced by cairn_delegate dogfooding session 2026-04-15 (D-...). Grok
Fast drafted the core query logic; the slash-less module derivation and
float-return coercion were tweaked by Sonnet in review.
"""
from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _zero_state() -> dict:
    return {
        "spend_gbp_mtd": 0.0,
        "spend_gbp_ytd": 0.0,
        "calls_mtd": 0,
        "by_module": [],
        "by_model": [],
        "top_delegating_sessions": [],
        "schema_failure_rate": 0.0,
        "refusal_rate": 0.0,
    }


def _module_for(session: str) -> str:
    """Derive module from delegating_session ('claw/foo' -> 'claw').

    Slash-less or empty session → 'unknown'.
    """
    if not session or "/" not in session:
        return "unknown"
    return session.split("/", 1)[0]


def build_delegation_context(db_path: Path | None = None) -> dict:
    """Aggregate cairn_delegation_log into the /api/cairn/context delegation block.

    Returns a dict matching the shape documented in projects/claw/core.md
    (D-... dogfooding entry). Missing DB, missing table → zero state.
    """
    if db_path is None:
        data_dir = os.getenv("CLAW_DATA_DIR", "./data")
        db_path = Path(data_dir) / "claw.db"

    if not db_path.exists():
        return _zero_state()

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("cairn_delegation_log",),
        )
        if not cur.fetchone():
            return _zero_state()

        now = datetime.now(timezone.utc)
        mtd_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ytd_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        iso_mtd = mtd_start.isoformat()
        iso_ytd = ytd_start.isoformat()

        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_gbp), 0) FROM cairn_delegation_log "
            "WHERE called_at >= ?",
            (iso_mtd,),
        )
        row = cur.fetchone()
        calls_mtd = int(row[0])
        spend_mtd = round(float(row[1]), 4)

        cur.execute(
            "SELECT COALESCE(SUM(cost_gbp), 0) FROM cairn_delegation_log "
            "WHERE called_at >= ?",
            (iso_ytd,),
        )
        spend_ytd = round(float(cur.fetchone()[0]), 4)

        cur.execute("SELECT COUNT(*) FROM cairn_delegation_log")
        total = int(cur.fetchone()[0])

        cur.execute(
            "SELECT COUNT(*) FROM cairn_delegation_log WHERE outcome = ?",
            ("refusal",),
        )
        refusals = int(cur.fetchone()[0])
        refusal_rate = round(refusals / total, 4) if total else 0.0

        cur.execute(
            "SELECT COUNT(*) FROM cairn_delegation_log "
            "WHERE outcome IN ('success', 'schema_failure')"
        )
        denom_sf = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*) FROM cairn_delegation_log WHERE outcome = ?",
            ("schema_failure",),
        )
        num_sf = int(cur.fetchone()[0])
        sf_rate = round(num_sf / denom_sf, 4) if denom_sf else 0.0

        cur.execute(
            """SELECT model_used, COUNT(*), COALESCE(SUM(cost_gbp), 0)
               FROM cairn_delegation_log
               GROUP BY model_used
               ORDER BY COUNT(*) DESC, model_used ASC"""
        )
        by_model = [
            {"model": r[0], "calls": int(r[1]), "spend_gbp": round(float(r[2]), 4)}
            for r in cur.fetchall()
        ]

        cur.execute(
            """SELECT delegating_session, COUNT(*), COALESCE(SUM(cost_gbp), 0)
               FROM cairn_delegation_log
               GROUP BY delegating_session
               ORDER BY COUNT(*) DESC, delegating_session ASC
               LIMIT 5"""
        )
        top_sessions = [
            {"session": r[0], "calls": int(r[1]), "spend_gbp": round(float(r[2]), 4)}
            for r in cur.fetchall()
        ]

        module_totals: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "spend_gbp": 0.0}
        )
        cur.execute("SELECT delegating_session, cost_gbp FROM cairn_delegation_log")
        for session, cost in cur.fetchall():
            module = _module_for(session)
            module_totals[module]["calls"] += 1
            module_totals[module]["spend_gbp"] += float(cost or 0.0)

        by_module = [
            {
                "module": mod,
                "calls": int(totals["calls"]),
                "spend_gbp": round(totals["spend_gbp"], 4),
            }
            for mod, totals in module_totals.items()
        ]
        by_module.sort(key=lambda x: (-x["calls"], x["module"]))

    return {
        "spend_gbp_mtd": spend_mtd,
        "spend_gbp_ytd": spend_ytd,
        "calls_mtd": calls_mtd,
        "by_module": by_module,
        "by_model": by_model,
        "top_delegating_sessions": top_sessions,
        "schema_failure_rate": sf_rate,
        "refusal_rate": refusal_rate,
    }
