"""
Etsy API v3 client with rate limiting and pagination.

Authentication: x-api-key header with keystring:shared_secret format.
(Changed Feb 2026 — Etsy now requires both in a colon-separated header.)
Rate limit: 5 QPS / 5K QPD (Personal Access tier).
Pagination: offset/limit based.

Credentials come from environment variables:
  ETSY_API_KEY       — the keystring
  ETSY_SHARED_SECRET — the shared secret
  Header sent: x-api-key: {keystring}:{shared_secret}
"""
import os
import asyncio
import logging
from datetime import datetime, timedelta

import httpx

log = logging.getLogger(__name__)

BASE_URL = 'https://api.etsy.com/v3'
MAX_QPS = 5
PAGE_SIZE = 100          # Etsy max per request
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0      # seconds, doubled each retry


class EtsyClient:
    """Async Etsy API v3 client with rate limiting."""

    def __init__(self, api_key: str = None, shared_secret: str = None):
        self.api_key = api_key or os.getenv('ETSY_API_KEY', '')
        self.shared_secret = shared_secret or os.getenv('ETSY_SHARED_SECRET', '')
        if not self.api_key:
            raise ValueError('ETSY_API_KEY not set')
        if not self.shared_secret:
            raise ValueError('ETSY_SHARED_SECRET not set')
        # Etsy requires keystring:secret combined in the header (since Feb 2026)
        self._auth_header = f'{self.api_key}:{self.shared_secret}'
        self._semaphore = asyncio.Semaphore(MAX_QPS)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={'x-api-key': self._auth_header},
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make a rate-limited, retried request."""
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
