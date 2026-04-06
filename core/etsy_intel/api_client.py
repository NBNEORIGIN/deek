"""
Etsy API v3 client with rate limiting and pagination.

Authentication:
  - API key: x-api-key header with keystring:shared_secret format
    (Changed Feb 2026 — Etsy now requires both colon-separated.)
  - OAuth 2.0: Authorization: Bearer {access_token} for scoped endpoints
    (receipts, transactions). x-api-key is still required alongside Bearer.

Rate limit: 5 QPS / 5K QPD (Personal Access tier).
Pagination: offset/limit based.

Credentials from environment:
  ETSY_API_KEY       — the keystring
  ETSY_SHARED_SECRET — the shared secret
"""
import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

BASE_URL = 'https://api.etsy.com/v3'
TOKEN_URL = 'https://api.etsy.com/v3/public/oauth/token'
MAX_QPS = 5
PAGE_SIZE = 100          # Etsy max per request
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0      # seconds, doubled each retry
REFRESH_BUFFER = timedelta(minutes=5)  # refresh token this long before expiry


class EtsyClient:
    """Async Etsy API v3 client with rate limiting and optional OAuth."""

    def __init__(self, api_key: str = None, shared_secret: str = None,
                 access_token: str = None, refresh_token: str = None,
                 token_expires_at: datetime = None):
        self.api_key = api_key or os.getenv('ETSY_API_KEY', '')
        self.shared_secret = shared_secret or os.getenv('ETSY_SHARED_SECRET', '')
        if not self.api_key:
            raise ValueError('ETSY_API_KEY not set')
        if not self.shared_secret:
            raise ValueError('ETSY_SHARED_SECRET not set')
        # Etsy requires keystring:secret combined in the header (since Feb 2026)
        self._api_key_header = f'{self.api_key}:{self.shared_secret}'

        # OAuth state (optional — enables scoped endpoints like receipts)
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_expires_at = token_expires_at

        self._semaphore = asyncio.Semaphore(MAX_QPS)
        self._client: httpx.AsyncClient | None = None

    @property
    def has_oauth(self) -> bool:
        return bool(self._access_token)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {'x-api-key': self._api_key_header}
            if self._access_token:
                headers['Authorization'] = f'Bearer {self._access_token}'
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    def _rebuild_client(self):
        """Force client rebuild on next request (after token refresh)."""
        if self._client and not self._client.is_closed:
            # Schedule close but don't await — just mark for rebuild
            self._client = None

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _maybe_refresh_token(self):
        """Refresh OAuth token if it's about to expire."""
        if not self._access_token or not self._refresh_token:
            return
        if not self._token_expires_at:
            return

        now = datetime.now(timezone.utc)
        expires = self._token_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        if now + REFRESH_BUFFER < expires:
            return  # still valid

        log.info('Etsy OAuth token expiring soon, refreshing...')
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(TOKEN_URL, json={
                    'grant_type': 'refresh_token',
                    'client_id': self.api_key,
                    'refresh_token': self._refresh_token,
                })
                resp.raise_for_status()
                data = resp.json()

            self._access_token = data['access_token']
            self._refresh_token = data['refresh_token']
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=data.get('expires_in', 3600)
            )
            self._rebuild_client()

            # Persist refreshed token to database
            from core.etsy_intel.db import save_oauth_token
            user_id = int(self._access_token.split('.')[0])
            save_oauth_token(
                user_id=user_id,
                access_token=self._access_token,
                refresh_token=self._refresh_token,
                expires_at=self._token_expires_at,
            )
            log.info('Etsy OAuth token refreshed successfully')
        except Exception as e:
            log.error('Failed to refresh Etsy OAuth token: %s', e)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make a rate-limited, retried request."""
        await self._maybe_refresh_token()
        client = await self._get_client()

        for attempt in range(MAX_RETRIES):
            async with self._semaphore:
                try:
                    resp = await client.request(method, path, **kwargs)

                    if resp.status_code == 429:
                        wait = RETRY_BACKOFF * (2 ** attempt)
                        log.warning('Etsy 429 rate limited, waiting %.1fs', wait)
                        await asyncio.sleep(wait)
                        continue

                    if resp.status_code >= 500:
                        wait = RETRY_BACKOFF * (2 ** attempt)
                        log.warning('Etsy %d server error, retrying in %.1fs',
                                    resp.status_code, wait)
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    return resp.json()

                except httpx.TimeoutException:
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_BACKOFF)
                        continue
                    raise

        raise RuntimeError(f'Etsy API failed after {MAX_RETRIES} retries: {path}')

    async def _get(self, path: str, params: dict = None) -> dict:
        return await self._request('GET', path, params=params)

    # ── Pagination helper ────────────────────────────────────────────────

    async def _paginate(self, path: str, params: dict = None,
                        results_key: str = 'results') -> list[dict]:
        """Fetch all pages of a paginated endpoint."""
        params = dict(params or {})
        params.setdefault('limit', PAGE_SIZE)
        params.setdefault('offset', 0)
        all_results = []

        while True:
            data = await self._get(path, params=params)
            results = data.get(results_key, [])
            all_results.extend(results)

            count = data.get('count', 0)
            if len(all_results) >= count or len(results) < params['limit']:
                break

            params['offset'] += params['limit']

        return all_results

    # ── Shop endpoints ───────────────────────────────────────────────────

    async def get_my_shops(self) -> list[dict]:
        """GET /v3/application/users/me/shops — discover shop IDs."""
        # This endpoint requires OAuth; for API key apps we may need shop IDs
        # directly. Try the endpoint, fall back to configured shop IDs.
        try:
            data = await self._get('/application/users/me/shops')
            return data.get('results', [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                log.warning('Cannot list shops with API key auth — '
                            'use configured ETSY_SHOP_IDS instead')
                return []
            raise

    async def get_shop(self, shop_id) -> dict:
        """GET /v3/application/shops/{shop_id} — numeric ID only."""
        return await self._get(f'/application/shops/{shop_id}')

    async def find_shop_by_name(self, shop_name: str) -> dict:
        """Find a shop by name. Returns the shop dict or raises."""
        data = await self._get('/application/shops', params={'shop_name': shop_name})
        results = data.get('results', [])
        if not results:
            raise ValueError(f'No shop found with name: {shop_name}')
        return results[0]

    async def resolve_shop(self, identifier: str) -> dict:
        """Resolve a shop by numeric ID or name string."""
        if identifier.isdigit():
            return await self.get_shop(int(identifier))
        return await self.find_shop_by_name(identifier)

    # ── Listing endpoints ────────────────────────────────────────────────

    async def get_active_listings(self, shop_id: int) -> list[dict]:
        """GET /v3/application/shops/{shop_id}/listings/active — all active listings."""
        return await self._paginate(
            f'/application/shops/{shop_id}/listings/active',
            params={'includes': 'images'},
        )

    async def get_listing(self, listing_id: int) -> dict:
        """GET /v3/application/listings/{listing_id}"""
        return await self._get(f'/application/listings/{listing_id}')

    async def get_listing_images(self, shop_id: int, listing_id: int) -> list[dict]:
        """GET /v3/application/shops/{shop_id}/listings/{listing_id}/images"""
        data = await self._get(
            f'/application/shops/{shop_id}/listings/{listing_id}/images'
        )
        return data.get('results', [])

    # ── Receipt / sales endpoints ────────────────────────────────────────

    async def get_receipts(self, shop_id: int, min_created: int = None) -> list[dict]:
        """
        GET /v3/application/shops/{shop_id}/receipts
        Note: requires OAuth token for receipt access. With API key only,
        this will return 401. Gracefully degrade.
        """
        params = {}
        if min_created:
            params['min_created'] = min_created

        try:
            return await self._paginate(
                f'/application/shops/{shop_id}/receipts',
                params=params,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                log.warning('Receipts endpoint requires OAuth — '
                            'skipping sales sync for shop %d', shop_id)
                return []
            raise

    # ── Review endpoints ─────────────────────────────────────────────────

    async def get_listing_reviews(self, listing_id: int) -> list[dict]:
        """GET /v3/application/listings/{listing_id}/reviews"""
        try:
            return await self._paginate(
                f'/application/listings/{listing_id}/reviews',
            )
        except httpx.HTTPStatusError:
            return []
