"""
Etsy Intelligence API routes.

Mounted at /etsy/* in the Cairn FastAPI app.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

router = APIRouter(prefix="/etsy", tags=["Etsy Intelligence"])


@router.get("/health")
async def etsy_health():
    """Module health check."""
    from core.etsy_intel.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM etsy_shops")
                shops = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM etsy_listings")
                listings = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM etsy_sales")
                sales = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM etsy_listing_snapshots")
                snapshots = cur.fetchone()[0]
        return {
            "status": "ok",
            "module": "etsy_intelligence",
            "counts": {
                "shops": shops,
                "listings": listings,
                "sales": sales,
                "snapshots": snapshots,
            },
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ── Sync ─────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def trigger_sync():
    """Trigger a full sync from Etsy API."""
    from core.etsy_intel.sync import sync_all
    result = await sync_all()
    return result


# ── Shops ────────────────────────────────────────────────────────────────────

@router.get("/shops")
async def list_shops():
    """List all synced Etsy shops."""
    from core.etsy_intel.db import get_shops
    return {"shops": get_shops()}


# ── Listings ─────────────────────────────────────────────────────────────────

@router.get("/listings")
async def list_listings(
    shop_id: Optional[int] = Query(None),
    state: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
):
    """List listings with optional filters."""
    from core.etsy_intel.db import get_listings
    return get_listings(
        shop_id=shop_id, state=state,
        min_score=min_score, max_score=max_score,
        limit=limit, offset=offset,
    )


@router.get("/listings/{listing_id}")
async def get_listing_detail(listing_id: int):
    """Single listing detail."""
    from core.etsy_intel.db import get_listing
    listing = get_listing(listing_id)
    if not listing:
        raise HTTPException(404, f"No listing found with ID {listing_id}")
    return listing


# ── Underperformers ──────────────────────────────────────────────────────────

@router.get("/underperformers")
async def underperformers(
    max_score: float = Query(5.0),
    limit: int = Query(20, le=100),
):
    """Listings with health score below threshold, worst first."""
    from core.etsy_intel.db import get_listings
    return get_listings(max_score=max_score, limit=limit)


# ── Reports ──────────────────────────────────────────────────────────────────

@router.get("/report/latest")
async def latest_report():
    """Latest health report."""
    from core.etsy_intel.reports import generate_report
    return generate_report()


# ── Memory Indexing ──────────────────────────────────────────────────────────

@router.post("/index-to-memory")
async def index_to_memory():
    """Push Etsy Intelligence context into Cairn memory."""
    from core.etsy_intel.reports import build_cairn_context
    import httpx
    import os

    context = build_cairn_context()
    cairn_url = os.getenv('CAIRN_API_URL', 'http://localhost:8765')

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f'{cairn_url}/memory/write',
                json={
                    'project': 'etsy-intelligence',
                    'query': 'Etsy Intelligence weekly context snapshot',
                    'decision': context['summary_text'],
                    'rejected': '',
                    'outcome': 'committed',
                    'model': 'system',
                    'files_changed': [],
                },
                timeout=10.0,
            )
            resp.raise_for_status()
        return {'status': 'ok', 'summary': context['summary_text']}
    except Exception as e:
        return {'status': 'partial', 'context': context, 'memory_error': str(e)}


# ── Cairn Context ────────────────────────────────────────────────────────────

@router.get("/cairn/context")
async def cairn_context():
    """Module context endpoint per CAIRN_MODULES.md spec."""
    from core.etsy_intel.reports import build_cairn_context
    return build_cairn_context()
