"""
Sales & Traffic analytics via SP-API.

Replaces: manual Business Report CSV upload (Seller Central → Reports → Business Reports)

Report: GET_SALES_AND_TRAFFIC_REPORT
Format: JSON (NOT TSV — different from the manual CSV)
Window: rolling 30-day (Amazon max is 60 days for this report type)

Two sync functions:
- sync_analytics() — LEGACY. Was MONTH granularity into ami_business_report_legacy.
  Writes commented out 2026-04-07. Kept for reference until Sprint 2.
- sync_daily_traffic() — NEW. DAY granularity into ami_daily_traffic.
  Idempotent upsert on (marketplace, asin, date). Source of truth for sessions/traffic.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from psycopg2.extras import execute_values

from core.amazon_intel.db import get_conn
from .client import Region, REGION_MARKETPLACE, REGION_MARKETPLACE_CODE, run_report

logger = logging.getLogger(__name__)


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_pct(val) -> float | None:
    """Normalise to 0-1 range (Amazon returns 0-100 for some fields, 0-1 for others)."""
    f = _safe_float(val)
    if f is None:
        return None
    return round(f / 100.0, 6) if f > 1.0 else round(f, 6)


def _safe_amount(val) -> float | None:
    """Extract from {'amount': N, 'currencyCode': 'GBP'} or plain number."""
    if val is None:
        return None
    if isinstance(val, dict):
        return _safe_float(val.get('amount'))
    return _safe_float(val)


def parse_sales_traffic_json(content: bytes) -> list[dict]:
    """
    Parse GET_SALES_AND_TRAFFIC_REPORT JSON.

    SP-API structure:
      {
        "salesAndTrafficByAsin": [
          {
            "parentAsin": "B0...",
            "childAsin": "B0...",
            "trafficByAsin": { sessions, pageViews, buyBoxPercentage, ... },
            "salesByAsin": { unitsOrdered, orderedProductSales, ... }
          },
          ...
        ]
      }
    """
    data = json.loads(content.decode('utf-8'))
    rows = []

    for item in data.get('salesAndTrafficByAsin', []):
        child_asin = (item.get('childAsin') or '').strip()
        if not child_asin:
            continue

        traffic = item.get('trafficByAsin') or {}
        sales = item.get('salesByAsin') or {}

        rows.append({
            'parent_asin': (item.get('parentAsin') or '').strip() or None,
            'child_asin': child_asin,
            'title': None,  # Not included in SP-API report
            'sessions': int(traffic.get('sessions') or 0),
            'session_percentage': _safe_pct(traffic.get('sessionPercentage')),
            'page_views': int(traffic.get('pageViews') or 0),
            'buy_box_percentage': _safe_pct(traffic.get('buyBoxPercentage')),
            'units_ordered': int(sales.get('unitsOrdered') or 0),
            'unit_session_percentage': _safe_pct(sales.get('unitSessionPercentage')),
            'ordered_product_sales': _safe_amount(sales.get('orderedProductSales')),
            'total_order_items': int(sales.get('totalOrderItems') or 0),
        })

    return rows


def sync_analytics(region: Region = 'EU', days: int = 30) -> dict:
    """
    Pull 30-day rolling Sales & Traffic report, parse, and store.
    """
    from core.amazon_intel.db import get_conn, insert_upload, update_upload

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    content = run_report(
        region,
        'GET_SALES_AND_TRAFFIC_REPORT',
        marketplace_id=REGION_MARKETPLACE[region],
        report_options={
            'dateGranularity': 'MONTH',
            'asinGranularity': 'CHILD',
        },
        data_start_time=start.strftime('%Y-%m-%dT00:00:00Z'),
        data_end_time=end.strftime('%Y-%m-%dT23:59:59Z'),
    )

    rows = parse_sales_traffic_json(content)

    marketplace = REGION_MARKETPLACE_CODE.get(region, region)
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
    filename = f'spapi_sales_traffic_{region}_{ts}.json'
    upload_id = insert_upload(filename, 'business_report', marketplace)

    errors: list[str] = []
    stored = 0

    # LEGACY: retired 2026-04-07 — ami_business_report_data renamed to ami_business_report_legacy.
    # 30-day rolling aggregates cause double-counting at 4x daily sync frequency.
    # Replaced by ami_daily_traffic (DAY granularity) + ami_orders (order-level).
    # build_snapshots() still reads ami_business_report_legacy until Sprint 2.
    # with get_conn() as conn:
    #     with conn.cursor() as cur:
    #         for row in rows:
    #             try:
    #                 cur.execute(
    #                     """INSERT INTO ami_business_report_legacy
    #                            (upload_id, parent_asin, child_asin, title,
    #                             sessions, session_percentage, page_views,
    #                             buy_box_percentage, units_ordered,
    #                             unit_session_percentage, ordered_product_sales,
    #                             total_order_items)
    #                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
    #                     (upload_id, row['parent_asin'], row['child_asin'],
    #                      row['title'], row['sessions'], row['session_percentage'],
    #                      row['page_views'], row['buy_box_percentage'],
    #                      row['units_ordered'], row['unit_session_percentage'],
    #                      row['ordered_product_sales'], row['total_order_items']),
    #                 )
    #                 stored += 1
    #             except Exception as e:
    #                 errors.append(f"ASIN {row['child_asin']}: {e}")
    #         conn.commit()

    update_upload(upload_id, row_count=stored, skip_count=len(rows) - stored,
                  error_count=len(errors), errors=errors[:50])

    return {
        'upload_id': upload_id,
        'region': region,
        'source': 'spapi',
        'row_count': stored,
        'asin_count': len(rows),
        'error_count': len(errors),
        'errors': errors[:10],
        'status': 'complete',
    }


# ── Sprint 1: Daily Traffic Sync (DAY granularity) ────────────────────────────

def _safe_float(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, dict):
        val = val.get('amount', val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_pct_dt(val) -> float | None:
    """Normalise percentage to 0-1 range."""
    f = _safe_float(val)
    if f is None:
        return None
    return round(f / 100.0, 6) if f > 1.0 else round(f, 6)


def parse_daily_traffic_json(content: bytes, region: str) -> list[dict]:
    """
    Parse GET_SALES_AND_TRAFFIC_REPORT with dateGranularity=DAY, asinGranularity=CHILD.

    SP-API structure (day granularity):
      {
        "salesAndTrafficByAsin": [
          {
            "parentAsin": "B0...",
            "childAsin": "B0...",
            "date": "2026-04-01",
            "trafficByAsin": { sessions, pageViews, buyBoxPercentage, ... },
            "salesByAsin": { unitsOrdered, orderedProductSales, ... }
          },
          ...
        ]
      }
    """
    from .client import REGION_MARKETPLACE_CODE
    marketplace = REGION_MARKETPLACE_CODE.get(region, region)

    data = json.loads(content.decode('utf-8'))
    rows = []

    for item in data.get('salesAndTrafficByAsin', []):
        child_asin = (item.get('childAsin') or '').strip()
        if not child_asin:
            continue

        row_date = (item.get('date') or '').strip()
        if not row_date:
            continue

        traffic = item.get('trafficByAsin') or {}
        sales = item.get('salesByAsin') or {}

        units_ordered = int(sales.get('unitsOrdered') or 0)
        sessions = int(traffic.get('sessions') or 0)
        conversion_rate = round(units_ordered / sessions, 6) if sessions > 0 else None

        rows.append({
            'marketplace': marketplace,
            'region': region,
            'asin': child_asin,
            'parent_asin': (item.get('parentAsin') or '').strip() or None,
            'date': row_date,
            'sessions': sessions,
            'session_percentage': _safe_pct_dt(traffic.get('sessionPercentage')),
            'page_views': int(traffic.get('pageViews') or 0),
            'page_views_percentage': _safe_pct_dt(traffic.get('pageViewsPercentage')),
            'buy_box_percentage': _safe_pct_dt(traffic.get('buyBoxPercentage')),
            'units_ordered': units_ordered,
            'units_ordered_b2b': int(sales.get('unitsOrderedB2B') or 0),
            'ordered_product_sales': _safe_float(sales.get('orderedProductSales')),
            'ordered_product_sales_b2b': _safe_float(sales.get('orderedProductSalesB2B')),
            'total_order_items': int(sales.get('totalOrderItems') or 0),
            'total_order_items_b2b': int(sales.get('totalOrderItemsB2B') or 0),
            'conversion_rate': conversion_rate,
        })

    return rows


def _upsert_daily_traffic(rows: list[dict]) -> int:
    """
    Batch upsert to ami_daily_traffic.
    UNIQUE on (marketplace, asin, date) — idempotent regardless of sync frequency.
    Returns count of rows affected.
    """
    if not rows:
        return 0

    DB_COLS = [
        'marketplace', 'region', 'asin', 'parent_asin', 'date',
        'sessions', 'session_percentage', 'page_views', 'page_views_percentage',
        'buy_box_percentage', 'units_ordered', 'units_ordered_b2b',
        'ordered_product_sales', 'ordered_product_sales_b2b',
        'total_order_items', 'total_order_items_b2b', 'conversion_rate',
    ]

    values = [tuple(row.get(c) for c in DB_COLS) for row in rows]
    col_list = ', '.join(DB_COLS)
    placeholders = '(' + ', '.join(['%s'] * len(DB_COLS)) + ')'

    sql = f"""
        INSERT INTO ami_daily_traffic ({col_list})
        VALUES %s
        ON CONFLICT (marketplace, asin, date) DO UPDATE SET
            sessions             = EXCLUDED.sessions,
            page_views           = EXCLUDED.page_views,
            buy_box_percentage   = EXCLUDED.buy_box_percentage,
            units_ordered        = EXCLUDED.units_ordered,
            ordered_product_sales = EXCLUDED.ordered_product_sales,
            conversion_rate      = EXCLUDED.conversion_rate,
            synced_at            = NOW()
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, values, template=placeholders, page_size=500)
            affected = cur.rowcount
            # M-number resolution
            cur.execute("""
                UPDATE ami_daily_traffic t
                SET m_number = s.m_number
                FROM ami_sku_mapping s
                WHERE t.asin = s.asin
                  AND t.marketplace = s.marketplace
                  AND t.m_number IS NULL
            """)
        conn.commit()

    return affected


def sync_daily_traffic(region: Region = 'EU', days_back: int = 7) -> dict:
    """
    Pull GET_SALES_AND_TRAFFIC_REPORT with DAY granularity (not MONTH).
    Stores individual day rows to ami_daily_traffic — idempotent upsert.

    Args:
        region:    'EU', 'NA', or 'FE'
        days_back: number of days of history to pull (7 for regular, 90 for backfill)

    Returns:
        {upserted, asin_count, date_range, region}
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    logger.info("Daily traffic sync %s: %d days back", region, days_back)

    content = run_report(
        region,
        'GET_SALES_AND_TRAFFIC_REPORT',
        marketplace_id=REGION_MARKETPLACE[region],
        report_options={
            'dateGranularity': 'DAY',
            'asinGranularity': 'CHILD',
        },
        data_start_time=start.strftime('%Y-%m-%dT00:00:00Z'),
        data_end_time=end.strftime('%Y-%m-%dT23:59:59Z'),
    )

    rows = parse_daily_traffic_json(content, region)
    logger.info("Daily traffic parsed: %d ASIN-day rows for %s", len(rows), region)

    upserted = _upsert_daily_traffic(rows)

    return {
        'region': region,
        'upserted': upserted,
        'asin_count': len(rows),
        'date_range': f"{start.date()} → {end.date()}",
        'date_range_start': str(start.date()),
        'date_range_end': str(end.date()),
        'status': 'complete',
    }
