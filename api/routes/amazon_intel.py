"""
Amazon Listing Intelligence API routes.

Mounted at /ami/* in the Cairn FastAPI app.
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
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
