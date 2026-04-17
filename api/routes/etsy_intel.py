"""
Etsy Intelligence API routes.

Mounted at /etsy/* in the Deek FastAPI app.
"""
import os
import secrets
import hashlib
import base64
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from typing import Optional
import logging
import httpx

from api.middleware.auth import verify_api_key

router = APIRouter(prefix="/etsy", tags=["Etsy Intelligence"])

log = logging.getLogger(__name__)

ETSY_AUTH_URL = 'https://www.etsy.com/oauth/connect'
ETSY_TOKEN_URL = 'https://api.etsy.com/v3/public/oauth/token'
ETSY_SCOPES = 'transactions_r shops_r'


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


# ── OAuth 2.0 ────────────────────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    return verifier, challenge


@router.get("/oauth/connect")
async def oauth_connect():
    """Initiate Etsy OAuth 2.0 flow. Redirects to Etsy consent page."""
    from core.etsy_intel.db import save_oauth_state

    api_key = os.getenv('ETSY_API_KEY', '')
    redirect_uri = os.getenv('ETSY_OAUTH_REDIRECT_URI', '')
    if not api_key or not redirect_uri:
        raise HTTPException(500, 'ETSY_API_KEY and ETSY_OAUTH_REDIRECT_URI must be set')

    state = secrets.token_urlsafe(32)
    verifier, challenge = _generate_pkce()

    # Store state + verifier for callback validation
    save_oauth_state(state, verifier)

    params = {
        'response_type': 'code',
        'client_id': api_key,
        'redirect_uri': redirect_uri,
        'scope': ETSY_SCOPES,
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    auth_url = f'{ETSY_AUTH_URL}?' + '&'.join(
        f'{k}={httpx.URL("", params={k: v}).params[k]}' for k, v in params.items()
    )
    # Build URL properly
    from urllib.parse import urlencode
    auth_url = f'{ETSY_AUTH_URL}?{urlencode(params)}'

    return RedirectResponse(url=auth_url)


@router.get("/oauth/callback")
async def oauth_callback(code: str = Query(...), state: str = Query(...)):
    """Handle Etsy OAuth callback. Exchanges code for tokens."""
    from core.etsy_intel.db import get_oauth_state, save_oauth_token

    # Validate state
    stored = get_oauth_state(state)
    if not stored:
        raise HTTPException(400, 'Invalid or expired OAuth state')

    api_key = os.getenv('ETSY_API_KEY', '')
    redirect_uri = os.getenv('ETSY_OAUTH_REDIRECT_URI', '')

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(ETSY_TOKEN_URL, json={
            'grant_type': 'authorization_code',
            'client_id': api_key,
            'redirect_uri': redirect_uri,
            'code': code,
            'code_verifier': stored['code_verifier'],
        })

        if resp.status_code != 200:
            detail = resp.text
            raise HTTPException(502, f'Etsy token exchange failed: {detail}')

        data = resp.json()

    access_token = data['access_token']
    refresh_token = data['refresh_token']
    expires_in = data.get('expires_in', 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Extract user_id from token (format: "user_id.token_string")
    try:
        user_id = int(access_token.split('.')[0])
    except (ValueError, IndexError):
        user_id = 1  # fallback

    save_oauth_token(
        user_id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=ETSY_SCOPES,
    )

    return HTMLResponse(content=f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 40px auto;">
    <h2>Etsy OAuth Connected</h2>
    <p>Successfully authenticated with Etsy.</p>
    <p>User ID: {user_id}</p>
    <p>Scopes: {ETSY_SCOPES}</p>
    <p>Token expires: {expires_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
    <p>You can now run <code>POST /etsy/sync</code> to fetch sales data.</p>
    </body></html>
    """)


@router.get("/oauth/status")
async def oauth_status():
    """Check OAuth token status."""
    from core.etsy_intel.db import get_oauth_token

    token = get_oauth_token()
    if not token:
        return {
            'connected': False,
            'message': 'No OAuth token. Visit /etsy/oauth/connect to authenticate.',
        }

    expires_at = token['expires_at']
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    expired = now >= expires_at

    return {
        'connected': True,
        'expired': expired,
        'user_id': token['user_id'],
        'scopes': token['scopes'],
        'expires_at': expires_at.isoformat(),
        'has_refresh_token': bool(token['refresh_token']),
    }


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


# ── Sales (cross-module read for manufacture sales-velocity feature) ────────

@router.get("/sales")
async def list_sales(
    days: int = Query(
        30, ge=1, le=365,
        description="Rolling window size in days. Default 30.",
    ),
    shop_id: Optional[int] = Query(
        None,
        description="Filter to a single shop. Default: all configured shops.",
    ),
    _: bool = Depends(verify_api_key),
):
    """
    Pre-aggregated Etsy sales for the last `days` days, grouped by listing_id.

    Returns one row per Etsy listing that had any sales in the window, with
    the listing's stored SKU plus total units. Built for the manufacture app's
    Sales Velocity module (Phase 2B.3) which consumes it via HTTP as a
    cross-module read — manufacture does not query Deek's Postgres directly,
    per the hard rule in `CLAUDE.md`.

    Requires `X-API-Key` header matching `DEEK_API_KEY`. The other `/etsy/*`
    routes are currently unauthenticated; this endpoint is explicitly gated
    because it crosses a module boundary.

    Defensive behaviour: rows where `etsy_listings.sku` is NULL or contains
    a comma (indicating Deek's `skus[0]` ingest collapsed a multi-SKU
    variation — see `core/etsy_intel/sync.py::_parse_receipts`) are
    excluded from the result and counted in the returned `skipped_*`
    fields so callers can detect data-quality regressions.
    """
    from core.etsy_intel.db import get_conn

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT el.shop_id,
                           el.listing_id,
                           el.sku AS external_sku,
                           SUM(es.quantity)::int AS total_quantity,
                           MIN(es.sale_date) AS first_sale_date,
                           MAX(es.sale_date) AS last_sale_date
                    FROM etsy_sales es
                    JOIN etsy_listings el ON el.listing_id = es.listing_id
                    WHERE es.sale_date >= NOW() - make_interval(days => %s)
                      AND (%s IS NULL OR el.shop_id = %s)
                    GROUP BY el.shop_id, el.listing_id, el.sku
                    ORDER BY el.listing_id
                """
                cur.execute(sql, (days, shop_id, shop_id))
                raw_rows = cur.fetchall()
    except Exception as e:
        log.exception("Failed to query etsy_sales aggregate")
        raise HTTPException(500, f"etsy_sales query failed: {e}")

    rows = []
    skipped_null_sku = 0
    skipped_multi_sku = 0
    for shop, listing, sku, qty, first_sale, last_sale in raw_rows:
        if sku is None or sku == "":
            skipped_null_sku += 1
            continue
        if "," in sku:
            # Deek's ingest collapsed a multi-SKU variation into a single
            # cell. We cannot safely attribute per-variation sales without a
            # schema change upstream — skip and count, so a regression shows.
            log.warning(
                "etsy /sales: skipping listing %s with multi-SKU value %r "
                "(expected single-SKU-per-listing model)",
                listing, sku,
            )
            skipped_multi_sku += 1
            continue
        rows.append({
            "shop_id": shop,
            "listing_id": listing,
            "external_sku": sku,
            "total_quantity": qty,
            "first_sale_date": first_sale.isoformat() if first_sale else None,
            "last_sale_date": last_sale.isoformat() if last_sale else None,
        })

    window_end = datetime.now(timezone.utc)
    return {
        "rows": rows,
        "window_days": days,
        "window_end": window_end.isoformat(),
        "shop_id_filter": shop_id,
        "row_count": len(rows),
        "skipped_null_sku": skipped_null_sku,
        "skipped_multi_sku": skipped_multi_sku,
    }


# ── Transaction-level sales (cross-module read for Ledger daily sync) ────────

@router.get("/sales/transactions")
async def list_sales_transactions(
    days: int = Query(
        7, ge=1, le=365,
        description="Rolling window size in days. Default 7.",
    ),
    shop_id: Optional[int] = Query(
        None,
        description="Filter to a single shop. Default: all configured shops.",
    ),
    _: bool = Depends(verify_api_key),
):
    """
    Transaction-level Etsy sales for the last `days` days.

    Returns one row per transaction with individual pricing. Built for
    Ledger's daily polling framework which needs per-transaction prices,
    quantities, shipping, and discounts to compute revenue.

    Requires `X-API-Key` header matching `DEEK_API_KEY`.
    """
    from core.etsy_intel.db import get_conn

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT es.transaction_id,
                           es.listing_id,
                           el.sku,
                           el.title,
                           es.price,
                           es.quantity,
                           es.shipping,
                           es.discount,
                           es.total,
                           es.sale_date,
                           COALESCE(el.currency, 'GBP') AS currency
                    FROM etsy_sales es
                    JOIN etsy_listings el ON el.listing_id = es.listing_id
                    WHERE es.sale_date >= NOW() - make_interval(days => %s)
                      AND (%s IS NULL OR es.shop_id = %s)
                      AND COALESCE(es.status, 'paid') NOT IN ('cancelled', 'refunded')
                    ORDER BY es.sale_date DESC
                """
                cur.execute(sql, (days, shop_id, shop_id))
                cols = [d[0] for d in cur.description]
                raw_rows = cur.fetchall()
    except Exception as e:
        log.exception("Failed to query etsy_sales transactions")
        raise HTTPException(500, f"etsy_sales transaction query failed: {e}")

    rows = []
    for row in raw_rows:
        r = dict(zip(cols, row))
        # Normalise dates to ISO date strings
        if r.get("sale_date") and hasattr(r["sale_date"], "date"):
            r["sale_date"] = r["sale_date"].date().isoformat()
        elif r.get("sale_date") and hasattr(r["sale_date"], "isoformat"):
            r["sale_date"] = r["sale_date"].isoformat()
        # Coerce Decimals to float for JSON
        for k in ("price", "shipping", "discount", "total"):
            if r.get(k) is not None:
                r[k] = float(r[k])
        rows.append(r)

    return rows


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
    """Push Etsy Intelligence context into Deek memory."""
    from core.etsy_intel.reports import build_deek_context
    import httpx
    import os

    context = build_deek_context()
    deek_url = os.getenv('DEEK_API_URL') or os.getenv('CAIRN_API_URL', 'http://localhost:8765')

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f'{deek_url}/memory/write',
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


# ── Deek Context ────────────────────────────────────────────────────────────

@router.get("/cairn/context")
async def deek_context():
    """Module context endpoint per DEEK_MODULES.md spec."""
    from core.etsy_intel.reports import build_deek_context
    return build_deek_context()
