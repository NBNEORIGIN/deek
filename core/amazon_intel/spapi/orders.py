"""
Orders flat file sync via SP-API.

Report: GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL
Format: TSV (tab-separated), gzip compressed
Window: 90 days on first run (backfill), 2 days rolling (subsequent runs)

Stores order lines idempotently in ami_orders.
UNIQUE on (amazon_order_id, order_item_id) — running multiple times is safe.

PII rule: buyer name, email, address, phone are NEVER stored. Skipped at
parse time. ship_country is retained for marketplace inference only.
"""
import csv
import gzip
import io
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from psycopg2.extras import execute_values

from core.amazon_intel.db import get_conn
from .client import Region, REGION_MARKETPLACE, run_report

log = logging.getLogger(__name__)

# TSV column → DB column. Value '_skip' means never store (PII or unused).
ORDERS_COLUMN_MAP = {
    'amazon-order-id':          'amazon_order_id',
    'order-item-id':            'order_item_id',
    'purchase-date':            'purchase_date',
    'payments-date':            '_skip',
    'reporting-date':           'order_date',
    'promise-date':             '_skip',
    'days-past-promise':        '_skip',
    'buyer-email':              '_skip',   # PII
    'buyer-name':               '_skip',   # PII
    'buyer-phone-number':       '_skip',   # PII
    'sku':                      'merchant_sku',
    'product-name':             'product_name',
    'quantity-purchased':       'quantity',
    'quantity-shipped':         'quantity_shipped',
    'quantity-to-ship':         'quantity_to_ship',
    'ship-service-level':       'ship_service_level',
    'recipient-name':           '_skip',   # PII
    'ship-address-1':           '_skip',   # PII
    'ship-address-2':           '_skip',   # PII
    'ship-address-3':           '_skip',   # PII
    'ship-city':                '_skip',   # PII
    'ship-state':               '_skip',   # PII
    'ship-postal-code':         '_skip',   # PII
    'ship-country':             'ship_country',
    'ship-phone-number':        '_skip',   # PII
    'item-price':               'item_price_amount',
    'item-tax':                 'item_tax_amount',
    'shipping-price':           'shipping_price_amount',
    'shipping-tax':             '_skip',
    'gift-wrap-price':          'gift_wrap_price_amount',
    'gift-wrap-tax':            '_skip',
    'item-promotion-discount':  'item_promotion_discount',
    'ship-promotion-discount':  '_skip',
    'carrier':                  '_skip',
    'tracking-number':          '_skip',
    'estimated-arrival-date':   '_skip',
    'order-channel':            'fulfillment_channel',
    'order-channel-instance':   '_skip',
    'delivery-start-date':      '_skip',
    'delivery-end-date':        '_skip',
    'delivery-time-zone':       '_skip',
    'delivery-instructions':    '_skip',
    'order-amazon-fees':        '_skip',
    'is-business-order':        'is_b2b',
    'purchase-order-number':    '_skip',
    'price-designation':        '_skip',
    'fulfilled-by':             '_skip',
    'shipment-status':          'shipment_status',
    'asin':                     'asin',
}

# Infer marketplace from ship-country (two-letter codes stored in DB)
SHIP_COUNTRY_TO_MARKETPLACE = {
    'GB': 'GB', 'DE': 'DE', 'FR': 'FR', 'ES': 'ES', 'IT': 'IT',
    'NL': 'NL', 'SE': 'SE', 'PL': 'PL', 'BE': 'BE',
    'US': 'US', 'CA': 'CA',
    'AU': 'AU', 'JP': 'JP',
    'MX': 'MX',
}

# Currency inferred from marketplace when not in price string
MARKETPLACE_CURRENCY = {
    'GB': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'ES': 'EUR', 'IT': 'EUR',
    'NL': 'EUR', 'SE': 'SEK', 'PL': 'PLN', 'BE': 'EUR',
    'US': 'USD', 'CA': 'CAD',
    'AU': 'AUD', 'JP': 'JPY',
    'MX': 'MXN',
}

REGION_DEFAULT_CURRENCY = {
    'EU': 'GBP',
    'NA': 'USD',
    'FE': 'AUD',
}


def _parse_currency_amount(raw: str) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Parse Amazon price strings. Handles:
    - "GBP 12.99" → (Decimal('12.99'), 'GBP')
    - "12.99"     → (Decimal('12.99'), None)
    - ""          → (None, None)
    """
    if not raw or not raw.strip():
        return None, None
    raw = raw.strip()
    parts = raw.split()
    if len(parts) == 2:
        currency = parts[0].upper()
        try:
            return Decimal(parts[1]), currency
        except InvalidOperation:
            pass
    try:
        return Decimal(raw), None
    except InvalidOperation:
        return None, None


def _parse_date(raw: str) -> Optional[date]:
    """Parse ISO 8601 date strings from Amazon TSV. Handles plain dates and datetime strings."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    # Plain date: '2026-04-01'
    if len(raw) == 10 and raw[4] == '-' and raw[7] == '-':
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    # Datetime with Z suffix or +offset: '2026-04-01T10:00:00Z'
    try:
        if raw.endswith('Z'):
            raw_iso = raw[:-1] + '+00:00'
        else:
            raw_iso = raw
        return datetime.fromisoformat(raw_iso).date()
    except ValueError:
        pass
    # Last resort: take first 10 chars as date
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _parse_datetime(raw: str) -> Optional[datetime]:
    """Parse ISO 8601 datetime from Amazon TSV, return tz-aware."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    try:
        # Handle 'Z' suffix
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except ValueError:
        try:
            from dateutil.parser import parse as parse_dt
            return parse_dt(raw).astimezone(timezone.utc)
        except Exception:
            return None


def _map_row(raw: dict, region: str) -> Optional[dict]:
    """
    Map raw TSV row to DB-ready dict.
    Returns None if amazon_order_id or order_item_id is missing.
    PII fields are dropped silently.
    """
    mapped: dict = {'region': region}
    currency_detected: Optional[str] = None

    for tsv_col, db_col in ORDERS_COLUMN_MAP.items():
        if db_col == '_skip':
            continue
        raw_val = raw.get(tsv_col, '').strip()
        if not raw_val:
            mapped[db_col] = None
            continue

        if db_col in ('item_price_amount', 'item_tax_amount',
                      'shipping_price_amount', 'gift_wrap_price_amount',
                      'item_promotion_discount'):
            amount, currency = _parse_currency_amount(raw_val)
            mapped[db_col] = amount
            if currency and not currency_detected:
                currency_detected = currency

        elif db_col == 'order_date':
            mapped[db_col] = _parse_date(raw_val)

        elif db_col == 'purchase_date':
            mapped[db_col] = _parse_datetime(raw_val)

        elif db_col in ('quantity', 'quantity_shipped', 'quantity_to_ship'):
            try:
                mapped[db_col] = int(raw_val)
            except (ValueError, TypeError):
                mapped[db_col] = None

        elif db_col == 'is_b2b':
            mapped[db_col] = raw_val.upper() in ('TRUE', 'YES', '1')

        elif db_col == 'ship_country':
            mapped[db_col] = raw_val[:5]

        elif db_col == 'product_name':
            mapped[db_col] = raw_val[:500]

        elif db_col == 'merchant_sku':
            mapped[db_col] = raw_val[:200]

        else:
            mapped[db_col] = raw_val[:500] if isinstance(raw_val, str) else raw_val

    # Require non-negotiable keys
    if not mapped.get('amazon_order_id') or not mapped.get('order_item_id'):
        return None
    if not mapped.get('order_date'):
        return None

    # Infer marketplace from ship_country
    ship_country = mapped.get('ship_country') or ''
    mapped['marketplace'] = SHIP_COUNTRY_TO_MARKETPLACE.get(ship_country, region)

    # Store detected currency on item_price
    mapped['item_price_currency'] = (
        currency_detected
        or MARKETPLACE_CURRENCY.get(mapped['marketplace'])
        or REGION_DEFAULT_CURRENCY.get(region, 'GBP')
    )

    # Default quantity to 1
    if mapped.get('quantity') is None:
        mapped['quantity'] = 1

    return mapped


def _parse_tsv(raw_bytes: bytes, region: str) -> list[dict]:
    """
    Decompress (if needed) and parse TSV. Returns list of mapped dicts.
    PII fields are silently dropped via ORDERS_COLUMN_MAP.
    """
    try:
        data = gzip.decompress(raw_bytes).decode('utf-8', errors='replace')
    except (gzip.BadGzipFile, OSError):
        data = raw_bytes.decode('utf-8', errors='replace')

    reader = csv.DictReader(io.StringIO(data), delimiter='\t')
    rows = []
    for raw_row in reader:
        row = _map_row(raw_row, region)
        if row:
            rows.append(row)
    return rows


def _upsert_rows(rows: list[dict]) -> tuple[int, int]:
    """
    Batch upsert via execute_values. Returns (inserted_or_updated, skipped).
    Only mutable status fields update on conflict — revenue fields are immutable.
    """
    if not rows:
        return 0, 0

    DB_COLS = [
        'amazon_order_id', 'order_item_id', 'marketplace', 'region',
        'asin', 'merchant_sku', 'product_name',
        'order_date', 'purchase_date',
        'quantity', 'quantity_shipped', 'quantity_to_ship',
        'item_price_amount', 'item_price_currency',
        'item_tax_amount', 'shipping_price_amount',
        'gift_wrap_price_amount', 'item_promotion_discount',
        'is_b2b', 'fulfillment_channel', 'ship_service_level',
        'ship_country', 'shipment_status',
    ]

    values = [
        tuple(row.get(col) for col in DB_COLS)
        for row in rows
    ]

    col_list = ', '.join(DB_COLS)
    placeholders = '(' + ', '.join(['%s'] * len(DB_COLS)) + ')'

    sql = f"""
        INSERT INTO ami_orders ({col_list})
        VALUES %s
        ON CONFLICT (amazon_order_id, order_item_id) DO UPDATE SET
            shipment_status   = EXCLUDED.shipment_status,
            quantity_shipped  = EXCLUDED.quantity_shipped,
            synced_at         = NOW()
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, values, template=placeholders, page_size=500)
            affected = cur.rowcount
        conn.commit()

    return affected, len(rows) - affected


def _resolve_missing_asins_and_m_numbers():
    """Post-insert batch UPDATE from ami_sku_mapping."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ami_orders o
                SET asin = s.asin
                FROM ami_sku_mapping s
                WHERE o.merchant_sku = s.merchant_sku
                  AND o.marketplace = s.marketplace
                  AND o.asin IS NULL
            """)
            cur.execute("""
                UPDATE ami_orders o
                SET m_number = s.m_number
                FROM ami_sku_mapping s
                WHERE o.merchant_sku = s.merchant_sku
                  AND o.marketplace = s.marketplace
                  AND o.m_number IS NULL
            """)
        conn.commit()


def sync_orders(region: Region, days_back: int = 2) -> dict:
    """
    Pull GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL for region,
    parse, and upsert to ami_orders.

    Args:
        region:    'EU', 'NA', or 'FE'
        days_back: history window (2 for regular runs, 90 for backfill)

    Returns:
        {inserted, skipped, date_range, region}
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)

    log.info("Orders sync %s: %s → %s (%d days)", region, start_date, end_date, days_back)

    raw_bytes = run_report(
        region,
        'GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL',
        marketplace_id=REGION_MARKETPLACE[region],
        data_start_time=start_date.strftime('%Y-%m-%dT00:00:00Z'),
        data_end_time=end_date.strftime('%Y-%m-%dT23:59:59Z'),
    )

    rows = _parse_tsv(raw_bytes, region)
    log.info("Orders parsed: %d rows for %s", len(rows), region)

    inserted, skipped = _upsert_rows(rows)
    _resolve_missing_asins_and_m_numbers()

    result = {
        'region': region,
        'inserted': inserted,
        'skipped': skipped,
        'total_parsed': len(rows),
        'date_range': f"{start_date} → {end_date}",
        'date_range_start': str(start_date),
        'date_range_end': str(end_date),
        'status': 'complete',
    }
    log.info("Orders sync complete: %s", result)
    return result


def backfill_orders(region: Region, days_back: int = 90) -> dict:
    """Initial 90-day population. Call once manually via API endpoint."""
    log.info("Orders BACKFILL %s: %d days", region, days_back)
    return sync_orders(region, days_back=days_back)
