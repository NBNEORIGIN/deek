"""
SP-API Product Fees v0 — feesEstimate batch sync.

Populates ami_fee_snapshots with referral + FBA + other fees per (asin, marketplace).
Called by scheduler (nightly) and on-demand via /ami/fees/sync API route.

Strategy:
  - For each marketplace, pick ASINs that have orders in ami_orders in the
    last 30 days.
  - Use the MEDIAN item_price_amount from those orders as the price point.
    (Amazon's fee calc takes listed price; recent sale prices track listing
    price closely enough for margin estimation.)
  - Batch up to 20 ASINs per POST /products/fees/v0/feesEstimate call.
  - Respect ~1 req/sec rate limit with a short sleep between batches.
  - Upsert results into ami_fee_snapshots.

Failure modes are captured per-row: api_status + api_error. We never raise
mid-sync — a single ASIN failure should not abort the whole marketplace.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from decimal import Decimal
from typing import Iterable

from ..db import get_conn
from .client import (
    MARKETPLACE_IDS,
    RateLimitError,
    Region,
    spapi_post,
)

logger = logging.getLogger(__name__)


# Marketplace → region routing (SP-API host selection).
MARKETPLACE_REGION: dict[str, str] = {
    'UK': 'EU', 'DE': 'EU', 'FR': 'EU', 'IT': 'EU', 'ES': 'EU',
    'NL': 'EU', 'SE': 'EU', 'PL': 'EU', 'BE': 'EU',
    'US': 'NA', 'CA': 'NA', 'MX': 'NA',
    'AU': 'FE', 'JP': 'FE', 'SG': 'FE',
}

# Currency per marketplace — used to tag the price point.
MARKETPLACE_CURRENCY: dict[str, str] = {
    'UK': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'IT': 'EUR', 'ES': 'EUR',
    'NL': 'EUR', 'BE': 'EUR',
    'SE': 'SEK', 'PL': 'PLN',
    'US': 'USD', 'CA': 'CAD', 'MX': 'MXN',
    'AU': 'AUD', 'JP': 'JPY', 'SG': 'SGD',
}

BATCH_SIZE = 20                  # Amazon hard cap per feesEstimate call
RATE_LIMIT_SLEEP_SEC = 1.1       # 1 req/sec burst 2; stay under the burst


def get_price_points(
    marketplace: str, lookback_days: int = 30,
) -> list[tuple[str, Decimal, bool]]:
    """
    Return [(asin, median_price, is_fba), ...] for ASINs with orders in the
    given marketplace in the last `lookback_days`. Orders without an ASIN
    or a price are excluded.

    `is_fba` is the dominant fulfillment channel for the ASIN over the
    lookback window: True if the ASIN's orders are mostly Amazon-fulfilled
    (FBA), False if mostly Merchant-fulfilled (FBM). The SP-API
    getMyFeesEstimate endpoint REQUIRES the right IsAmazonFulfilled value
    in the request — passing IsAmazonFulfilled=true for an FBM listing
    returns ClientError InvalidParameterValue. Bug surfaced 2026-05-02:
    28% of UK ASINs were failing because the code hardcoded
    IsAmazonFulfilled=True regardless of the listing's actual fulfillment
    channel. Fixed to derive from ami_orders.fulfillment_channel
    (Amazon = FBA, Merchant = FBM).

    Uses MARKETPLACE_ALIASES so that passing "UK" also matches ami_orders
    rows tagged "GB" (ami_orders uses ISO country codes).
    """
    from ..margin.quartile_brief import MARKETPLACE_ALIASES
    codes = MARKETPLACE_ALIASES.get(marketplace.upper(), [marketplace.upper()])
    # item_price_amount is the ORDER LINE total (unit_price × quantity).
    # Divide by quantity to get per-unit price for fee estimation.
    # fulfillment_channel: 'Amazon' = FBA, 'Merchant' = FBM in NBNE's data.
    sql = """
        SELECT asin,
               item_price_amount / NULLIF(quantity, 0) AS unit_price,
               fulfillment_channel
        FROM ami_orders
        WHERE marketplace = ANY(%s)
          AND asin IS NOT NULL AND asin <> ''
          AND item_price_amount IS NOT NULL
          AND item_price_amount > 0
          AND quantity > 0
          AND order_date >= (CURRENT_DATE - (%s || ' days')::interval)
    """
    rows_by_asin: dict[str, dict] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (codes, lookback_days))
            for asin, price, channel in cur.fetchall():
                if price is None:
                    continue
                bucket = rows_by_asin.setdefault(asin, {'prices': [], 'channels': []})
                bucket['prices'].append(float(price))
                if channel:
                    bucket['channels'].append(channel)

    result: list[tuple[str, Decimal, bool]] = []
    for asin, b in rows_by_asin.items():
        med = statistics.median(b['prices'])
        # Dominant channel via simple count. If no channel info at all,
        # default to FBA (the historical assumption — keeps behaviour
        # backward-compatible for ASINs we've not seen orders for).
        if b['channels']:
            fba_count = sum(1 for c in b['channels'] if (c or '').lower() in ('amazon', 'afn'))
            mfn_count = len(b['channels']) - fba_count
            is_fba = fba_count >= mfn_count  # tie → FBA
        else:
            is_fba = True
        result.append((
            asin,
            Decimal(str(med)).quantize(Decimal('0.01')),
            is_fba,
        ))
    return result


def _build_fees_request(
    marketplace_id: str,
    currency: str,
    asin: str,
    price: Decimal,
    is_fba: bool = True,
) -> dict:
    """
    One entry in FeesEstimateByIdRequestList for the batch feesEstimate call.

    Per Amazon's spec, each element wraps a FeesEstimateRequest alongside
    sibling IdType/IdValue — NOT a flat dict. The ASIN is echoed back in
    FeesEstimateIdentifier.SellerInputIdentifier so callers can match up
    results to requests.

    Two payload requirements that aren't obvious from the spec but matter
    in practice (both surfaced 2026-05-02 as the cause of a 28%
    InvalidParameterValue failure rate on UK ASINs):

      1. PriceToEstimateFees needs `Shipping` populated, even if zero,
         for some categories (most apparel + media listings). Sending
         only ListingPrice returned ClientError. Shipping=0.00 is a
         safe idiomatic value across categories.
      2. IsAmazonFulfilled must match the ACTUAL fulfillment channel
         of the listing. Passing True for an FBM listing returns
         InvalidParameterValue. The caller derives is_fba from
         ami_orders.fulfillment_channel — see get_price_points().

    Points (Japan-only rewards/cashback structure) is omitted entirely
    rather than passed as null — the SP-API treats absence as null.
    """
    return {
        'FeesEstimateRequest': {
            'MarketplaceId': marketplace_id,
            'IsAmazonFulfilled': bool(is_fba),
            'PriceToEstimateFees': {
                'ListingPrice': {
                    'CurrencyCode': currency,
                    'Amount': float(price),
                },
                'Shipping': {
                    'CurrencyCode': currency,
                    'Amount': 0.00,
                },
            },
            'Identifier': asin,
        },
        'IdType': 'ASIN',
        'IdValue': asin,
    }


def _extract_fees(result: dict) -> dict:
    """
    Pull referral/fba/variable-closing/other out of a single FeesEstimateResult.
    Returns a flat dict suitable for upsert.
    """
    out: dict = {
        'referral_fee': None,
        'fba_fee': None,
        'variable_closing_fee': None,
        'other_fees': Decimal('0'),
        'total_fees': None,
        'fee_detail': result,
        'api_status': result.get('Status'),
        'api_error': None,
    }

    err = result.get('Error') or {}
    if err:
        out['api_error'] = f"{err.get('Type', '')}: {err.get('Message', '')}".strip(': ')

    est = (result.get('FeesEstimate') or {})
    total = est.get('TotalFeesEstimate') or {}
    if 'Amount' in total:
        out['total_fees'] = Decimal(str(total['Amount'])).quantize(Decimal('0.01'))

    other = Decimal('0')
    for fee in (est.get('FeeDetailList') or []):
        ftype = (fee.get('FeeType') or '').lower()
        amount = ((fee.get('FinalFee') or {}).get('Amount')
                  or (fee.get('FeeAmount') or {}).get('Amount'))
        if amount is None:
            continue
        amount_d = Decimal(str(amount)).quantize(Decimal('0.01'))
        if 'referral' in ftype:
            out['referral_fee'] = amount_d
        elif 'fba' in ftype or 'fulfillment' in ftype:
            # Multiple FBA components can appear; sum them.
            out['fba_fee'] = (out['fba_fee'] or Decimal('0')) + amount_d
        elif 'variableclosing' in ftype or 'variable_closing' in ftype:
            out['variable_closing_fee'] = amount_d
        else:
            other += amount_d
    if other:
        out['other_fees'] = other
    return out


def _upsert_snapshot(
    asin: str, marketplace: str, region: str,
    price: Decimal, currency: str, fees: dict,
) -> None:
    sql = """
        INSERT INTO ami_fee_snapshots (
            asin, marketplace, region,
            price_point_amount, price_point_currency,
            referral_fee, fba_fee, variable_closing_fee, other_fees, total_fees,
            fee_detail, api_status, api_error, estimated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, NOW()
        )
        ON CONFLICT (asin, marketplace) DO UPDATE SET
            region                = EXCLUDED.region,
            price_point_amount    = EXCLUDED.price_point_amount,
            price_point_currency  = EXCLUDED.price_point_currency,
            referral_fee          = EXCLUDED.referral_fee,
            fba_fee               = EXCLUDED.fba_fee,
            variable_closing_fee  = EXCLUDED.variable_closing_fee,
            other_fees            = EXCLUDED.other_fees,
            total_fees            = EXCLUDED.total_fees,
            fee_detail            = EXCLUDED.fee_detail,
            api_status            = EXCLUDED.api_status,
            api_error             = EXCLUDED.api_error,
            estimated_at          = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                asin, marketplace, region,
                price, currency,
                fees['referral_fee'], fees['fba_fee'],
                fees['variable_closing_fee'], fees['other_fees'], fees['total_fees'],
                json.dumps(fees['fee_detail'], default=str),
                fees['api_status'], fees['api_error'],
            ))
            conn.commit()


def sync_fees_for_marketplace(marketplace: str, lookback_days: int = 30) -> dict:
    """
    Sync fee estimates for all recently-active ASINs in `marketplace`.
    Returns a summary dict: {marketplace, region, asin_count, success, failed, errors}.
    """
    region = MARKETPLACE_REGION.get(marketplace)
    if region is None:
        raise ValueError(f"Unknown marketplace: {marketplace}")
    marketplace_id = MARKETPLACE_IDS[marketplace]
    currency = MARKETPLACE_CURRENCY[marketplace]

    price_points = get_price_points(marketplace, lookback_days=lookback_days)
    summary = {
        'marketplace': marketplace,
        'region': region,
        'asin_count': len(price_points),
        'success': 0,
        'failed': 0,
        'errors': [],
    }
    if not price_points:
        logger.info("fees sync %s: no recently-active ASINs", marketplace)
        return summary

    logger.info(
        "fees sync %s: %d ASINs in %d batches",
        marketplace, len(price_points), (len(price_points) + BATCH_SIZE - 1) // BATCH_SIZE,
    )

    for i in range(0, len(price_points), BATCH_SIZE):
        batch = price_points[i:i + BATCH_SIZE]
        # getMyFeesEstimates takes a bare JSON array as the body — no wrapper
        # object. Confirmed by probing the live endpoint; wrapping in
        # FeesEstimateByIdRequestList returned "Missing PriceToEstimateFees"
        # while the top-level array returned 200 with per-ASIN results.
        body = [
            _build_fees_request(marketplace_id, currency, asin, price, is_fba)
            for asin, price, is_fba in batch
        ]
        try:
            resp = spapi_post(region, '/products/fees/v0/feesEstimate', body)
        except RateLimitError:
            logger.warning("fees sync %s: rate limited, backing off 10s", marketplace)
            time.sleep(10)
            try:
                resp = spapi_post(region, '/products/fees/v0/feesEstimate', body)
            except Exception as e:
                summary['failed'] += len(batch)
                summary['errors'].append(f"batch {i}: {e!r}")
                continue
        except Exception as e:
            summary['failed'] += len(batch)
            summary['errors'].append(f"batch {i}: {e!r}")
            logger.exception("fees sync %s: batch failed", marketplace)
            continue

        # Response mirrors the request: bare JSON array of FeesEstimateResult.
        # (Some SP-API doc versions show it wrapped in FeesEstimateResultList —
        # accept both defensively.)
        if isinstance(resp, list):
            results = resp
        else:
            results = (resp or {}).get('FeesEstimateResultList') or []
        # Match results back to the ASINs we sent. Amazon echoes the Identifier
        # as `FeesEstimateIdentifier.SellerInputIdentifier` (or similar).
        # We trust the ordering Amazon returns matches request ordering.
        for (asin, price, _is_fba), result in zip(batch, results):
            fees = _extract_fees(result)
            try:
                _upsert_snapshot(asin, marketplace, region, price, currency, fees)
                if fees['api_status'] == 'Success':
                    summary['success'] += 1
                else:
                    summary['failed'] += 1
            except Exception as e:
                summary['failed'] += 1
                summary['errors'].append(f"{asin}: upsert {e!r}")

        time.sleep(RATE_LIMIT_SLEEP_SEC)

    logger.info("fees sync %s: %s", marketplace, summary)
    return summary


def sync_fees_all(marketplaces: Iterable[str] | None = None) -> dict:
    """
    Sync every supported marketplace. Defaults to the eight NBNE runs.
    """
    targets = list(marketplaces) if marketplaces else ['UK', 'DE', 'FR', 'IT', 'ES', 'US', 'CA', 'AU']
    results = {}
    for mp in targets:
        try:
            results[mp] = sync_fees_for_marketplace(mp)
        except Exception as e:
            logger.exception("fees sync %s failed", mp)
            results[mp] = {'error': repr(e)}
    return results
