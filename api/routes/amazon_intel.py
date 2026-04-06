"""
Amazon Listing Intelligence API routes.

Mounted at /ami/* in the Cairn FastAPI app.
"""
from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException, Query
from typing import Optional

router = APIRouter(prefix="/ami", tags=["Amazon Intelligence"])


@router.get("/health")
async def ami_health():
    """Module health check."""
    from core.amazon_intel.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ami_uploads")
                uploads = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM ami_sku_mapping")
                mappings = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM ami_listing_snapshots")
                snapshots = cur.fetchone()[0]
        return {
            "status": "ok",
            "module": "amazon_intelligence",
            "counts": {
                "sku_mappings": mappings,
                "uploads": uploads,
                "snapshots": snapshots,
            },
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ── SKU Mapping ──────────────────────────────────────────────────────────────

@router.post("/sku-mapping/sync")
async def sync_sku_mapping():
    """Re-read the stock sheet CSV and upsert into ami_sku_mapping."""
    from core.amazon_intel.stock_sheet import sync_from_stock_sheet
    result = sync_from_stock_sheet()
    return result


@router.get("/sku-mapping/stats")
async def sku_mapping_stats():
    """Return counts: total SKUs, unique M-numbers, by country."""
    from core.amazon_intel.stock_sheet import get_mapping_stats
    return get_mapping_stats()


# ── Uploads ──────────────────────────────────────────────────────────────────

@router.get("/uploads")
async def list_uploads(limit: int = Query(50, le=200)):
    """List all uploads with status."""
    from core.amazon_intel.db import list_uploads
    rows = list_uploads(limit=limit)
    for r in rows:
        for k, v in r.items():
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
    return {"uploads": rows}


@router.post("/upload/flatfile")
async def upload_flatfile(file: UploadFile = File(...),
                          marketplace: Optional[str] = Query(None)):
    """Upload an Amazon inventory flatfile (.xlsm), parse and store."""
    if not file.filename.endswith(('.xlsm', '.xlsx')):
        raise HTTPException(400, "Expected .xlsm or .xlsx file")
    from core.amazon_intel.parsers.flatfile import parse_and_store_flatfile
    content = await file.read()
    result = parse_and_store_flatfile(content, file.filename, marketplace)
    return result


@router.post("/upload/all-listings")
async def upload_all_listings(file: UploadFile = File(...),
                               marketplace: Optional[str] = Query(None)):
    """Upload an Amazon All Listings Report (.txt TSV) to enrich SKU→ASIN mapping."""
    from core.amazon_intel.parsers.all_listings import parse_and_store_all_listings
    content = await file.read()
    result = parse_and_store_all_listings(content, file.filename, marketplace)
    return result


@router.post("/upload/business-report")
async def upload_business_report(file: UploadFile = File(...),
                                  marketplace: Optional[str] = Query(None)):
    """Upload an Amazon Business Report CSV."""
    from core.amazon_intel.parsers.business_report import parse_and_store_business_report
    content = await file.read()
    result = parse_and_store_business_report(content, file.filename, marketplace)
    return result


@router.post("/upload/advertising")
async def upload_advertising(file: UploadFile = File(...),
                              marketplace: Optional[str] = Query(None)):
    """Upload an Amazon Advertising report (CSV or XLSX)."""
    from core.amazon_intel.parsers.advertising import parse_and_store_advertising
    content = await file.read()
    result = parse_and_store_advertising(content, file.filename, marketplace)
    return result


# ── New products ─────────────────────────────────────────────────────────────

@router.post("/new-products/ingest")
async def ingest_new_products(
    csv_path: str = Query(default='data/nbne-processes/new_products_dec2025_mar2026.csv'),
):
    """Ingest the new products reference CSV into ami_new_products."""
    from core.amazon_intel.db import ingest_new_products_csv
    from pathlib import Path
    path = Path(csv_path)
    if not path.exists():
        # Try relative to claw root
        path = Path(__file__).parent.parent.parent / csv_path
    if not path.exists():
        return {'error': f'File not found: {csv_path}', 'status': 'error'}
    result = ingest_new_products_csv(str(path))
    return {'status': 'complete', **result}


@router.post("/migrate")
async def run_migration():
    """Run schema migrations (add new columns to existing tables)."""
    from core.amazon_intel.db import migrate_ami_schema
    migrate_ami_schema()
    return {'status': 'complete', 'message': 'Schema migrations applied'}


# ── Snapshots ────────────────────────────────────────────────────────────────

@router.post("/snapshots/build")
async def build_snapshots(marketplace: Optional[str] = Query(None)):
    """Assemble snapshots from latest uploaded data, run scoring."""
    from core.amazon_intel.snapshots import build_snapshots as _build
    result = await _build(marketplace)
    return result


@router.get("/snapshots")
async def list_snapshots(
    marketplace: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_score: Optional[float] = Query(None),
    diagnosis: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
):
    """List snapshots, filterable by marketplace, score range, diagnosis code."""
    from core.amazon_intel.snapshots import query_snapshots
    return query_snapshots(
        marketplace=marketplace, min_score=min_score, max_score=max_score,
        diagnosis=diagnosis, limit=limit, offset=offset,
    )


@router.get("/snapshots/{asin}")
async def get_snapshot(asin: str):
    """Latest snapshot for a specific ASIN."""
    from core.amazon_intel.snapshots import get_latest_snapshot
    snap = get_latest_snapshot(asin)
    if not snap:
        raise HTTPException(404, f"No snapshot for ASIN {asin}")
    return snap


# ── Reports ──────────────────────────────────────────────────────────────────

@router.post("/report/generate")
async def generate_report(marketplace: Optional[str] = Query(None)):
    """Generate weekly health report from current snapshots."""
    from core.amazon_intel.reports import generate_weekly_report
    return generate_weekly_report(marketplace)


@router.get("/report/latest")
async def latest_report(marketplace: Optional[str] = Query(None)):
    """Most recent weekly report."""
    from core.amazon_intel.reports import get_latest_report
    report = get_latest_report(marketplace)
    if not report:
        raise HTTPException(404, "No reports generated yet")
    return report


# ── Underperformers ──────────────────────────────────────────────────────────

@router.get("/underperformers")
async def underperformers(
    max_score: float = Query(5.0),
    marketplace: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
):
    """Listings with health score below threshold, worst first."""
    from core.amazon_intel.snapshots import query_snapshots
    return query_snapshots(
        marketplace=marketplace, max_score=max_score,
        limit=limit, offset=0, order_by='health_score ASC',
    )


# ── Memory Indexing ──────────────────────────────────────────────────────────

@router.post("/index-to-memory")
async def index_to_memory():
    """Push listing snapshots and report summary into Cairn memory."""
    from core.amazon_intel.memory import index_snapshots_to_memory
    return index_snapshots_to_memory()


# ── Cairn Context ────────────────────────────────────────────────────────────

@router.get("/cairn/context")
async def cairn_context():
    """Module context endpoint per CAIRN_MODULES.md spec."""
    from core.amazon_intel.reports import build_cairn_context
    return build_cairn_context()


# ── SP-API Automated Sync ─────────────────────────────────────────────────────

@router.get("/spapi/status")
async def spapi_status():
    """
    Recent SP-API sync log entries.
    Status values: running | complete | error
    While 'running': Amazon is generating the report (5-20 min).
    """
    from core.amazon_intel.spapi.scheduler import get_sync_status
    syncs = get_sync_status(limit=40)
    running = [s for s in syncs if s['status'] == 'running']
    last_complete = {s['sync_type']: s for s in syncs if s['status'] == 'complete'}
    return {
        'syncs': syncs,
        'running': running,
        'last_complete': last_complete,
    }


@router.post("/spapi/sync")
async def spapi_sync(
    background_tasks: BackgroundTasks,
    region: Optional[str] = Query(None, description="EU, NA, FE — default: all active"),
    force: bool = Query(False, description="Ignore 6hr interval check"),
):
    """
    Trigger SP-API sync (inventory + analytics + advertising).
    Runs in background — returns immediately with job info.
    Poll /ami/spapi/status for results.
    """
    from core.amazon_intel.spapi.scheduler import run_full_sync, ACTIVE_REGIONS

    regions = [region] if region else None  # type: ignore[list-item]
    background_tasks.add_task(run_full_sync, regions=regions, force=force)
    return {
        'status': 'started',
        'regions': regions or ACTIVE_REGIONS,
        'force': force,
        'message': 'Sync running in background. Poll /ami/spapi/status for results.',
    }


@router.post("/spapi/sync/inventory")
async def spapi_sync_inventory(
    background_tasks: BackgroundTasks,
    region: str = Query('EU'),
):
    """Pull All Listings Report for a region via SP-API."""
    from core.amazon_intel.spapi.scheduler import _run_logged
    from core.amazon_intel.spapi.inventory import sync_inventory
    background_tasks.add_task(_run_logged, 'inventory', region, sync_inventory, region=region)
    return {'status': 'started', 'region': region, 'type': 'inventory',
            'message': 'Poll /ami/spapi/status for result. Amazon report generation: 5-20 min.'}


@router.post("/spapi/sync/analytics")
async def spapi_sync_analytics(
    background_tasks: BackgroundTasks,
    region: str = Query('EU'),
    days: int = Query(30, le=60),
):
    """Pull 30-day Sales & Traffic report via SP-API."""
    from core.amazon_intel.spapi.scheduler import _run_logged
    from core.amazon_intel.spapi.analytics import sync_analytics
    background_tasks.add_task(_run_logged, 'analytics', region, sync_analytics,
                               region=region, days=days)
    return {'status': 'started', 'region': region, 'type': 'analytics', 'days': days,
            'message': 'Poll /ami/spapi/status for result.'}


@router.post("/spapi/sync/advertising")
async def spapi_sync_advertising(
    background_tasks: BackgroundTasks,
    region: str = Query('EU'),
    profile_id: Optional[str] = Query(None),
    days: int = Query(30, le=60),
):
    """Pull Sponsored Products search term report via Ads API."""
    from core.amazon_intel.spapi.advertising import sync_advertising
    background_tasks.add_task(sync_advertising, region=region,
                               profile_id=profile_id, days=days)
    return {'status': 'started', 'region': region, 'type': 'advertising', 'days': days}


@router.get("/spapi/advertising/profiles")
async def spapi_advertising_profiles(region: str = Query('EU')):
    """
    Discover advertising profile IDs for a region.
    Run this once per region, then store the profileId in .env as
    AMAZON_ADS_PROFILE_ID_{EU/NA/AU}.
    """
    from core.amazon_intel.spapi.advertising import get_advertising_profiles
    profiles = get_advertising_profiles(region=region)
    return {'region': region, 'profiles': profiles}


# ── Listings Write API ────────────────────────────────────────────────────────

@router.get("/spapi/listings/{sku}")
async def get_listing(sku: str, region: str = Query('EU')):
    """Retrieve current listing attributes for a SKU from Amazon."""
    from core.amazon_intel.spapi.listings import get_listing as _get
    return _get(sku=sku, region=region)  # type: ignore[arg-type]


@router.patch("/spapi/listings/{sku}/price")
async def patch_listing_price(
    sku: str,
    price: float = Query(...),
    currency: str = Query('GBP'),
    region: str = Query('EU'),
):
    """Update the price for a SKU on Amazon."""
    from core.amazon_intel.spapi.listings import update_price
    return update_price(sku=sku, price=price, currency=currency, region=region)  # type: ignore[arg-type]


@router.patch("/spapi/listings/{sku}/bullets")
async def patch_listing_bullets(
    sku: str,
    region: str = Query('EU'),
    bullets: list[str] = Query(...),
):
    """Update bullet points for a SKU on Amazon (max 5)."""
    from core.amazon_intel.spapi.listings import update_bullets
    return update_bullets(sku=sku, bullets=bullets, region=region)  # type: ignore[arg-type]


@router.patch("/spapi/listings/{sku}/title")
async def patch_listing_title(
    sku: str,
    title: str = Query(...),
    region: str = Query('EU'),
):
    """Update the listing title for a SKU on Amazon."""
    from core.amazon_intel.spapi.listings import update_title
    return update_title(sku=sku, title=title, region=region)  # type: ignore[arg-type]
