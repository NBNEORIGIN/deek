"""
Ambient + voice interface endpoints — Phase 0 of the Deek Voice & Ambient brief.

Three endpoints, all read-only except /tasks:

    GET    /api/deek/morning-number?location=workshop|office|home
    GET    /api/deek/ambient?location=workshop|office|home
    POST   /api/deek/tasks          { assignee, content, source, location, ... }
    GET    /api/deek/tasks?assignee=X&status=open
    PATCH  /api/deek/tasks/{id}     { status }

Design notes:

- Data for morning-number and ambient comes from the existing module
  federation snapshots stored in ``claw_code_chunks`` with
  ``chunk_type='module_snapshot'``. The federation poll already refreshes
  them every 15 min; we just parse the markdown headers out.
- ``deek_tasks`` is a new Deek-owned table (created on first use). Not a
  replacement for CRM follow-ups — those are project-scoped. This is
  voice-captured ad-hoc notes ("remind Ben to recheck DONALD stock").
- Endpoints are intentionally cheap (<500ms). No LLM calls. No blocking
  network IO beyond Postgres. Auth via shared DEEK_API_KEY for now; user
  auth comes with the PWA.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.middleware.auth import verify_api_key

router = APIRouter(tags=["deek-ambient"])


# ── Location config ─────────────────────────────────────────────────────────

VALID_LOCATIONS = {"workshop", "office", "home"}

# Staleness threshold for a module snapshot — older than this and we
# return stale=True and trend=null in morning-number responses.
SNAPSHOT_FRESHNESS_HOURS = 2.0


# ── Response schemas ────────────────────────────────────────────────────────


class MorningNumber(BaseModel):
    number: str
    unit: str
    headline: str
    subtitle: str
    trend: Optional[str] = None  # "up" | "down" | "flat" | None
    as_of: Optional[datetime] = None
    source_module: str
    stale: bool = False


class PanelItem(BaseModel):
    label: str
    status: Optional[str] = None
    detail: Optional[str] = None


class AmbientPanel(BaseModel):
    id: str
    title: str
    items: list[PanelItem]


class RecentRecommendation(BaseModel):
    text: str
    created_at: Optional[datetime] = None
    dissent: str = "none"


class AmbientPayload(BaseModel):
    location: str
    morning_number: MorningNumber
    panels: list[AmbientPanel]
    recent_recommendation: Optional[RecentRecommendation] = None
    generated_at: datetime


class TaskCreate(BaseModel):
    assignee: str
    content: str
    source: str = "voice"          # voice | web | api
    location: Optional[str] = None
    created_by: Optional[str] = None
    due_at: Optional[datetime] = None


class TaskPatch(BaseModel):
    status: Optional[str] = None    # open | done | cancelled
    content: Optional[str] = None
    due_at: Optional[datetime] = None


class Task(BaseModel):
    id: int
    assignee: str
    content: str
    status: str
    source: str
    location: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    due_at: Optional[datetime]
    completed_at: Optional[datetime]


class TaskList(BaseModel):
    tasks: list[Task]


# ── DB helpers ──────────────────────────────────────────────────────────────


def _get_conn():
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    return psycopg2.connect(dsn, connect_timeout=5)


def _ensure_tasks_schema() -> None:
    """Create deek_tasks if it doesn't exist. Safe to call repeatedly."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deek_tasks (
                    id SERIAL PRIMARY KEY,
                    assignee VARCHAR(100) NOT NULL,
                    content TEXT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    source VARCHAR(20) NOT NULL DEFAULT 'voice',
                    location VARCHAR(20),
                    created_by VARCHAR(100),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    due_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_deek_tasks_assignee_status "
                "ON deek_tasks(assignee, status)"
            )
        conn.commit()
    finally:
        conn.close()


def _load_snapshot(module: str) -> tuple[str | None, datetime | None]:
    """Return (markdown_content, indexed_at) for the latest snapshot of
    the given module, or (None, None) if no snapshot exists.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_content, indexed_at
                FROM claw_code_chunks
                WHERE project_id = 'deek'
                  AND chunk_type = 'module_snapshot'
                  AND file_path = %s
                ORDER BY indexed_at DESC
                LIMIT 1
                """,
                (f"snapshots/{module}.md",),
            )
            row = cur.fetchone()
            if not row:
                return None, None
            return row[0], row[1]
    finally:
        conn.close()


def _is_stale(ts: Optional[datetime]) -> bool:
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age > (SNAPSHOT_FRESHNESS_HOURS * 3600)


# ── Snapshot parsers ────────────────────────────────────────────────────────


def _parse_manufacture_snapshot(md: str) -> dict:
    """Extract the fields we care about from the Manufacture snapshot markdown."""
    out: dict = {
        "open_orders": None,
        "rolf_units": None,
        "mimaki_units": None,
        "mutoh_units": None,
        "top_deficits": [],
    }
    m = re.search(r"Total open orders:\s*\*?\*?(\d+)", md)
    if m:
        out["open_orders"] = int(m.group(1))
    for machine_key, machine_label in (
        ("rolf_units", "ROLF"), ("mimaki_units", "MIMAKI"), ("mutoh_units", "MUTOH"),
    ):
        m = re.search(rf"{machine_label}:\s*(\d+)\s*orders?,\s*(\d+)\s*units?", md)
        if m:
            out[machine_key] = {
                "orders": int(m.group(1)),
                "units": int(m.group(2)),
            }
    # Top deficit lines: "  - M0634: 0 on hand, 962 short"
    for match in re.finditer(
        r"-\s+(M\d+):\s+(\d+)\s+on\s+hand,\s+(\d+)\s+short",
        md,
    ):
        out["top_deficits"].append({
            "sku": match.group(1),
            "on_hand": int(match.group(2)),
            "short": int(match.group(3)),
        })
        if len(out["top_deficits"]) >= 5:
            break
    return out


def _parse_crm_snapshot(md: str) -> dict:
    """Extract fields from CRM snapshot markdown."""
    out: dict = {
        "pipeline_value": None,
        "active_projects": None,
        "follow_ups_overdue": None,
        "stale_leads": None,
        "recent_activity_7d": None,
    }
    m = re.search(r"Pipeline value\*?\*?:\s*£([\d,]+\.?\d*)\s*across\s*(\d+)\s*active", md)
    if m:
        out["pipeline_value"] = float(m.group(1).replace(",", ""))
        out["active_projects"] = int(m.group(2))
    m = re.search(r"Follow-ups overdue\*?\*?:\s*(\d+)", md)
    if m:
        out["follow_ups_overdue"] = int(m.group(1))
    m = re.search(r"Stale leads[^:]*:\s*\*?\*?(\d+)", md)
    if m:
        out["stale_leads"] = int(m.group(1))
    m = re.search(r"Recent activity[^:]*:\s*\*?\*?(\d+)", md)
    if m:
        out["recent_activity_7d"] = int(m.group(1))
    return out


def _parse_ledger_snapshot(md: str) -> dict:
    """Extract cash + revenue from Ledger snapshot markdown."""
    out: dict = {
        "cash_position": None,
        "revenue_mtd": None,
        "revenue_ytd": None,
        "gross_margin_mtd": None,
    }
    m = re.search(r"Cash Position:\*?\*?\s*£([\d,]+\.?\d*)", md)
    if m:
        out["cash_position"] = float(m.group(1).replace(",", ""))
    m = re.search(r"Revenue MTD:\*?\*?\s*£([\d,]+\.?\d*)", md)
    if m:
        out["revenue_mtd"] = float(m.group(1).replace(",", ""))
    m = re.search(r"Revenue YTD:\*?\*?\s*£([\d,]+\.?\d*)", md)
    if m:
        out["revenue_ytd"] = float(m.group(1).replace(",", ""))
    m = re.search(r"Gross margin MTD:\*?\*?\s*([\d.]+)%", md)
    if m:
        out["gross_margin_mtd"] = float(m.group(1))
    return out


# ── Inbox triage count (office ambient panel) ───────────────────────────────


def _inbox_triage_counts() -> dict:
    """Count email_triage rows from the last 24h by classification.

    Returns {total, new_enquiry, existing_project_reply, unread, oldest_unread_minutes}.
    If the table doesn't exist (fresh DB) returns zeros, doesn't raise.
    """
    out = {
        "total": 0,
        "new_enquiry": 0,
        "existing_project_reply": 0,
        "unread": 0,
    }
    try:
        conn = _get_conn()
    except Exception:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT classification, COUNT(*)
                FROM cairn_intel.email_triage
                WHERE processed_at >= NOW() - INTERVAL '24 hours'
                GROUP BY classification
                """
            )
            for cls, n in cur.fetchall():
                out["total"] += n
                if cls in out:
                    out[cls] = n
            # "Unread" = not yet sent to Toby
            cur.execute(
                """
                SELECT COUNT(*) FROM cairn_intel.email_triage
                WHERE sent_to_toby_at IS NULL
                  AND processed_at >= NOW() - INTERVAL '24 hours'
                """
            )
            out["unread"] = cur.fetchone()[0]
    except Exception:
        # Table missing or some other issue — degrade gracefully
        pass
    finally:
        conn.close()
    return out


# ── Morning number ──────────────────────────────────────────────────────────


def _morning_number_workshop() -> MorningNumber:
    md, indexed_at = _load_snapshot("manufacture")
    if md is None:
        return MorningNumber(
            number="—", unit="", headline="Manufacture snapshot unavailable",
            subtitle="Check module federation poll",
            source_module="manufacture", stale=True,
        )
    data = _parse_manufacture_snapshot(md)
    open_orders = data.get("open_orders") or 0
    total_units = 0
    for key in ("rolf_units", "mimaki_units", "mutoh_units"):
        v = data.get(key)
        if isinstance(v, dict):
            total_units += v.get("units", 0)
    subtitle = f"{open_orders} open orders"
    if total_units:
        subtitle = f"{total_units} units across {open_orders} orders"
    return MorningNumber(
        number=str(open_orders),
        unit="orders",
        headline=f"{open_orders} open orders today",
        subtitle=subtitle,
        as_of=indexed_at,
        source_module="manufacture",
        stale=_is_stale(indexed_at),
    )


def _morning_number_office() -> MorningNumber:
    md, indexed_at = _load_snapshot("crm")
    if md is None:
        return MorningNumber(
            number="—", unit="", headline="CRM snapshot unavailable",
            subtitle="Check module federation poll",
            source_module="crm", stale=True,
        )
    data = _parse_crm_snapshot(md)
    overdue = data.get("follow_ups_overdue") or 0
    pipeline = data.get("pipeline_value") or 0
    projects = data.get("active_projects") or 0
    headline = (
        f"{overdue} follow-ups overdue" if overdue
        else f"{projects} active projects"
    )
    subtitle = f"£{pipeline:,.0f} pipeline" if pipeline else "(pipeline figure unavailable)"
    return MorningNumber(
        number=str(overdue if overdue else projects),
        unit="follow-ups" if overdue else "projects",
        headline=headline,
        subtitle=subtitle,
        as_of=indexed_at,
        source_module="crm",
        stale=_is_stale(indexed_at),
    )


def _morning_number_home() -> MorningNumber:
    md, indexed_at = _load_snapshot("ledger")
    if md is None:
        return MorningNumber(
            number="—", unit="", headline="Ledger snapshot unavailable",
            subtitle="Module not yet online",
            source_module="ledger", stale=True,
        )
    data = _parse_ledger_snapshot(md)
    cash = data.get("cash_position") or 0
    rev_mtd = data.get("revenue_mtd") or 0
    return MorningNumber(
        number=f"£{cash:,.0f}",
        unit="cash",
        headline=f"£{cash:,.0f} cash position",
        subtitle=f"£{rev_mtd:,.0f} revenue MTD" if rev_mtd else "",
        as_of=indexed_at,
        source_module="ledger",
        stale=_is_stale(indexed_at),
    )


@router.get("/morning-number", response_model=MorningNumber)
async def morning_number(
    location: str = Query(..., description="workshop | office | home"),
    _: bool = Depends(verify_api_key),
):
    if location not in VALID_LOCATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"location must be one of {sorted(VALID_LOCATIONS)}",
        )
    if location == "workshop":
        return _morning_number_workshop()
    if location == "office":
        return _morning_number_office()
    return _morning_number_home()


# ── Ambient payload ─────────────────────────────────────────────────────────


def _panels_workshop() -> list[AmbientPanel]:
    md, _ = _load_snapshot("manufacture")
    data = _parse_manufacture_snapshot(md) if md else {}
    machines = []
    for label, key in (("ROLF", "rolf_units"), ("MIMAKI", "mimaki_units"), ("MUTOH", "mutoh_units")):
        v = data.get(key)
        if isinstance(v, dict) and v.get("orders"):
            machines.append(PanelItem(
                label=label,
                status="running",
                detail=f'{v["orders"]} orders · {v["units"]} units',
            ))
        else:
            machines.append(PanelItem(label=label, status="available", detail=None))

    deficits = [
        PanelItem(
            label=d["sku"],
            status=None,
            detail=f'{d["short"]} short ({d["on_hand"]} on hand)',
        )
        for d in data.get("top_deficits", [])
    ]

    return [
        AmbientPanel(id="machine_status", title="Machines", items=machines),
        AmbientPanel(id="stock_deficits", title="Top 5 stock deficits", items=deficits),
    ]


def _panels_office() -> list[AmbientPanel]:
    crm_md, _ = _load_snapshot("crm")
    crm = _parse_crm_snapshot(crm_md) if crm_md else {}
    triage = _inbox_triage_counts()

    inbox_items = [
        PanelItem(label="New enquiries (24h)", detail=str(triage.get("new_enquiry", 0))),
        PanelItem(label="Project replies (24h)", detail=str(triage.get("existing_project_reply", 0))),
        PanelItem(label="Unreviewed", detail=str(triage.get("unread", 0))),
    ]
    crm_items = [
        PanelItem(
            label="Follow-ups overdue",
            detail=str(crm.get("follow_ups_overdue", 0) or 0),
            status="amber" if (crm.get("follow_ups_overdue") or 0) > 0 else None,
        ),
        PanelItem(
            label="Stale leads (14+ days)",
            detail=str(crm.get("stale_leads", 0) or 0),
        ),
        PanelItem(
            label="Recent activity (7d)",
            detail=str(crm.get("recent_activity_7d", 0) or 0),
        ),
    ]
    return [
        AmbientPanel(id="inbox_triage", title="Inbox", items=inbox_items),
        AmbientPanel(id="crm_followups", title="CRM", items=crm_items),
    ]


def _panels_home() -> list[AmbientPanel]:
    led_md, _ = _load_snapshot("ledger")
    led = _parse_ledger_snapshot(led_md) if led_md else {}

    financial_items = [
        PanelItem(
            label="Cash",
            detail=f'£{(led.get("cash_position") or 0):,.0f}',
        ),
        PanelItem(
            label="Revenue MTD",
            detail=f'£{(led.get("revenue_mtd") or 0):,.0f}',
        ),
        PanelItem(
            label="Gross margin MTD",
            detail=f'{(led.get("gross_margin_mtd") or 0):.1f}%',
        ),
    ]
    return [
        AmbientPanel(
            id="financial_health", title="Financial", items=financial_items,
        ),
    ]


@router.get("/ambient", response_model=AmbientPayload)
async def ambient(
    location: str = Query(..., description="workshop | office | home"),
    _: bool = Depends(verify_api_key),
):
    if location not in VALID_LOCATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"location must be one of {sorted(VALID_LOCATIONS)}",
        )
    if location == "workshop":
        panels = _panels_workshop()
        mn = _morning_number_workshop()
    elif location == "office":
        panels = _panels_office()
        mn = _morning_number_office()
    else:
        panels = _panels_home()
        mn = _morning_number_home()

    return AmbientPayload(
        location=location,
        morning_number=mn,
        panels=panels,
        recent_recommendation=None,  # Wired in Phase 1 when dissent system is live
        generated_at=datetime.now(timezone.utc),
    )


# ── Tasks ───────────────────────────────────────────────────────────────────


@router.post("/tasks", response_model=Task)
async def create_task(
    body: TaskCreate,
    _: bool = Depends(verify_api_key),
):
    _ensure_tasks_schema()
    if not body.assignee.strip():
        raise HTTPException(status_code=400, detail="assignee is required")
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    if body.location and body.location not in VALID_LOCATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"location must be one of {sorted(VALID_LOCATIONS)}",
        )
    if body.source not in {"voice", "web", "api"}:
        raise HTTPException(
            status_code=400,
            detail="source must be 'voice', 'web', or 'api'",
        )

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO deek_tasks
                    (assignee, content, source, location, created_by, due_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, assignee, content, status, source, location,
                          created_by, created_at, due_at, completed_at
                """,
                (
                    body.assignee.strip().lower(),
                    body.content.strip(),
                    body.source,
                    body.location,
                    body.created_by,
                    body.due_at,
                ),
            )
            row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return Task(
        id=row[0], assignee=row[1], content=row[2], status=row[3],
        source=row[4], location=row[5], created_by=row[6],
        created_at=row[7], due_at=row[8], completed_at=row[9],
    )


@router.get("/tasks", response_model=TaskList)
async def list_tasks(
    assignee: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    _: bool = Depends(verify_api_key),
):
    _ensure_tasks_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            clauses: list[str] = []
            params: list = []
            if assignee:
                clauses.append("assignee = %s")
                params.append(assignee.strip().lower())
            if status:
                clauses.append("status = %s")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            cur.execute(
                f"""
                SELECT id, assignee, content, status, source, location,
                       created_by, created_at, due_at, completed_at
                FROM deek_tasks
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return TaskList(
        tasks=[
            Task(
                id=r[0], assignee=r[1], content=r[2], status=r[3],
                source=r[4], location=r[5], created_by=r[6],
                created_at=r[7], due_at=r[8], completed_at=r[9],
            )
            for r in rows
        ]
    )


@router.patch("/tasks/{task_id}", response_model=Task)
async def patch_task(
    task_id: int,
    body: TaskPatch,
    _: bool = Depends(verify_api_key),
):
    _ensure_tasks_schema()
    if body.status and body.status not in {"open", "done", "cancelled"}:
        raise HTTPException(
            status_code=400,
            detail="status must be 'open', 'done', or 'cancelled'",
        )

    # Build the SET clause dynamically
    sets: list[str] = []
    params: list = []
    if body.status is not None:
        sets.append("status = %s")
        params.append(body.status)
        if body.status in {"done", "cancelled"}:
            sets.append("completed_at = NOW()")
        else:
            sets.append("completed_at = NULL")
    if body.content is not None:
        sets.append("content = %s")
        params.append(body.content.strip())
    if body.due_at is not None:
        sets.append("due_at = %s")
        params.append(body.due_at)
    if not sets:
        raise HTTPException(status_code=400, detail="no fields to update")

    params.append(task_id)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE deek_tasks
                SET {', '.join(sets)}
                WHERE id = %s
                RETURNING id, assignee, content, status, source, location,
                          created_by, created_at, due_at, completed_at
                """,
                params,
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            conn.commit()
    finally:
        conn.close()

    return Task(
        id=row[0], assignee=row[1], content=row[2], status=row[3],
        source=row[4], location=row[5], created_by=row[6],
        created_at=row[7], due_at=row[8], completed_at=row[9],
    )
