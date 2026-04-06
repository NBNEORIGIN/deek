"""
Amazon SP-API client — auth, token refresh, regional routing, report lifecycle.

Auth: LWA (Login with Amazon) OAuth2 — refresh token → access token (1hr TTL)
All SP-API requests use: x-amz-access-token: {access_token}

Regions:
  EU → sellingpartnerapi-eu.amazon.com  (UK, DE, FR, IT, ES, NL, SE...)
  NA → sellingpartnerapi-na.amazon.com  (US, CA, MX)
  FE → sellingpartnerapi-fe.amazon.com  (AU, JP, SG...)
"""
import gzip
import logging
import os
import time
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

Region = Literal['EU', 'NA', 'FE']

REGION_HOSTS: dict[str, str] = {
    'EU': 'sellingpartnerapi-eu.amazon.com',
    'NA': 'sellingpartnerapi-na.amazon.com',
    'FE': 'sellingpartnerapi-fe.amazon.com',
}

MARKETPLACE_IDS: dict[str, str] = {
    'UK': 'A1F83G8C2ARO7P',
    'US': 'ATVPDKIKX0DER',
    'DE': 'A1PA6795UKMFR9',
    'FR': 'A13V1IB3VIYZZH',
    'CA': 'A2EUQ1WTGCTBG2',
    'AU': 'A39IBJ37TRP1C6',
    'IT': 'APJ6JRA9NG5V4',
    'ES': 'A1RKKUPIHCS9HS',
    'NL': 'A1805IZSGTT6HS',
    'SE': 'A2NODRKZP88ZB9',
    'MX': 'A1AM78C64UM0Y8',
}

# Primary marketplace per region (used for report requests)
REGION_MARKETPLACE: dict[str, str] = {
    'EU': MARKETPLACE_IDS['UK'],
    'NA': MARKETPLACE_IDS['US'],
    'FE': MARKETPLACE_IDS['AU'],
}

REGION_MARKETPLACE_CODE: dict[str, str] = {
    'EU': 'UK',
    'NA': 'US',
    'FE': 'AU',
}

SELLER_IDS: dict[str, str] = {
    'EU': os.getenv('AMAZON_SELLER_ID_EU', 'ANO0V0M1RQZY9'),
    'NA': os.getenv('AMAZON_SELLER_ID_NA', 'AU398HK55HDI4'),
    'FE': os.getenv('AMAZON_SELLER_ID_AU', 'A35C7AI7WDWERB'),
}

CLIENT_ID = os.getenv(
    'AMAZON_CLIENT_ID',
    'amzn1.application-oa2-client.be933583cbc1430cb46386de8df677cf',
)

# In-memory token cache: {region: (access_token, expires_at)}
_token_cache: dict[str, tuple[str, float]] = {}


class RateLimitError(Exception):
    pass


class ReportError(Exception):
    pass


def get_access_token(region: Region) -> str:
    """
    Exchange LWA refresh token for access token.
    Tokens are cached in-memory with a 60s expiry buffer.
    """
    cached = _token_cache.get(region)
    if cached and time.time() < cached[1] - 60:
        return cached[0]

    env_key = 'AMAZON_REFRESH_TOKEN_AU' if region == 'FE' else f'AMAZON_REFRESH_TOKEN_{region}'
    refresh_token = os.getenv(env_key)
    if not refresh_token:
        raise ValueError(
            f"Missing refresh token for region {region}. "
            f"Set {env_key} in .env"
        )

    client_secret = os.getenv('AMAZON_CLIENT_SECRET', '')
    if not client_secret:
        raise ValueError("Missing AMAZON_CLIENT_SECRET in .env")

    with httpx.Client(timeout=15) as client:
        resp = client.post(
            'https://api.amazon.com/auth/o2/token',
            json={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': CLIENT_ID,
                'client_secret': client_secret,
            },
        )
        resp.raise_for_status()

    data = resp.json()
    access_token = data['access_token']
    expires_in = int(data.get('expires_in', 3600))
    _token_cache[region] = (access_token, time.time() + expires_in)
    return access_token


def _headers(region: Region) -> dict[str, str]:
    return {
        'x-amz-access-token': get_access_token(region),
        'Content-Type': 'application/json',
    }


def spapi_get(region: Region, path: str, params: dict | None = None) -> dict:
    host = REGION_HOSTS[region]
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f'https://{host}{path}',
            params=params or {},
            headers=_headers(region),
        )
    if resp.status_code == 429:
        raise RateLimitError(f"Rate limited: GET {path}")
    resp.raise_for_status()
    return resp.json()


def spapi_post(region: Region, path: str, body: dict) -> dict:
    host = REGION_HOSTS[region]
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f'https://{host}{path}',
            json=body,
            headers=_headers(region),
        )
    if resp.status_code == 429:
        raise RateLimitError(f"Rate limited: POST {path}")
    resp.raise_for_status()
    return resp.json()


def spapi_patch(region: Region, path: str, body: dict, params: dict | None = None) -> dict:
    host = REGION_HOSTS[region]
    with httpx.Client(timeout=30) as client:
        resp = client.patch(
            f'https://{host}{path}',
            json=body,
            params=params or {},
            headers=_headers(region),
        )
    if resp.status_code == 429:
        raise RateLimitError(f"Rate limited: PATCH {path}")
    resp.raise_for_status()
    return resp.json()


# ── Report lifecycle ──────────────────────────────────────────────────────────

def create_report(region: Region, report_type: str,
                  marketplace_id: str | None = None,
                  report_options: dict | None = None,
                  data_start_time: str | None = None,
                  data_end_time: str | None = None) -> str:
    """Request a report. Returns reportId."""
    body: dict = {
        'reportType': report_type,
        'marketplaceIds': [marketplace_id or REGION_MARKETPLACE[region]],
    }
    if report_options:
        body['reportOptions'] = report_options
    if data_start_time:
        body['dataStartTime'] = data_start_time
    if data_end_time:
        body['dataEndTime'] = data_end_time

    data = spapi_post(region, '/reports/2021-06-30/reports', body)
    return data['reportId']


def wait_for_report(region: Region, report_id: str,
                    max_wait: int = 1800, poll_interval: int = 30) -> str:
    """
    Poll until report processing is complete.
    Returns reportDocumentId.
    Raises ReportError on CANCELLED/FATAL, TimeoutError if max_wait exceeded.
    """
    deadline = time.time() + max_wait
    elapsed = 0
    while time.time() < deadline:
        data = spapi_get(region, f'/reports/2021-06-30/reports/{report_id}')
        status = data.get('processingStatus', '')
        logger.info("SP-API report %s: status=%s elapsed=%ds", report_id, status, elapsed)
        if status == 'DONE':
            doc_id = data.get('reportDocumentId')
            if not doc_id:
                raise ReportError(f"Report {report_id} DONE but no documentId")
            return doc_id
        if status in ('CANCELLED', 'FATAL'):
            raise ReportError(f"Report {report_id} failed: status={status}")
        time.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(f"Report {report_id} not ready after {max_wait}s")


def download_report_document(region: Region, doc_id: str) -> bytes:
    """
    Download report bytes from SP-API document endpoint.
    Handles GZIP decompression automatically.
    """
    data = spapi_get(region, f'/reports/2021-06-30/documents/{doc_id}')
    url = data['url']
    compression = data.get('compressionAlgorithm', '')

    with httpx.Client(timeout=120) as client:
        resp = client.get(url)
        resp.raise_for_status()

    content = resp.content
    if compression == 'GZIP':
        content = gzip.decompress(content)
    return content


def run_report(region: Region, report_type: str, **kwargs) -> bytes:
    """
    Full report lifecycle: create → poll → download.
    Convenience wrapper. Returns raw bytes.
    """
    report_id = create_report(region, report_type, **kwargs)
    doc_id = wait_for_report(region, report_id)
    return download_report_document(region, doc_id)
