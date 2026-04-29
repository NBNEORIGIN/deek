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
from fastapi.responses import StreamingResponse
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
    source: str = "voice"          # voice | web | api | deek
    location: Optional[str] = None
    created_by: Optional[str] = None
    due_at: Optional[datetime] = None
    # PM extensions (optional — basic voice tasks skip these)
    title: Optional[str] = None
    priority: Optional[str] = None   # low | medium | high | critical
    context: Optional[str] = None
    linked_module: Optional[str] = None
    linked_ref: Optional[str] = None


class TaskPatch(BaseModel):
    status: Optional[str] = None    # open | done | cancelled
    content: Optional[str] = None
    due_at: Optional[datetime] = None
    title: Optional[str] = None
    priority: Optional[str] = None
    context: Optional[str] = None
    linked_module: Optional[str] = None
    linked_ref: Optional[str] = None


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
    title: Optional[str] = None
    priority: Optional[str] = None
    context: Optional[str] = None
    linked_module: Optional[str] = None
    linked_ref: Optional[str] = None


class TaskList(BaseModel):
    tasks: list[Task]


class VoiceChatRequest(BaseModel):
    content: str
    location: str                 # workshop | office | home
    session_id: Optional[str] = None  # auto-generated if absent
    user: Optional[str] = None    # "toby" | "jo" etc; logged in telemetry
    allow_tools: bool = False     # Phase 1: deny tool use in voice path (safety)


class VoiceChatResponse(BaseModel):
    response: str
    session_id: str
    model_used: str
    cost_usd: float
    latency_ms: int
    outcome: str                  # success | budget_trip | backend_error
    budget_remaining: Optional[float] = None


# ── DB helpers ──────────────────────────────────────────────────────────────


def _get_conn():
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")
    return psycopg2.connect(dsn, connect_timeout=5)


def _ensure_tasks_schema() -> None:
    """Create deek_tasks if it doesn't exist + defensive ALTERs for new columns.

    Safe to call repeatedly. Idempotent.
    """
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
            # ── PM extensions (v2 brief) ─────────────────────────
            cur.execute("ALTER TABLE deek_tasks ADD COLUMN IF NOT EXISTS title VARCHAR(200)")
            cur.execute("ALTER TABLE deek_tasks ADD COLUMN IF NOT EXISTS priority VARCHAR(10)")
            cur.execute("ALTER TABLE deek_tasks ADD COLUMN IF NOT EXISTS context TEXT")
            cur.execute("ALTER TABLE deek_tasks ADD COLUMN IF NOT EXISTS linked_module VARCHAR(40)")
            cur.execute("ALTER TABLE deek_tasks ADD COLUMN IF NOT EXISTS linked_ref VARCHAR(200)")

            # Task event log for observability
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deek_task_events (
                    id SERIAL PRIMARY KEY,
                    task_id INTEGER REFERENCES deek_tasks(id) ON DELETE CASCADE,
                    event VARCHAR(30) NOT NULL,
                    actor VARCHAR(200),
                    detail JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_deek_task_events_task ON deek_task_events(task_id, created_at DESC)"
            )
        conn.commit()
    finally:
        conn.close()


def _ensure_staff_schema() -> None:
    """Create deek_staff_profile and deek_pending_briefings. Idempotent."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deek_staff_profile (
                    email VARCHAR(200) PRIMARY KEY,
                    display_name VARCHAR(100),
                    role_tag VARCHAR(30),
                    briefings_enabled BOOLEAN NOT NULL DEFAULT true,
                    briefing_time TIME NOT NULL DEFAULT '07:30',
                    active_days VARCHAR(40) NOT NULL DEFAULT 'mon,tue,wed,thu,fri',
                    quiet_start TIME NOT NULL DEFAULT '22:00',
                    quiet_end TIME NOT NULL DEFAULT '06:30',
                    preferred_voice VARCHAR(200),
                    preferred_face VARCHAR(20),
                    notes TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deek_pending_briefings (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(200) NOT NULL,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    briefing_md TEXT NOT NULL,
                    seen_at TIMESTAMPTZ,
                    dismissed_at TIMESTAMPTZ,
                    incorrect_reason TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_briefings_email ON deek_pending_briefings(email, generated_at DESC)"
            )
        conn.commit()
    finally:
        conn.close()


def _log_task_event(task_id: int, event: str, actor: str | None, detail: dict | None = None) -> None:
    """Best-effort task-event logging. Never raises."""
    import json as _json
    try:
        conn = _get_conn()
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO deek_task_events (task_id, event, actor, detail)
                VALUES (%s, %s, %s, %s)
                """,
                (task_id, event, actor, _json.dumps(detail) if detail else None),
            )
        conn.commit()
    except Exception:
        pass
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


def _row_to_task(row: tuple) -> "Task":
    """Map the canonical SELECT column order to a Task model."""
    return Task(
        id=row[0], assignee=row[1], content=row[2], status=row[3],
        source=row[4], location=row[5], created_by=row[6],
        created_at=row[7], due_at=row[8], completed_at=row[9],
        title=row[10] if len(row) > 10 else None,
        priority=row[11] if len(row) > 11 else None,
        context=row[12] if len(row) > 12 else None,
        linked_module=row[13] if len(row) > 13 else None,
        linked_ref=row[14] if len(row) > 14 else None,
    )


_TASK_SELECT_COLS = (
    "id, assignee, content, status, source, location, created_by, "
    "created_at, due_at, completed_at, title, priority, context, "
    "linked_module, linked_ref"
)


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
    if body.source not in {"voice", "web", "api", "deek"}:
        raise HTTPException(
            status_code=400,
            detail="source must be 'voice', 'web', 'api', or 'deek'",
        )
    if body.priority and body.priority not in {"low", "medium", "high", "critical"}:
        raise HTTPException(
            status_code=400,
            detail="priority must be 'low', 'medium', 'high', or 'critical'",
        )

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO deek_tasks
                    (assignee, content, source, location, created_by, due_at,
                     title, priority, context, linked_module, linked_ref)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, assignee, content, status, source, location,
                          created_by, created_at, due_at, completed_at,
                          title, priority, context, linked_module, linked_ref
                """,
                (
                    body.assignee.strip().lower(),
                    body.content.strip(),
                    body.source,
                    body.location,
                    body.created_by,
                    body.due_at,
                    body.title,
                    body.priority,
                    body.context,
                    body.linked_module,
                    body.linked_ref,
                ),
            )
            row = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    _log_task_event(
        task_id=row[0],
        event="created",
        actor=body.created_by or "unknown",
        detail={"source": body.source, "priority": body.priority, "linked": f"{body.linked_module}:{body.linked_ref}" if body.linked_module else None},
    )

    return _row_to_task(row)


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
                SELECT {_TASK_SELECT_COLS}
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

    return TaskList(tasks=[_row_to_task(r) for r in rows])


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
    if body.priority and body.priority not in {"low", "medium", "high", "critical"}:
        raise HTTPException(
            status_code=400,
            detail="priority must be 'low', 'medium', 'high', or 'critical'",
        )

    # Build the SET clause dynamically
    sets: list[str] = []
    params: list = []
    status_change = None
    if body.status is not None:
        sets.append("status = %s")
        params.append(body.status)
        status_change = body.status
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
    if body.title is not None:
        sets.append("title = %s")
        params.append(body.title)
    if body.priority is not None:
        sets.append("priority = %s")
        params.append(body.priority)
    if body.context is not None:
        sets.append("context = %s")
        params.append(body.context)
    if body.linked_module is not None:
        sets.append("linked_module = %s")
        params.append(body.linked_module)
    if body.linked_ref is not None:
        sets.append("linked_ref = %s")
        params.append(body.linked_ref)
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
                RETURNING {_TASK_SELECT_COLS}
                """,
                params,
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="task not found")
            conn.commit()
    finally:
        conn.close()

    if status_change:
        _log_task_event(
            task_id=task_id,
            event=f"status_{status_change}",
            actor=None,
            detail={"status": status_change},
        )
    return _row_to_task(row)


# ── Voice chat ──────────────────────────────────────────────────────────────


# Voice-specific behavioural rules. These append to the unified
# identity prefix (NOT replace it). Identity is in DEEK_IDENTITY.md —
# loaded by core.identity.assembler. These rules only describe the
# TTS-specific constraints; everything else comes from identity.
#
# Brief 1a.2 Task 1: a single call site (core.identity.assembler.
# get_system_prompt_prefix) is the source of truth across ALL paths.
# The previous VOICE_SYSTEM_PROMPT constant (which contained its own
# "I don't have that information" instruction directly at odds with
# the identity layer's behavioural directive) is deleted.
VOICE_TTS_RULES = (
    "\n## Voice-mode output constraints\n"
    "Your response will be read aloud by text-to-speech, so:\n"
    "- Keep it UNDER 60 words.\n"
    "- Use short sentences. No markdown, no code blocks, no bullets.\n"
    "- No tool calls on this path — rely on the identity above + the live\n"
    "  context block below.\n"
    "- Copy figures EXACTLY from context. Never round or rephrase numbers;\n"
    "  TTS pronounces '£28,275' correctly.\n"
    "- If the user's question is about LIVE DATA from a module and that\n"
    "  module is unreachable per the identity above, say so and name the\n"
    "  module. For questions about Deek's own capabilities, NBNE, the\n"
    "  team, modules, LLMs, marketplaces, or email — answer from the\n"
    "  identity above; do NOT default to 'I don't have that information'.\n"
    "- Location prioritisation: WORKSHOP → production + machine data;\n"
    "  OFFICE → CRM + email + client; HOME → softer tone, financial\n"
    "  + high-level summary.\n"
)


def _build_voice_system_prompt(location: str) -> str:
    """Return the unified identity prefix + voice-specific rules.

    Delegates identity content to core.identity.assembler — the SAME
    function core/agent.py uses for chat. This is the Brief 1a.2 Task 1
    deliverable: one call site, zero divergence between paths.
    """
    from core.identity import assembler as _ia, probe as _ip
    identity_prefix = _ia.get_system_prompt_prefix(
        reachable=_ip.get_reachable_modules(),
        errors=_ip.get_errors(),
    )
    return identity_prefix + VOICE_TTS_RULES


def _ensure_voice_telemetry_schema() -> None:
    """Create deek_voice_sessions if it doesn't exist. Safe to call repeatedly."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS deek_voice_sessions (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(100) NOT NULL,
                    user_label VARCHAR(100),
                    location VARCHAR(20),
                    question TEXT NOT NULL,
                    response TEXT,
                    model_used VARCHAR(100),
                    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    outcome VARCHAR(30) NOT NULL DEFAULT 'success',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_deek_voice_sessions_created "
                "ON deek_voice_sessions(created_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_deek_voice_sessions_session "
                "ON deek_voice_sessions(session_id, created_at DESC)"
            )
        conn.commit()
    finally:
        conn.close()


def _voice_daily_spend_gbp() -> float:
    """Sum cost_usd over voice sessions from the last 24h, ~0.80 GBP/USD."""
    _ensure_voice_telemetry_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM deek_voice_sessions
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                """
            )
            usd = float(cur.fetchone()[0] or 0)
    finally:
        conn.close()
    return usd * 0.80


def _voice_daily_count() -> int:
    _ensure_voice_telemetry_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM deek_voice_sessions
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                """
            )
            return int(cur.fetchone()[0] or 0)
    finally:
        conn.close()


def _log_voice_telemetry(
    session_id: str, user_label: Optional[str], location: Optional[str],
    question: str, response: Optional[str], model_used: Optional[str],
    cost_usd: float, latency_ms: int, outcome: str,
) -> None:
    _ensure_voice_telemetry_schema()
    try:
        conn = _get_conn()
    except Exception:
        return  # telemetry failure must never break the voice path
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO deek_voice_sessions
                    (session_id, user_label, location, question, response,
                     model_used, cost_usd, latency_ms, outcome)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (session_id, user_label, location, question, response,
                 model_used, cost_usd, latency_ms, outcome),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _strip_markdown_for_tts(text: str) -> str:
    """Remove markdown so SpeechSynthesis reads cleanly."""
    text = re.sub(r"```[^`]*```", "", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@router.post("/chat/voice", response_model=VoiceChatResponse)
async def chat_voice(
    body: VoiceChatRequest,
    _: bool = Depends(verify_api_key),
):
    """Voice-optimised chat turn.

    - Forces tier-1 routing with qwen2.5:7b-instruct so responses are fast
      and don't compete with Qwen-coder-32B for VRAM.
    - Enforces a daily budget via DEEK_VOICE_DAILY_LIMIT and
      DEEK_VOICE_DAILY_COST_GBP. Tripped budget returns a canned message.
    - Logs every turn to deek_voice_sessions for telemetry.
    """
    import time
    import uuid

    if not body.content or not body.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    if body.location not in VALID_LOCATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"location must be one of {sorted(VALID_LOCATIONS)}",
        )

    session_id = body.session_id or f"voice-{uuid.uuid4().hex[:12]}"
    question = body.content.strip()

    # ── Budget check ──────────────────────────────────────────────────
    # Count is the PRIMARY defence (fires even for zero-cost local turns).
    # £ cap is secondary — meaningful only if we ever fall back to the
    # paid Claude/DeepSeek path. For local-only voice, spent_gbp stays 0
    # and only the count ever trips.
    daily_limit = int(os.getenv("DEEK_VOICE_DAILY_LIMIT", "200"))
    daily_cost_cap_gbp = float(os.getenv("DEEK_VOICE_DAILY_COST_GBP", "0.50"))
    count_today = _voice_daily_count()
    spent_gbp = _voice_daily_spend_gbp()
    if count_today >= daily_limit or spent_gbp >= daily_cost_cap_gbp:
        trip_reason = (
            f"count {count_today}/{daily_limit}"
            if count_today >= daily_limit
            else f"spend £{spent_gbp:.2f}/£{daily_cost_cap_gbp:.2f}"
        )
        canned = (
            "Deek is thinking less today. The daily voice budget has been "
            "reached. I'll be back tomorrow."
        )
        _log_voice_telemetry(
            session_id=session_id, user_label=body.user, location=body.location,
            question=question, response=f"{canned} ({trip_reason})",
            model_used=None,
            cost_usd=0.0, latency_ms=0, outcome="budget_trip",
        )
        return VoiceChatResponse(
            response=canned, session_id=session_id,
            model_used="", cost_usd=0.0, latency_ms=0,
            outcome="budget_trip",
            budget_remaining=max(0.0, daily_cost_cap_gbp - spent_gbp),
        )

    # ── Direct Ollama call with pre-loaded ambient context ────────────
    #
    # We intentionally BYPASS the agent pipeline for voice. Rationale:
    # 1. The agent picks OLLAMA_MODEL_PREFERRED (qwen2.5-coder:32b) which
    #    has 33s cold-load latency — unacceptable for voice UX.
    # 2. The voice path uses max_tool_rounds=0 anyway, so the agent's
    #    tool-routing machinery adds cost without value.
    # 3. Directly calling qwen2.5:7b-instruct (always-warm) gives us
    #    sub-5s responses with good enough quality for conversational Q&A.
    #
    # Context pre-loading: we fetch the location's relevant federation
    # snapshot(s) and paste them into the system prompt. The model
    # doesn't need tools because the answer is usually already in
    # 2-3 KB of live business state.

    voice_model = os.getenv(
        "OLLAMA_VOICE_MODEL",
        os.getenv("OLLAMA_CLASSIFIER_MODEL", "qwen2.5:7b-instruct"),
    )
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

    # Pull the relevant snapshot(s) for this location
    ctx_blocks: list[str] = []
    snapshot_modules = {
        "workshop": ["manufacture"],
        "office": ["crm"],
        "home": ["ledger", "crm"],
    }
    for mod in snapshot_modules.get(body.location, []):
        md, _ts = _load_snapshot(mod)
        if md:
            ctx_blocks.append(f"=== {mod.upper()} SNAPSHOT ===\n{md[:2500]}")

    context_section = "\n\n".join(ctx_blocks) if ctx_blocks else "(no live snapshots available)"

    # Brief 1a.2 Task 1: unified system prompt. The previous
    # "answer using ONLY the context above, else say I don't have
    # that information" clause is deliberately removed — it conflicts
    # with the identity layer's answering-self-referential-questions
    # directive. The voice TTS rules (in VOICE_TTS_RULES) handle the
    # "say so explicitly" behaviour correctly.
    system_prompt = (
        _build_voice_system_prompt(body.location)
        + f"\n\n## Live business context (location: {body.location})\n"
        + context_section
        + "\n\nFor questions about LIVE BUSINESS DATA (orders, revenue,"
          " pipeline, today's numbers), answer from the context above."
          " For questions about Deek's own capabilities or NBNE itself,"
          " answer from the identity block at the top."
    )

    t0 = time.monotonic()
    try:
        import httpx
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{ollama_base}/api/chat",
                json={
                    "model": voice_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question},
                    ],
                    "stream": False,
                    "options": {
                        "num_predict": 150,
                        "temperature": 0.2,
                    },
                },
            )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        err_text = (
            "Sorry, something went wrong reaching the brain. "
            "Try again in a moment."
        )
        _log_voice_telemetry(
            session_id=session_id, user_label=body.user, location=body.location,
            question=question, response=f"{type(exc).__name__}: {exc}",
            model_used=voice_model, cost_usd=0.0, latency_ms=latency_ms,
            outcome="backend_error",
        )
        return VoiceChatResponse(
            response=err_text, session_id=session_id,
            model_used="", cost_usd=0.0, latency_ms=latency_ms,
            outcome="backend_error",
            budget_remaining=max(0.0, daily_cost_cap_gbp - spent_gbp),
        )

    latency_ms = int((time.monotonic() - t0) * 1000)

    if r.status_code != 200:
        err_text = f"Deek returned HTTP {r.status_code}."
        _log_voice_telemetry(
            session_id=session_id, user_label=body.user, location=body.location,
            question=question, response=err_text, model_used=voice_model,
            cost_usd=0.0, latency_ms=latency_ms, outcome="backend_error",
        )
        return VoiceChatResponse(
            response=err_text, session_id=session_id,
            model_used=voice_model, cost_usd=0.0, latency_ms=latency_ms,
            outcome="backend_error",
            budget_remaining=max(0.0, daily_cost_cap_gbp - spent_gbp),
        )

    try:
        raw = (r.json().get("message", {}) or {}).get("content", "").strip()
    except Exception:
        raw = ""

    reply = _strip_markdown_for_tts(raw) or "Sorry, I didn't get an answer."

    _log_voice_telemetry(
        session_id=session_id, user_label=body.user, location=body.location,
        question=question, response=reply, model_used=voice_model,
        cost_usd=0.0, latency_ms=latency_ms, outcome="success",
    )

    # Brief 1a.2 Phase B — audit log every model response for later
    # divergence analysis. Fire-and-forget so the hot path isn't blocked.
    try:
        from core.memory.response_audit import log_async, ResponseAuditRow
        log_async(ResponseAuditRow(
            path='voice',
            system_prompt=system_prompt,
            response_text=reply,
            session_id=session_id,
            model=voice_model,
            user_question=question,
            latency_ms=latency_ms,
        ))
    except Exception:
        pass

    return VoiceChatResponse(
        response=reply,
        session_id=session_id,
        model_used=voice_model,
        cost_usd=0.0,
        latency_ms=latency_ms,
        outcome="success",
        budget_remaining=max(0.0, daily_cost_cap_gbp - spent_gbp),
    )


# ── Voice chat — STREAMING variant ───────────────────────────────────────


def _build_voice_context(location: str) -> tuple[str, str]:
    """Return (voice_model, system_prompt) with ambient context pre-loaded."""
    voice_model = os.getenv(
        "OLLAMA_VOICE_MODEL",
        os.getenv("OLLAMA_CLASSIFIER_MODEL", "qwen2.5:7b-instruct"),
    )
    ctx_blocks: list[str] = []
    snapshot_modules = {
        "workshop": ["manufacture"],
        "office": ["crm"],
        "home": ["ledger", "crm"],
    }
    for mod in snapshot_modules.get(location, []):
        md, _ts = _load_snapshot(mod)
        if md:
            ctx_blocks.append(f"=== {mod.upper()} SNAPSHOT ===\n{md[:2500]}")

    context_section = "\n\n".join(ctx_blocks) if ctx_blocks else "(no live snapshots available)"
    # Brief 1a.2 Task 1 — unified system prompt. See the non-streaming
    # endpoint above for the full rationale.
    system_prompt = (
        _build_voice_system_prompt(location)
        + f"\n\n## Live business context (location: {location})\n"
        + context_section
        + "\n\nFor questions about LIVE BUSINESS DATA (orders, revenue,"
          " pipeline, today's numbers), answer from the context above."
          " For questions about Deek's own capabilities or NBNE itself,"
          " answer from the identity block at the top."
    )
    return voice_model, system_prompt


@router.post("/chat/voice/stream")
async def chat_voice_stream(
    body: VoiceChatRequest,
    _: bool = Depends(verify_api_key),
):
    """Streaming variant of /chat/voice — yields SSE events:

        event: response_delta   data: {"text": "chunk"}
        event: done             data: {"session_id", "model_used", "latency_ms", "outcome", "cost_usd"}
        event: error            data: {"error": "..."}

    Identical auth / budget / telemetry logic to the non-streaming endpoint;
    only the model call differs (stream=True, chunked yields).
    """
    import json as _json
    import time
    import uuid

    if not body.content or not body.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    if body.location not in VALID_LOCATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"location must be one of {sorted(VALID_LOCATIONS)}",
        )

    session_id = body.session_id or f"voice-{uuid.uuid4().hex[:12]}"
    question = body.content.strip()

    # Budget check — same logic as non-streaming endpoint
    daily_limit = int(os.getenv("DEEK_VOICE_DAILY_LIMIT", "200"))
    daily_cost_cap_gbp = float(os.getenv("DEEK_VOICE_DAILY_COST_GBP", "0.50"))
    count_today = _voice_daily_count()
    spent_gbp = _voice_daily_spend_gbp()

    voice_model, system_prompt = _build_voice_context(body.location)
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

    async def event_gen():
        if count_today >= daily_limit or spent_gbp >= daily_cost_cap_gbp:
            canned = (
                "Deek is thinking less today. The daily voice budget has been "
                "reached. I'll be back tomorrow."
            )
            _log_voice_telemetry(
                session_id=session_id, user_label=body.user, location=body.location,
                question=question, response=canned, model_used=None,
                cost_usd=0.0, latency_ms=0, outcome="budget_trip",
            )
            yield f"event: response_delta\ndata: {_json.dumps({'text': canned})}\n\n"
            yield f"event: done\ndata: {_json.dumps({'session_id': session_id, 'model_used': '', 'latency_ms': 0, 'outcome': 'budget_trip', 'cost_usd': 0.0})}\n\n"
            return

        t0 = time.monotonic()
        collected = []
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    f"{ollama_base}/api/chat",
                    json={
                        "model": voice_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": question},
                        ],
                        "stream": True,
                        "options": {
                            "num_predict": 200,
                            "temperature": 0.2,
                        },
                    },
                ) as r:
                    if r.status_code != 200:
                        err = f"Ollama HTTP {r.status_code}"
                        yield f"event: error\ndata: {_json.dumps({'error': err})}\n\n"
                        latency_ms = int((time.monotonic() - t0) * 1000)
                        _log_voice_telemetry(
                            session_id=session_id, user_label=body.user,
                            location=body.location, question=question,
                            response=err, model_used=voice_model, cost_usd=0.0,
                            latency_ms=latency_ms, outcome="backend_error",
                        )
                        return

                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = _json.loads(line)
                        except Exception:
                            continue
                        # Ollama streaming chunks: {"message": {"content": "chunk"}, "done": false}
                        msg = obj.get("message") or {}
                        delta = msg.get("content") or ""
                        if delta:
                            collected.append(delta)
                            yield f"event: response_delta\ndata: {_json.dumps({'text': delta})}\n\n"
                        if obj.get("done"):
                            break
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            yield f"event: error\ndata: {_json.dumps({'error': err})}\n\n"
            latency_ms = int((time.monotonic() - t0) * 1000)
            _log_voice_telemetry(
                session_id=session_id, user_label=body.user, location=body.location,
                question=question, response=err, model_used=voice_model,
                cost_usd=0.0, latency_ms=latency_ms, outcome="backend_error",
            )
            return

        latency_ms = int((time.monotonic() - t0) * 1000)
        full_reply = _strip_markdown_for_tts("".join(collected).strip()) \
            or "Sorry, I didn't get an answer."

        _log_voice_telemetry(
            session_id=session_id, user_label=body.user, location=body.location,
            question=question, response=full_reply, model_used=voice_model,
            cost_usd=0.0, latency_ms=latency_ms, outcome="success",
        )

        # Brief 1a.2 Phase B — audit log. Same row shape as the
        # non-streaming path; fire-and-forget.
        try:
            from core.memory.response_audit import log_async, ResponseAuditRow
            log_async(ResponseAuditRow(
                path='voice_stream',
                system_prompt=system_prompt,
                response_text=full_reply,
                session_id=session_id,
                model=voice_model,
                user_question=question,
                latency_ms=latency_ms,
            ))
        except Exception:
            pass

        yield f"event: done\ndata: {_json.dumps({'session_id': session_id, 'model_used': voice_model, 'latency_ms': latency_ms, 'outcome': 'success', 'cost_usd': 0.0})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Voice session history ───────────────────────────────────────────────────


class VoiceTurn(BaseModel):
    role: str                    # "user" | "deek"
    text: str
    at: datetime
    outcome: Optional[str] = None
    model_used: Optional[str] = None
    latency_ms: Optional[int] = None


class VoiceSessionsResponse(BaseModel):
    turns: list[VoiceTurn]


@router.get("/voice/sessions", response_model=VoiceSessionsResponse)
async def voice_sessions(
    session_id: Optional[str] = Query(None),
    user: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    _: bool = Depends(verify_api_key),
):
    """Return recent voice turns for hydration on PWA load.

    Prefers session_id when given (continuity of a single conversation).
    Falls back to ``user`` for cross-device history ("tell me more about
    that" from a different Pi). Returns newest last so the client can
    append without reordering.
    """
    # Short-circuit when neither selector is given — avoids a DB roundtrip
    # and also means this test path works without a live Postgres.
    if not session_id and not user:
        return VoiceSessionsResponse(turns=[])

    _ensure_voice_telemetry_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            clauses: list[str] = []
            params: list = []
            if session_id:
                clauses.append("session_id = %s")
                params.append(session_id)
            elif user:
                clauses.append("user_label = %s")
                params.append(user)
                # 10-minute window for cross-device continuity
                clauses.append(
                    "created_at >= NOW() - INTERVAL '10 minutes'"
                )
            where = f"WHERE {' AND '.join(clauses)}"
            params.append(limit)
            cur.execute(
                f"""
                SELECT question, response, outcome, model_used,
                       latency_ms, created_at
                FROM deek_voice_sessions
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = list(reversed(cur.fetchall()))
    finally:
        conn.close()

    turns: list[VoiceTurn] = []
    for question, response, outcome, model_used, latency_ms, created_at in rows:
        turns.append(VoiceTurn(role="user", text=question, at=created_at))
        if response:
            turns.append(VoiceTurn(
                role="deek", text=response, at=created_at,
                outcome=outcome, model_used=model_used, latency_ms=latency_ms,
            ))
    return VoiceSessionsResponse(turns=turns)


# ── Voice sessions LIST — chat-history sidebar ─────────────────────────────


class VoiceSessionSummary(BaseModel):
    session_id: str
    title: str
    last_at: datetime
    turn_count: int


class VoiceSessionsListResponse(BaseModel):
    sessions: list[VoiceSessionSummary]


@router.get("/voice/sessions/list", response_model=VoiceSessionsListResponse)
async def voice_sessions_list(
    user: str = Query(..., min_length=3, description="user_label / email"),
    limit: int = Query(30, ge=1, le=100),
    _: bool = Depends(verify_api_key),
):
    """Return distinct sessions for `user`, ordered most-recent-first.

    For the ChatGPT-style left sidebar — one row per past conversation,
    titled by the first user message in that session (truncated). Excludes
    sessions older than 60 days so the list stays manageable.
    """
    _ensure_voice_telemetry_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        session_id,
                        question,
                        created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY session_id
                            ORDER BY created_at ASC
                        ) AS rn,
                        COUNT(*) OVER (PARTITION BY session_id) AS turn_count,
                        MAX(created_at) OVER (PARTITION BY session_id) AS last_at
                    FROM deek_voice_sessions
                    WHERE user_label = %s
                      AND created_at >= NOW() - INTERVAL '60 days'
                )
                SELECT session_id, question AS title, last_at, turn_count
                FROM ranked
                WHERE rn = 1
                ORDER BY last_at DESC
                LIMIT %s
                """,
                (user, limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    sessions: list[VoiceSessionSummary] = []
    for session_id, title, last_at, turn_count in rows:
        # Truncate the title to a manageable sidebar width
        clean = (title or '').strip().replace('\n', ' ')
        if len(clean) > 60:
            clean = clean[:57] + '…'
        sessions.append(VoiceSessionSummary(
            session_id=session_id,
            title=clean or '(untitled)',
            last_at=last_at,
            turn_count=int(turn_count),
        ))
    return VoiceSessionsListResponse(sessions=sessions)


# ── Voice metrics (telemetry dashboard) ────────────────────────────────────


class VoiceMetricsOutcome(BaseModel):
    outcome: str
    count: int


class VoiceMetricsDaily(BaseModel):
    day: str                         # YYYY-MM-DD
    count: int
    cost_usd: float
    avg_latency_ms: float


class VoiceMetricsResponse(BaseModel):
    count_24h: int
    count_7d: int
    cost_usd_24h: float
    cost_usd_7d: float
    avg_latency_ms_24h: float
    outcomes_24h: list[VoiceMetricsOutcome]
    by_location_24h: list[dict]      # [{location, count}]
    by_day_7d: list[VoiceMetricsDaily]
    recent_turns: list[dict]         # [{session_id, user, location, question,
                                     #   response, outcome, model_used,
                                     #   latency_ms, created_at}] last 20
    budget_limit: int
    budget_cost_cap_gbp: float


@router.get("/voice/metrics", response_model=VoiceMetricsResponse)
async def voice_metrics(
    _: bool = Depends(verify_api_key),
):
    """Aggregations over deek_voice_sessions for the /admin/voice-metrics UI."""
    _ensure_voice_telemetry_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # Totals
            cur.execute(
                """
                SELECT
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
                  COALESCE(SUM(cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'), 0),
                  COALESCE(SUM(cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'), 0),
                  COALESCE(AVG(latency_ms) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND outcome = 'success'), 0)
                FROM deek_voice_sessions
                """
            )
            r = cur.fetchone() or (0, 0, 0, 0, 0)
            count_24h = int(r[0] or 0)
            count_7d = int(r[1] or 0)
            cost_24h = float(r[2] or 0)
            cost_7d = float(r[3] or 0)
            avg_latency_24h = float(r[4] or 0)

            # Outcomes breakdown (24h)
            cur.execute(
                """
                SELECT outcome, COUNT(*)
                FROM deek_voice_sessions
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY outcome
                ORDER BY COUNT(*) DESC
                """
            )
            outcomes = [
                VoiceMetricsOutcome(outcome=row[0] or "unknown", count=int(row[1]))
                for row in cur.fetchall()
            ]

            # By location (24h)
            cur.execute(
                """
                SELECT location, COUNT(*)
                FROM deek_voice_sessions
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY location
                ORDER BY COUNT(*) DESC
                """
            )
            by_loc = [
                {"location": row[0] or "unknown", "count": int(row[1])}
                for row in cur.fetchall()
            ]

            # By day (last 7)
            cur.execute(
                """
                SELECT
                  to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                  COUNT(*) AS count,
                  COALESCE(SUM(cost_usd), 0) AS cost_usd,
                  COALESCE(AVG(latency_ms) FILTER (WHERE outcome = 'success'), 0) AS avg_latency
                FROM deek_voice_sessions
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY day
                ORDER BY day ASC
                """
            )
            by_day = [
                VoiceMetricsDaily(
                    day=row[0], count=int(row[1]),
                    cost_usd=float(row[2]),
                    avg_latency_ms=float(row[3]),
                )
                for row in cur.fetchall()
            ]

            # Recent 20 turns
            cur.execute(
                """
                SELECT session_id, user_label, location, question, response,
                       outcome, model_used, latency_ms, created_at
                FROM deek_voice_sessions
                ORDER BY created_at DESC
                LIMIT 20
                """
            )
            recent = [
                {
                    "session_id": row[0],
                    "user": row[1],
                    "location": row[2],
                    "question": row[3],
                    "response": row[4],
                    "outcome": row[5],
                    "model_used": row[6],
                    "latency_ms": row[7],
                    "created_at": row[8].isoformat() if row[8] else None,
                }
                for row in cur.fetchall()
            ]
    finally:
        conn.close()

    return VoiceMetricsResponse(
        count_24h=count_24h, count_7d=count_7d,
        cost_usd_24h=cost_24h, cost_usd_7d=cost_7d,
        avg_latency_ms_24h=avg_latency_24h,
        outcomes_24h=outcomes,
        by_location_24h=by_loc,
        by_day_7d=by_day,
        recent_turns=recent,
        budget_limit=int(os.getenv("DEEK_VOICE_DAILY_LIMIT", "200")),
        budget_cost_cap_gbp=float(os.getenv("DEEK_VOICE_DAILY_COST_GBP", "0.50")),
    )


# ── Commit transcript to wiki ───────────────────────────────────────────────


class CommitRequest(BaseModel):
    session_id: str
    max_turns: int = 40
    title_hint: Optional[str] = None    # optional user-provided title


class CommitResponse(BaseModel):
    ok: bool
    wiki_path: str
    title: str
    slug: str
    turn_count: int
    sync_result: dict = Field(default_factory=dict)


_COMMIT_SUMMARISER_SYSTEM = (
    "You are distilling a Q&A voice conversation into a concise wiki article. "
    "Output STRICT JSON only, no prose:\n"
    "{\n"
    '  "title": "short 4-8 word title describing the topic",\n'
    '  "slug": "kebab-case-slug-from-title (no date, no spaces)",\n'
    '  "summary": "1-2 sentence summary of the key facts or decision",\n'
    '  "body_md": "markdown body with bullet points of facts exchanged, quoting key figures exactly. Under 300 words.",\n'
    '  "tags": ["topic1", "topic2"]\n'
    "}\n"
    "Copy figures and names exactly as they appear in the conversation. "
    "If the conversation was trivial (e.g. greetings only), set title to "
    "'Trivial exchange' and body_md to a one-line note."
)


def _slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] or "untitled"


@router.post("/voice/commit", response_model=CommitResponse)
async def voice_commit(
    body: CommitRequest,
    _: bool = Depends(verify_api_key),
):
    """Distill a voice session into a wiki article and trigger embedding.

    Pipeline:
      1. Load up to N recent turns for this session_id from deek_voice_sessions
      2. Ask the local voice model (Qwen 7B) to emit a JSON article
      3. Write the markdown to wiki/modules/voice-{slug}-{date}.md
         (mounted from /opt/nbne/deek/wiki on the host)
      4. Trigger /admin/wiki-sync to embed into claw_code_chunks so it's
         findable via search_wiki
    """
    import json as _json
    from datetime import date
    import httpx

    _ensure_voice_telemetry_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT question, response, created_at, location
                FROM deek_voice_sessions
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (body.session_id, body.max_turns),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No turns found for session_id={body.session_id}",
        )

    transcript_md = []
    location_seen: Optional[str] = None
    for question, response, created_at, location in rows:
        if location and not location_seen:
            location_seen = location
        transcript_md.append(f"**User:** {question}")
        if response:
            transcript_md.append(f"**Deek:** {response}")
        transcript_md.append("")
    transcript_text = "\n".join(transcript_md)

    # Ask the local model for a structured summary
    voice_model = os.getenv(
        "OLLAMA_VOICE_MODEL",
        os.getenv("OLLAMA_CLASSIFIER_MODEL", "qwen2.5:7b-instruct"),
    )
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

    user_prompt = (
        (f"Title hint (optional): {body.title_hint}\n\n" if body.title_hint else "")
        + f"Location context: {location_seen or 'unknown'}\n\n"
        + f"Conversation:\n\n{transcript_text}\n\n"
        + "Return the JSON article now."
    )

    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{ollama_base}/api/chat",
                json={
                    "model": voice_model,
                    "messages": [
                        {"role": "system", "content": _COMMIT_SUMMARISER_SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"num_predict": 800, "temperature": 0.1},
                },
            )
    except Exception as exc:
        raise HTTPException(502, f"summariser failed: {type(exc).__name__}: {exc}")
    if r.status_code != 200:
        raise HTTPException(502, f"summariser HTTP {r.status_code}: {r.text[:200]}")

    raw = (r.json().get("message", {}) or {}).get("content", "").strip()
    try:
        parsed = _json.loads(raw)
    except Exception:
        # Fallback extraction if the model wrapped JSON in prose
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise HTTPException(502, f"summariser output could not be parsed: {raw[:200]}")
        parsed = _json.loads(match.group(0))

    title = (parsed.get("title") or "Deek voice session").strip()[:100]
    slug_part = body.title_hint or parsed.get("slug") or title
    slug = _slugify(slug_part)
    summary = (parsed.get("summary") or "").strip()
    body_md = (parsed.get("body_md") or "").strip()
    tags = parsed.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    date_str = date.today().isoformat()
    filename = f"voice-{slug}-{date_str}.md"
    wiki_dir = Path(os.getenv("DEEK_WIKI_DIR") or os.getenv("CAIRN_WIKI_DIR") or "/app/wiki").resolve()
    modules_dir = wiki_dir / "modules"
    try:
        modules_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise HTTPException(500, f"wiki directory not writable: {exc}")

    wiki_path = modules_dir / filename
    tags_line = ", ".join(tags) if tags else ""
    article = (
        f"# {title}\n\n"
        f"_Committed from Deek voice session on {date_str}. "
        f"Session: `{body.session_id}`"
        f"{', location: ' + location_seen if location_seen else ''}_\n\n"
        f"{('**Tags:** ' + tags_line + chr(10) + chr(10)) if tags_line else ''}"
        f"## Summary\n\n{summary}\n\n"
        f"## Detail\n\n{body_md}\n\n"
        f"---\n\n"
        f"## Full transcript\n\n{transcript_text}\n"
    )
    try:
        wiki_path.write_text(article, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"failed to write wiki article: {exc}")

    # Trigger wiki-sync for embedding. Best-effort; the file is on disk
    # even if embedding fails (cron will pick it up next cycle).
    sync_result: dict = {}
    try:
        api_key = (
            os.getenv("DEEK_API_KEY")
            or os.getenv("CAIRN_API_KEY")
            or os.getenv("CLAW_API_KEY", "")
        )
        with httpx.Client(timeout=30.0) as client:
            sr = client.post(
                "http://localhost:8765/admin/wiki-sync",
                headers={"X-API-Key": api_key},
            )
            if sr.status_code == 200:
                sync_result = sr.json()
            else:
                sync_result = {"error": f"HTTP {sr.status_code}"}
    except Exception as exc:
        sync_result = {"error": f"{type(exc).__name__}: {exc}"}

    return CommitResponse(
        ok=True,
        wiki_path=str(wiki_path),
        title=title,
        slug=slug,
        turn_count=len(rows),
        sync_result=sync_result,
    )


# ── Staff profiles ──────────────────────────────────────────────────────────


VALID_ROLE_TAGS = {"production", "dispatch", "tech", "admin", "director"}


class StaffProfile(BaseModel):
    email: str
    display_name: Optional[str] = None
    role_tag: Optional[str] = None
    briefings_enabled: bool = True
    briefing_time: str = "07:30"        # HH:MM
    active_days: str = "mon,tue,wed,thu,fri"
    quiet_start: str = "22:00"
    quiet_end: str = "06:30"
    preferred_voice: Optional[str] = None
    preferred_face: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StaffProfileList(BaseModel):
    profiles: list[StaffProfile]


class StaffProfileUpsert(BaseModel):
    email: str
    display_name: Optional[str] = None
    role_tag: Optional[str] = None
    briefings_enabled: Optional[bool] = None
    briefing_time: Optional[str] = None
    active_days: Optional[str] = None
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    preferred_voice: Optional[str] = None
    preferred_face: Optional[str] = None
    notes: Optional[str] = None


def _row_to_staff(r: tuple) -> StaffProfile:
    return StaffProfile(
        email=r[0], display_name=r[1], role_tag=r[2],
        briefings_enabled=bool(r[3]),
        briefing_time=r[4].strftime("%H:%M") if r[4] else "07:30",
        active_days=r[5] or "mon,tue,wed,thu,fri",
        quiet_start=r[6].strftime("%H:%M") if r[6] else "22:00",
        quiet_end=r[7].strftime("%H:%M") if r[7] else "06:30",
        preferred_voice=r[8], preferred_face=r[9], notes=r[10],
        created_at=r[11], updated_at=r[12],
    )


_STAFF_SELECT_COLS = (
    "email, display_name, role_tag, briefings_enabled, briefing_time, "
    "active_days, quiet_start, quiet_end, preferred_voice, preferred_face, "
    "notes, created_at, updated_at"
)


@router.get("/staff", response_model=StaffProfileList)
async def list_staff(_: bool = Depends(verify_api_key)):
    _ensure_staff_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STAFF_SELECT_COLS} FROM deek_staff_profile "
                "ORDER BY role_tag, email"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return StaffProfileList(profiles=[_row_to_staff(r) for r in rows])


@router.get("/staff/{email}", response_model=StaffProfile)
async def get_staff(email: str, _: bool = Depends(verify_api_key)):
    _ensure_staff_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_STAFF_SELECT_COLS} FROM deek_staff_profile WHERE email = %s",
                (email.lower().strip(),),
            )
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="staff profile not found")
    finally:
        conn.close()
    return _row_to_staff(r)


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _validate_time(field: str, value: str) -> None:
    if not _TIME_RE.match(value or ""):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be HH:MM (24h)",
        )


def _validate_active_days(value: str) -> None:
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in _VALID_DAYS]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"active_days contains invalid day(s): {bad}",
        )


@router.put("/staff", response_model=StaffProfile)
async def upsert_staff(body: StaffProfileUpsert, _: bool = Depends(verify_api_key)):
    """Upsert a staff profile — creates on first PUT, updates thereafter."""
    _ensure_staff_schema()
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="email is required and must be valid")
    if body.role_tag and body.role_tag not in VALID_ROLE_TAGS:
        raise HTTPException(
            status_code=400,
            detail=f"role_tag must be one of {sorted(VALID_ROLE_TAGS)}",
        )
    if body.briefing_time is not None:
        _validate_time("briefing_time", body.briefing_time)
    if body.quiet_start is not None:
        _validate_time("quiet_start", body.quiet_start)
    if body.quiet_end is not None:
        _validate_time("quiet_end", body.quiet_end)
    if body.active_days is not None:
        _validate_active_days(body.active_days)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO deek_staff_profile
                    (email, display_name, role_tag, briefings_enabled,
                     briefing_time, active_days, quiet_start, quiet_end,
                     preferred_voice, preferred_face, notes)
                VALUES (%s, %s, %s, COALESCE(%s, true),
                        COALESCE(%s, '07:30'::time),
                        COALESCE(%s, 'mon,tue,wed,thu,fri'),
                        COALESCE(%s, '22:00'::time),
                        COALESCE(%s, '06:30'::time),
                        %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    display_name = COALESCE(EXCLUDED.display_name, deek_staff_profile.display_name),
                    role_tag = COALESCE(EXCLUDED.role_tag, deek_staff_profile.role_tag),
                    briefings_enabled = COALESCE(%s, deek_staff_profile.briefings_enabled),
                    briefing_time = COALESCE(EXCLUDED.briefing_time, deek_staff_profile.briefing_time),
                    active_days = COALESCE(EXCLUDED.active_days, deek_staff_profile.active_days),
                    quiet_start = COALESCE(EXCLUDED.quiet_start, deek_staff_profile.quiet_start),
                    quiet_end = COALESCE(EXCLUDED.quiet_end, deek_staff_profile.quiet_end),
                    preferred_voice = COALESCE(EXCLUDED.preferred_voice, deek_staff_profile.preferred_voice),
                    preferred_face = COALESCE(EXCLUDED.preferred_face, deek_staff_profile.preferred_face),
                    notes = COALESCE(EXCLUDED.notes, deek_staff_profile.notes),
                    updated_at = NOW()
                RETURNING {_STAFF_SELECT_COLS}
                """,
                (
                    email, body.display_name, body.role_tag, body.briefings_enabled,
                    body.briefing_time, body.active_days, body.quiet_start, body.quiet_end,
                    body.preferred_voice, body.preferred_face, body.notes,
                    body.briefings_enabled,  # second placeholder for ON CONFLICT
                ),
            )
            r = cur.fetchone()
            conn.commit()
    finally:
        conn.close()
    return _row_to_staff(r)


@router.delete("/staff/{email}")
async def delete_staff(email: str, _: bool = Depends(verify_api_key)):
    _ensure_staff_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM deek_staff_profile WHERE email = %s RETURNING email",
                (email.strip().lower(),),
            )
            r = cur.fetchone()
            conn.commit()
    finally:
        conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="staff profile not found")
    return {"deleted": email}


# ── Seed default staff profiles at API startup ─────────────────────────────

DEFAULT_STAFF = [
    ("toby@nbnesigns.com", "Toby", "director",
     "cross-business overview, blockers, decisions needed"),
    ("jo@nbnesigns.com", "Jo", "director",
     "operations, client relationships, cash position"),
    ("ben@nbnesigns.com", "Ben", "production",
     "make list, machine assignments, batch priorities"),
    ("gabby@nbnesigns.com", "Gabby", "dispatch",
     "dispatch queue, labels, packing"),
    ("ivan@nbnesigns.com", "Ivan", "tech",
     "machine maintenance flags, CNC job queue"),
    ("sanna@nbnesigns.com", "Sanna", "admin",
     "email triage, quote follow-ups, supplier comms"),
]


def seed_default_staff_profiles() -> None:
    """Insert the default 6 staff profiles if they don't already exist.
    Called from API startup. Idempotent — ON CONFLICT DO NOTHING."""
    try:
        _ensure_staff_schema()
        conn = _get_conn()
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            for email, name, role, notes in DEFAULT_STAFF:
                cur.execute(
                    """
                    INSERT INTO deek_staff_profile
                        (email, display_name, role_tag, notes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (email, name, role, notes),
                )
        conn.commit()
    finally:
        conn.close()


# ── Morning briefing generator ──────────────────────────────────────────────


class BriefingResponse(BaseModel):
    user: str
    display_name: Optional[str] = None
    role_tag: Optional[str] = None
    generated_at: datetime
    briefing_md: str
    open_tasks: list[Task] = Field(default_factory=list)
    stale_snapshots: list[str] = Field(default_factory=list)


def _open_tasks_for(email: str, limit: int = 20) -> list[Task]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_TASK_SELECT_COLS}
                FROM deek_tasks
                WHERE assignee = %s AND status = 'open'
                ORDER BY
                    CASE priority
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 4
                    END,
                    due_at NULLS LAST,
                    created_at DESC
                LIMIT %s
                """,
                (email.lower(), limit),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_task(r) for r in rows]


def _build_director_briefing(
    email: str, display_name: str,
) -> tuple[str, list[str]]:
    """Cash + follow-ups + pipeline delta + critical tasks + escalations."""
    stale: list[str] = []
    led_md, led_ts = _load_snapshot("ledger")
    crm_md, crm_ts = _load_snapshot("crm")
    mfg_md, mfg_ts = _load_snapshot("manufacture")

    lines: list[str] = []
    name = (display_name or email.split("@")[0]).title()
    lines.append(f"## Deek's morning read — {name}")
    lines.append("")

    # Cash + revenue (ledger)
    if led_md:
        if _is_stale(led_ts):
            stale.append("ledger")
        ledger = _parse_ledger_snapshot(led_md)
        cash = ledger.get("cash_position")
        rev_mtd = ledger.get("revenue_mtd")
        gm_mtd = ledger.get("gross_margin_mtd")
        if cash is not None:
            lines.append(f"**Cash:** £{cash:,.0f}")
        if rev_mtd is not None:
            lines.append(f"**Revenue MTD:** £{rev_mtd:,.0f}")
        if gm_mtd is not None:
            lines.append(f"**Gross margin MTD:** {gm_mtd:.1f}%")
        lines.append("")

    # Pipeline + follow-ups (CRM)
    if crm_md:
        if _is_stale(crm_ts):
            stale.append("crm")
        crm = _parse_crm_snapshot(crm_md)
        pipeline = crm.get("pipeline_value")
        overdue = crm.get("follow_ups_overdue")
        stale_leads = crm.get("stale_leads")
        parts = []
        if overdue is not None and overdue > 0:
            parts.append(f"**{overdue} follow-ups overdue**")
        if pipeline is not None:
            parts.append(f"Pipeline: £{pipeline:,.0f}")
        if stale_leads is not None and stale_leads > 0:
            parts.append(f"{stale_leads} stale leads")
        if parts:
            lines.append("**CRM:** " + ", ".join(parts))
            lines.append("")

    # Production summary (manufacture)
    if mfg_md:
        if _is_stale(mfg_ts):
            stale.append("manufacture")
        mfg = _parse_manufacture_snapshot(mfg_md)
        open_orders = mfg.get("open_orders")
        if open_orders:
            lines.append(f"**Production:** {open_orders} open orders")
            lines.append("")

    # Critical / high tasks across the team
    director_tasks = _critical_team_tasks(limit=5)
    if director_tasks:
        lines.append("**Team tasks needing attention:**")
        for t in director_tasks:
            p = f"[{t.priority}] " if t.priority else ""
            assignee = t.assignee.split("@")[0]
            lines.append(f"- {p}{assignee}: {t.title or t.content}")
        lines.append("")

    return "\n".join(lines).rstrip(), stale


def _critical_team_tasks(limit: int = 5) -> list[Task]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {_TASK_SELECT_COLS}
                FROM deek_tasks
                WHERE status = 'open'
                  AND (priority IN ('critical', 'high') OR due_at < NOW())
                ORDER BY
                    CASE priority
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        ELSE 2
                    END,
                    due_at NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_task(r) for r in rows]


def _build_production_briefing(
    email: str, display_name: str,
) -> tuple[str, list[str]]:
    stale: list[str] = []
    mfg_md, mfg_ts = _load_snapshot("manufacture")

    name = (display_name or email.split("@")[0]).title()
    lines = [f"## Deek's morning read — {name}", ""]

    if mfg_md:
        if _is_stale(mfg_ts):
            stale.append("manufacture")
        mfg = _parse_manufacture_snapshot(mfg_md)
        open_orders = mfg.get("open_orders") or 0
        lines.append(f"**Make list:** {open_orders} open orders")
        for machine_key, machine_label in (
            ("rolf_units", "ROLF"), ("mimaki_units", "MIMAKI"), ("mutoh_units", "MUTOH"),
        ):
            v = mfg.get(machine_key)
            if isinstance(v, dict):
                lines.append(f"- {machine_label}: {v.get('orders', 0)} orders · {v.get('units', 0)} units")
            else:
                lines.append(f"- {machine_label}: available")
        lines.append("")

        deficits = mfg.get("top_deficits", [])[:3]
        if deficits:
            lines.append("**Top stock deficits:**")
            for d in deficits:
                lines.append(f"- {d['sku']}: {d['short']} short ({d['on_hand']} on hand)")
            lines.append("")

    return "\n".join(lines).rstrip(), stale


def _build_admin_briefing(
    email: str, display_name: str,
) -> tuple[str, list[str]]:
    stale: list[str] = []
    crm_md, crm_ts = _load_snapshot("crm")
    name = (display_name or email.split("@")[0]).title()
    lines = [f"## Deek's morning read — {name}", ""]

    triage = _inbox_triage_counts()
    if triage.get("total"):
        lines.append(f"**Inbox (24h):** {triage.get('new_enquiry', 0)} new enquiries, "
                     f"{triage.get('existing_project_reply', 0)} project replies, "
                     f"{triage.get('unread', 0)} unreviewed")
        lines.append("")

    if crm_md:
        if _is_stale(crm_ts):
            stale.append("crm")
        crm = _parse_crm_snapshot(crm_md)
        overdue = crm.get("follow_ups_overdue") or 0
        stale_leads = crm.get("stale_leads") or 0
        if overdue or stale_leads:
            lines.append(f"**CRM:** {overdue} follow-ups overdue, {stale_leads} stale leads")
            lines.append("")

    return "\n".join(lines).rstrip(), stale


def _build_generic_briefing(
    email: str, display_name: str,
) -> tuple[str, list[str]]:
    name = (display_name or email.split("@")[0]).title()
    lines = [f"## Deek's morning read — {name}", ""]
    lines.append("No role-specific template yet. Your open tasks are below.")
    return "\n".join(lines).rstrip(), []


def build_briefing(email: str) -> BriefingResponse:
    """Generate a briefing for a given user's email. Reads profile + snapshots.

    Returns the briefing object. Does NOT persist to deek_pending_briefings —
    that's the scheduler's job.
    """
    _ensure_staff_schema()
    _ensure_tasks_schema()
    email = email.strip().lower()

    # Load profile (may be absent — we still produce a briefing)
    conn = _get_conn()
    display_name: Optional[str] = None
    role_tag: Optional[str] = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT display_name, role_tag FROM deek_staff_profile WHERE email = %s",
                (email,),
            )
            r = cur.fetchone()
            if r:
                display_name, role_tag = r[0], r[1]
    finally:
        conn.close()

    builder_map = {
        "director": _build_director_briefing,
        "production": _build_production_briefing,
        "dispatch": _build_production_briefing,    # dispatch reuses production for now
        "tech": _build_production_briefing,
        "admin": _build_admin_briefing,
    }
    builder = builder_map.get(role_tag or "", _build_generic_briefing)
    md, stale = builder(email, display_name or email)

    open_tasks = _open_tasks_for(email)
    if open_tasks:
        md += "\n\n## Your open tasks\n"
        for t in open_tasks:
            p = f"[{t.priority}] " if t.priority else ""
            due = f" (due {t.due_at.strftime('%Y-%m-%d')})" if t.due_at else ""
            md += f"- {p}{t.title or t.content}{due}\n"

    if stale:
        md += f"\n\n_Note: snapshot data for {', '.join(stale)} is older than 2 hours._"

    return BriefingResponse(
        user=email,
        display_name=display_name,
        role_tag=role_tag,
        generated_at=datetime.now(timezone.utc),
        briefing_md=md,
        open_tasks=open_tasks,
        stale_snapshots=stale,
    )


@router.get("/briefing", response_model=BriefingResponse)
async def briefing(
    user: str = Query(..., description="email address"),
    _: bool = Depends(verify_api_key),
):
    return build_briefing(user)


# ── Pending briefings (populated by scheduled job, read by PWA) ──────────


class PendingBriefing(BaseModel):
    id: int
    email: str
    generated_at: datetime
    briefing_md: str
    seen_at: Optional[datetime]
    dismissed_at: Optional[datetime]
    incorrect_reason: Optional[str]


class PendingBriefingList(BaseModel):
    items: list[PendingBriefing]
    unseen_count: int


@router.get("/briefings/pending", response_model=PendingBriefingList)
async def list_pending_briefings(
    user: str = Query(..., description="email"),
    limit: int = Query(10, ge=1, le=50),
    _: bool = Depends(verify_api_key),
):
    _ensure_staff_schema()
    email = user.strip().lower()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, generated_at, briefing_md, seen_at,
                       dismissed_at, incorrect_reason
                FROM deek_pending_briefings
                WHERE email = %s AND dismissed_at IS NULL
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (email, limit),
            )
            rows = cur.fetchall()
            items = [
                PendingBriefing(
                    id=r[0], email=r[1], generated_at=r[2], briefing_md=r[3],
                    seen_at=r[4], dismissed_at=r[5], incorrect_reason=r[6],
                )
                for r in rows
            ]
            unseen = sum(1 for i in items if i.seen_at is None)
    finally:
        conn.close()
    return PendingBriefingList(items=items, unseen_count=unseen)


class BriefingPatch(BaseModel):
    action: str                          # 'seen' | 'dismissed' | 'incorrect'
    incorrect_reason: Optional[str] = None


@router.patch("/briefings/pending/{briefing_id}", response_model=PendingBriefing)
async def patch_pending_briefing(
    briefing_id: int,
    body: BriefingPatch,
    _: bool = Depends(verify_api_key),
):
    if body.action not in {"seen", "dismissed", "incorrect"}:
        raise HTTPException(status_code=400, detail="action must be seen|dismissed|incorrect")
    _ensure_staff_schema()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if body.action == "seen":
                cur.execute(
                    "UPDATE deek_pending_briefings SET seen_at = COALESCE(seen_at, NOW()) "
                    "WHERE id = %s RETURNING id, email, generated_at, briefing_md, seen_at, dismissed_at, incorrect_reason",
                    (briefing_id,),
                )
            elif body.action == "dismissed":
                cur.execute(
                    "UPDATE deek_pending_briefings SET dismissed_at = NOW(), seen_at = COALESCE(seen_at, NOW()) "
                    "WHERE id = %s RETURNING id, email, generated_at, briefing_md, seen_at, dismissed_at, incorrect_reason",
                    (briefing_id,),
                )
            else:  # incorrect
                cur.execute(
                    "UPDATE deek_pending_briefings SET incorrect_reason = %s, seen_at = COALESCE(seen_at, NOW()) "
                    "WHERE id = %s RETURNING id, email, generated_at, briefing_md, seen_at, dismissed_at, incorrect_reason",
                    (body.incorrect_reason or "(no reason given)", briefing_id),
                )
            r = cur.fetchone()
            if not r:
                raise HTTPException(status_code=404, detail="briefing not found")
            conn.commit()
    finally:
        conn.close()
    return PendingBriefing(
        id=r[0], email=r[1], generated_at=r[2], briefing_md=r[3],
        seen_at=r[4], dismissed_at=r[5], incorrect_reason=r[6],
    )


# ── Scheduler-triggered: generate briefings for everyone enabled today ────


def _weekday_key() -> str:
    """Return today's 3-letter lowercase day key (mon, tue, wed, ...)."""
    return datetime.now(timezone.utc).strftime("%a").lower()


def generate_daily_briefings() -> dict:
    """Generate + persist briefings for every enabled staff member whose
    active_days include today. Called by the APScheduler job.
    """
    _ensure_staff_schema()
    day = _weekday_key()
    results: dict = {"day": day, "generated": [], "skipped": []}

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, active_days, briefings_enabled "
                "FROM deek_staff_profile "
                "WHERE briefings_enabled = true"
            )
            candidates = cur.fetchall()
    finally:
        conn.close()

    for email, active_days, enabled in candidates:
        if not enabled:
            results["skipped"].append({"email": email, "reason": "disabled"})
            continue
        if day not in (active_days or "").lower():
            results["skipped"].append({"email": email, "reason": f"not active on {day}"})
            continue
        try:
            b = build_briefing(email)
            conn2 = _get_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "INSERT INTO deek_pending_briefings (email, briefing_md) "
                        "VALUES (%s, %s) RETURNING id",
                        (email, b.briefing_md),
                    )
                    new_id = cur.fetchone()[0]
                    conn2.commit()
                results["generated"].append({"email": email, "id": new_id})
            finally:
                conn2.close()
        except Exception as exc:
            results["skipped"].append(
                {"email": email, "reason": f"{type(exc).__name__}: {exc}"}
            )
    return results


@router.post("/briefings/generate-now")
async def briefings_generate_now(_: bool = Depends(verify_api_key)):
    """Manual trigger of the daily briefing generation. For testing + admin."""
    return generate_daily_briefings()
