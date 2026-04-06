"""
Etsy Intelligence API routes.

Mounted at /etsy/* in the Cairn FastAPI app.
"""
import os
import secrets
import hashlib
import base64
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from typing import Optional
import httpx

router = APIRouter(prefix="/etsy", tags=["Etsy Intelligence"])

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
