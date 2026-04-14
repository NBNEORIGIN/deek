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
    background_tasks.add_task(_run_logged, 'inventory', region, sync_inventory)
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
    background_tasks.add_task(_run_logged, 'analytics', region, sync_analytics, days=days)
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
    Discover advertising profile IDs from Amazon for a region (live API call).
    Superseded by ami_advertising_profiles table — use
    GET /spapi/advertising/profiles/db for the authoritative stored set.
    """
    from core.amazon_intel.spapi.advertising import get_advertising_profiles
    profiles = get_advertising_profiles(region=region)
    return {'region': region, 'profiles': profiles}


@router.get("/spapi/advertising/profiles/db")
async def spapi_advertising_profiles_db(
    region: Optional[str] = Query(None),
    active_only: bool = Query(True),
):
    """List advertising profiles currently configured for sync from ami_advertising_profiles."""
    from core.amazon_intel.db import list_advertising_profiles
    profiles = list_advertising_profiles(region=region, active_only=active_only)
    return {'region': region, 'count': len(profiles), 'profiles': profiles}


@router.post("/spapi/advertising/profiles/seed")
async def spapi_advertising_profiles_seed(
    json_path: Optional[str] = Query(None,
        description="Path to amazon_ads_profiles.json. Defaults to AMAZON_ADS_PROFILES_JSON env var, then D:/claw/amazon_ads_profiles.json"),
):
    """
    Upsert rows into ami_advertising_profiles from amazon_ads_profiles.json
    (produced by scripts/ads_auth.py). Safe to re-run — matched on profile_id.
    """
    import os as _os
    from core.amazon_intel.db import seed_advertising_profiles_from_json
    path = (
        json_path
        or _os.getenv('AMAZON_ADS_PROFILES_JSON')
        or _os.path.join(_os.getenv('CAIRN_ROOT', '/opt/nbne/cairn/deploy'),
                         'amazon_ads_profiles.json')
    )
    if not _os.path.exists(path):
        # Fallback to Windows dev path
        if _os.path.exists(r'D:\claw\amazon_ads_profiles.json'):
            path = r'D:\claw\amazon_ads_profiles.json'
        else:
            return {'error': 'amazon_ads_profiles.json not found',
                    'tried': path, 'hint': 'pass json_path explicitly'}
    return seed_advertising_profiles_from_json(path)


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


# ── Generic Report Endpoints (used by Manufacture restock adapter) ────────────

@router.post("/spapi/report/request")
async def spapi_report_request(
    report_type: str,
    region: str = "EU",
    marketplace_ids: list[str] = None,
):
    """
    Request a generic SP-API report. Returns reportId immediately.
    The report is queued by Amazon — poll /ami/spapi/report/{id}/status for completion.

    Used by: Manufacture restock module (GET_FBA_INVENTORY_PLANNING_DATA)
    """
    import asyncio
    from core.amazon_intel.spapi.client import create_report, REGION_MARKETPLACE

    region = region.upper()
    mp_id = (marketplace_ids or [None])[0] or REGION_MARKETPLACE.get(region)

    try:
        report_id = await asyncio.to_thread(
            create_report, region, report_type, mp_id
        )
        return {'report_id': report_id, 'region': region, 'status': 'IN_QUEUE'}
    except Exception as exc:
        raise HTTPException(500, f"Failed to request report: {exc}")


@router.get("/spapi/report/{report_id}/status")
async def spapi_report_status(report_id: str, region: str = "EU"):
    """
    Check the processing status of a report.
    Returns: {processing_status, document_id}
    Status values: IN_QUEUE | IN_PROGRESS | DONE | CANCELLED | FATAL
    """
    import asyncio
    from core.amazon_intel.spapi.client import spapi_get

    region = region.upper()
    try:
        data = await asyncio.to_thread(
            spapi_get, region, f'/reports/2021-06-30/reports/{report_id}'
        )
        return {
            'report_id': report_id,
            'processing_status': data.get('processingStatus', ''),
            'document_id': data.get('reportDocumentId'),
            'region': region,
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to check report status: {exc}")


@router.get("/spapi/report/{report_id}/download")
async def spapi_report_download(report_id: str, region: str = "EU"):
    """
    Download a completed report as raw bytes.
    Returns 404 if report is not yet DONE.
    Used by: Manufacture restock adapter (polls status separately, calls this when DONE).
    """
    import asyncio
    from fastapi.responses import Response as FastAPIResponse
    from core.amazon_intel.spapi.client import spapi_get, download_report_document

    region = region.upper()
    try:
        # Check status first
        data = await asyncio.to_thread(
            spapi_get, region, f'/reports/2021-06-30/reports/{report_id}'
        )
        status = data.get('processingStatus', '')
        if status != 'DONE':
            raise HTTPException(404, f"Report not ready: processingStatus={status}")

        doc_id = data.get('reportDocumentId')
        if not doc_id:
            raise HTTPException(500, "Report DONE but no documentId")

        content = await asyncio.to_thread(download_report_document, region, doc_id)
        return FastAPIResponse(
            content=content,
            media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename=restock_{region}_{report_id}.csv'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to download report: {exc}")


# ── SKU Mapping Lookup (used by Manufacture restock assembler) ────────────────

@router.get("/sku-mapping/lookup")
async def sku_mapping_lookup(
    sku: str = Query(..., description="Merchant SKU to look up"),
    marketplace: Optional[str] = Query(None, description="Marketplace code e.g. GB, US"),
):
    """
    Look up M-number for a merchant SKU.
    Returns {sku, m_number, asin, country} or 404 if not found.
    """
    from core.amazon_intel.db import get_conn

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if marketplace:
                    cur.execute(
                        """
                        SELECT sku, m_number, asin, country
                        FROM ami_sku_mapping
                        WHERE sku = %s AND (country ILIKE %s OR country IS NULL)
                        LIMIT 1
                        """,
                        (sku, marketplace),
                    )
                else:
                    cur.execute(
                        "SELECT sku, m_number, asin, country FROM ami_sku_mapping WHERE sku = %s LIMIT 1",
                        (sku,),
                    )
                row = cur.fetchone()
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}")

    if not row:
        raise HTTPException(404, f"SKU not found: {sku}")

    return {
        'sku': row[0],
        'm_number': row[1],
        'asin': row[2],
        'country': row[3],
    }


@router.patch("/spapi/listings/{sku}/title")
async def patch_listing_title(
    sku: str,
    title: str = Query(...),
    region: str = Query('EU'),
):
    """Update the listing title for a SKU on Amazon."""
    from core.amazon_intel.spapi.listings import update_title
    return update_title(sku=sku, title=title, region=region)  # type: ignore[arg-type]


# ── Catalog Enrichment (Phase 1) ────────────────────────────────────────────

@router.post("/catalog/enrich")
async def enrich_catalog(
    background_tasks: BackgroundTasks,
    region: str = Query('EU'),
    limit: int = Query(100),
    skip_recent_hours: int = Query(24),
):
    """Trigger catalog enrichment — fetches full listing content via Catalog Items API."""
    from core.amazon_intel.spapi.catalog import run_enrichment
    background_tasks.add_task(run_enrichment, region=region, limit=limit,
                              skip_recent_hours=skip_recent_hours)
    return {'status': 'started', 'region': region, 'limit': limit}


@router.get("/catalog/content/{asin}")
async def get_listing_content(asin: str, marketplace: str = Query('UK')):
    """Get enriched listing content for an ASIN."""
    from core.amazon_intel.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT asin, marketplace, title, bullet1, bullet2, bullet3, bullet4, bullet5,
                          description, main_image_url, image_urls, image_count,
                          aplus_present, brand, parent_asin, variation_type,
                          child_asins, product_type, list_price_amount,
                          last_enriched_at, content_hash
                   FROM ami_listing_content WHERE asin = %s AND marketplace = %s""",
                (asin, marketplace),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"No listing content for {asin} in {marketplace}")
    cols = [d[0] for d in cur.description]
    result = dict(zip(cols, row))
    for k, v in result.items():
        if hasattr(v, 'isoformat'):
            result[k] = v.isoformat()
    return result


@router.get("/catalog/content")
async def list_listing_content(
    marketplace: str = Query('UK'),
    limit: int = Query(50),
    offset: int = Query(0),
):
    """List enriched listing content."""
    from core.amazon_intel.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT asin, marketplace, title, brand, image_count,
                          aplus_present, product_type, last_enriched_at
                   FROM ami_listing_content
                   WHERE marketplace = %s
                   ORDER BY last_enriched_at DESC
                   LIMIT %s OFFSET %s""",
                (marketplace, limit, offset),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    for r in rows:
        for k, v in r.items():
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
    return {'marketplace': marketplace, 'count': len(rows), 'items': rows}


@router.get("/catalog/changes/{asin}")
async def get_listing_changes(asin: str, marketplace: str = Query('UK'), limit: int = Query(50)):
    """Get content change history for an ASIN."""
    from core.amazon_intel.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT field_name, old_value, new_value, changed_at
                   FROM ami_listing_content_history
                   WHERE asin = %s AND marketplace = %s
                   ORDER BY changed_at DESC LIMIT %s""",
                (asin, marketplace, limit),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    for r in rows:
        for k, v in r.items():
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
    return {'asin': asin, 'changes': rows}


# ── Embeddings (Phase 1) ────────────────────────────────────────────────────

@router.post("/catalog/embed")
async def embed_listings(
    background_tasks: BackgroundTasks,
    marketplace: str = Query('UK'),
):
    """Trigger embedding generation for all listing content in a marketplace."""
    from core.amazon_intel.spapi.embeddings import embed_all_listings
    background_tasks.add_task(embed_all_listings, marketplace=marketplace)
    return {'status': 'started', 'marketplace': marketplace}


@router.get("/catalog/search")
async def semantic_search_listings(
    q: str = Query(...),
    marketplace: str = Query('UK'),
    field_type: str = Query('combined'),
    limit: int = Query(20),
):
    """Semantic search over listing content embeddings."""
    from core.amazon_intel.spapi.embeddings import semantic_search
    results = semantic_search(q, marketplace=marketplace, field_type=field_type, limit=limit)
    for r in results:
        for k, v in r.items():
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
            elif isinstance(v, float):
                r[k] = round(v, 4)
    return {'query': q, 'results': results}


# ── Notifications (Phase 2) ─────────────────────────────────────────────────

@router.post("/notifications/setup")
async def setup_notifications_endpoint(region: str = Query('EU')):
    """Full notification setup: create destination + subscribe to all types."""
    from core.amazon_intel.spapi.notifications import setup_notifications
    return setup_notifications(region)  # type: ignore[arg-type]


@router.get("/notifications/destinations")
async def list_notification_destinations(region: str = Query('EU')):
    """List notification destinations for a region."""
    from core.amazon_intel.spapi.notifications import list_destinations
    return list_destinations(region)  # type: ignore[arg-type]


@router.get("/notifications/subscriptions/{notification_type}")
async def get_notification_subscription(notification_type: str, region: str = Query('EU')):
    """Get subscription status for a notification type."""
    from core.amazon_intel.spapi.notifications import get_subscription
    result = get_subscription(region, notification_type)  # type: ignore[arg-type]
    return result or {'status': 'not_subscribed'}


@router.post("/notifications/poll")
async def poll_notifications_endpoint(
    region: str = Query('EU'),
    max_messages: int = Query(10),
):
    """Poll SQS for pending notifications."""
    from core.amazon_intel.spapi.notifications import poll_notifications
    notifications = poll_notifications(region, max_messages=max_messages)  # type: ignore[arg-type]
    return {'region': region, 'count': len(notifications), 'notifications': notifications}


@router.post("/notifications/test")
async def send_test_notification(
    region: str = Query('EU'),
    asin: str = Query('B000TEST01'),
):
    """Send a test notification to the SQS queue for verification."""
    from core.amazon_intel.spapi.notifications import send_test_notification
    return send_test_notification(region, asin=asin)  # type: ignore[arg-type]


@router.get("/notifications/events")
async def list_notification_events(
    region: str = Query(None),
    limit: int = Query(50),
):
    """List recent notification events."""
    from core.amazon_intel.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            if region:
                cur.execute(
                    """SELECT id, notification_type, region, asin, sku, event_time,
                              processed, received_at
                       FROM ami_notification_events
                       WHERE region = %s ORDER BY received_at DESC LIMIT %s""",
                    (region, limit),
                )
            else:
                cur.execute(
                    """SELECT id, notification_type, region, asin, sku, event_time,
                              processed, received_at
                       FROM ami_notification_events
                       ORDER BY received_at DESC LIMIT %s""",
                    (limit,),
                )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    for r in rows:
        for k, v in r.items():
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
    return {'count': len(rows), 'events': rows}


@router.post("/notifications/processor")
async def run_notification_processor_endpoint(
    background_tasks: BackgroundTasks,
    poll_cycles: int = Query(3),
):
    """Run the notification processor across all configured regions."""
    from core.amazon_intel.spapi.notifications import run_notification_processor
    background_tasks.add_task(run_notification_processor, poll_cycles=poll_cycles)
    return {'status': 'started', 'poll_cycles': poll_cycles}
