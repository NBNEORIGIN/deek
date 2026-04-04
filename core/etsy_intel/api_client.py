"""
Etsy API v3 client with rate limiting and pagination.

Authentication: x-api-key header with API key.
Rate limit: 5 QPS / 5K QPD (Personal Access tier).
Pagination: offset/limit based.

Credentials come from environment variables:
  ETSY_API_KEY — the API key (x-api-key header)
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

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv('ETSY_API_KEY', '')
        if not self.api_key:
            raise ValueError('ETSY_API_KEY not set')
        self._semaphore = asyncio.Semaphore(MAX_QPS)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={'x-api-key': self.api_key},
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

    async def get_shop(self, shop_id: int) -> dict:
        """GET /v3/application/shops/{shop_id}"""
        return await self._get(f'/application/shops/{shop_id}')

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
