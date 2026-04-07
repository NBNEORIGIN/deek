"""
Sales & Traffic analytics via SP-API.

Replaces: manual Business Report CSV upload (Seller Central → Reports → Business Reports)

Report: GET_SALES_AND_TRAFFIC_REPORT
Format: JSON (NOT TSV — different from the manual CSV)
Window: rolling 30-day (Amazon max is 60 days for this report type)

The JSON structure differs from the manual CSV, so this module has its own
parser. Output goes into ami_business_report_data — same table as the manual
upload path, so snapshots and scoring work unchanged.
"""
import json
from datetime import datetime, timedelta, timezone

from .client import Region, REGION_MARKETPLACE, REGION_MARKETPLACE_CODE, run_report


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
