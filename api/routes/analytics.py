"""
Amazon Intelligence — Analytics API routes.

Mounted at /ami/analytics/* in the Cairn FastAPI app.
Source of truth: ami_orders (revenue), ami_daily_traffic (sessions/traffic),
ami_velocity (computed velocity + alerts).

NEVER query ami_business_report_legacy for revenue. Use ami_orders only.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/ami/analytics", tags=["Amazon Analytics"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class AlertAcknowledge(BaseModel):
    acknowledged_by: str


# ── Revenue endpoints ──────────────────────────────────────────────────────────

@router.get("/revenue")
async def get_revenue(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    marketplace: Optional[str] = Query(None, description="Filter by marketplace code (GB, DE, US...)"),
    group_by: str = Query("day", description="Grouping: day | week | month | m_number"),
):
    """
    Revenue from ami_orders (atomic order lines, idempotent upsert).
    This is the authoritative source. Do not use ami_business_report_legacy
    or ami_listing_snapshots for revenue figures — those are subject to
    double-counting from 30-day rolling aggregates.
    """
    from core.amazon_intel.db import get_conn

    trunc_map = {
        'day': "DATE_TRUNC('day', order_date)",
        'week': "DATE_TRUNC('week', order_date)",
        'month': "DATE_TRUNC('month', order_date)",
        'm_number': 'm_number',
    }
    if group_by not in trunc_map:
        raise HTTPException(400, f"group_by must be one of: {list(trunc_map)}")

    group_expr = trunc_map[group_by]

    sql = f"""
        SELECT
            {group_expr}   AS period,
            marketplace,
            SUM(quantity)  AS units,
            SUM(item_price_amount) AS revenue,
            MAX(item_price_currency) AS currency
        FROM ami_orders
        WHERE order_date BETWEEN %(start_date)s AND %(end_date)s
          AND (%(marketplace)s IS NULL OR marketplace = %(marketplace)s)
          AND (shipment_status IS NULL OR shipment_status != 'Cancelled')
        GROUP BY {group_expr}, marketplace
        ORDER BY period DESC
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                'start_date': start_date,
                'end_date': end_date,
                'marketplace': marketplace,
            })
            cols = [d[0] for d in cur.description]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                for k, v in d.items():
                    if hasattr(v, 'isoformat'):
                        d[k] = v.isoformat()
                    elif hasattr(v, '__float__'):
                        d[k] = float(v)
                rows.append(d)

    return {'rows': rows, 'count': len(rows), 'source': 'ami_orders'}


@router.get("/revenue/summary")
async def get_revenue_summary(
    marketplace: Optional[str] = Query(None, description="Filter by marketplace code"),
):
    """
    Today / WTD / MTD / YTD revenue summary from ami_orders.
    Includes period-over-period comparison.
    All figures are the authoritative source — no double-counting risk.
    """
    from core.amazon_intel.db import get_conn

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    def _sum(conn, start: date, end: date) -> float:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(SUM(item_price_amount * quantity), 0)
                FROM ami_orders
                WHERE order_date BETWEEN %(start)s AND %(end)s
                  AND (%(mkt)s IS NULL OR marketplace = %(mkt)s)
                  AND (shipment_status IS NULL OR shipment_status != 'Cancelled')
            """, {'start': start, 'end': end, 'mkt': marketplace})
            row = cur.fetchone()
        return float(row[0]) if row and row[0] else 0.0

    def _pct_change(current: float, prior: float) -> Optional[float]:
        if prior == 0:
            return None
        return round((current - prior) / prior * 100, 1)

    with get_conn() as conn:
        today_rev = _sum(conn, today, today)
        yesterday_rev = _sum(conn, today - timedelta(days=1), today - timedelta(days=1))

        wtd_rev = _sum(conn, week_start, today)
        last_week_start = week_start - timedelta(weeks=1)
        last_week_end = week_start - timedelta(days=1)
        last_wtd_rev = _sum(conn, last_week_start, last_week_end)

        mtd_rev = _sum(conn, month_start, today)
        last_month_start = (month_start - timedelta(days=1)).replace(day=1)
        last_month_end = month_start - timedelta(days=1)
        last_mtd_rev = _sum(conn, last_month_start, last_month_end)

        ytd_rev = _sum(conn, year_start, today)
        last_year_start = year_start.replace(year=year_start.year - 1)
        last_year_end = year_start - timedelta(days=1)
        last_ytd_rev = _sum(conn, last_year_start, last_year_end)

    return {
        'today': today_rev,
        'yesterday': yesterday_rev,
        'vs_yesterday_pct': _pct_change(today_rev, yesterday_rev),
        'wtd': wtd_rev,
        'last_week': last_wtd_rev,
        'vs_last_week_pct': _pct_change(wtd_rev, last_wtd_rev),
        'mtd': mtd_rev,
        'last_month': last_mtd_rev,
        'vs_last_month_pct': _pct_change(mtd_rev, last_mtd_rev),
        'ytd': ytd_rev,
        'last_year': last_ytd_rev,
        'vs_last_year_pct': _pct_change(ytd_rev, last_ytd_rev),
        'marketplace': marketplace or 'all',
        'source': 'ami_orders',
        'double_count_risk': False,
    }


# ── Traffic endpoints ──────────────────────────────────────────────────────────

@router.get("/traffic")
async def get_traffic(
    start_date: date = Query(...),
    end_date: date = Query(...),
    marketplace: Optional[str] = Query(None),
    asin: Optional[str] = Query(None),
):
    """Daily sessions, page views, Buy Box, conversion from ami_daily_traffic."""
    from core.amazon_intel.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, asin, marketplace, sessions, page_views,
                       buy_box_percentage, units_ordered, conversion_rate, m_number
                FROM ami_daily_traffic
                WHERE date BETWEEN %(start)s AND %(end)s
                  AND (%(mkt)s IS NULL OR marketplace = %(mkt)s)
                  AND (%(asin)s IS NULL OR asin = %(asin)s)
                ORDER BY date DESC, sessions DESC
            """, {'start': start_date, 'end': end_date, 'mkt': marketplace, 'asin': asin})
            cols = [d[0] for d in cur.description]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                for k, v in d.items():
                    if hasattr(v, 'isoformat'):
                        d[k] = v.isoformat()
                    elif hasattr(v, '__float__') and v is not None:
                        d[k] = float(v)
                rows.append(d)

    return {'rows': rows, 'count': len(rows)}


# ── Alerts endpoints ───────────────────────────────────────────────────────────

@router.get("/alerts")
async def get_alerts(
    acknowledged: bool = Query(False),
    marketplace: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    """Velocity alerts from ami_velocity."""
    from core.amazon_intel.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, marketplace, asin, m_number, alert,
                       velocity_7d, velocity_7d_prior, trend_pct,
                       computed_date, alert_acknowledged, alert_acknowledged_by,
                       alert_acknowledged_at
                FROM ami_velocity
                WHERE alert IS NOT NULL
                  AND alert_acknowledged = %(acked)s
                  AND (%(mkt)s IS NULL OR marketplace = %(mkt)s)
                ORDER BY computed_date DESC, alert
                LIMIT %(limit)s
            """, {'acked': acknowledged, 'mkt': marketplace, 'limit': limit})
            cols = [d[0] for d in cur.description]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                for k, v in d.items():
                    if hasattr(v, 'isoformat'):
                        d[k] = v.isoformat()
                    elif hasattr(v, '__float__') and v is not None:
                        d[k] = float(v)
                rows.append(d)

    return {'alerts': rows, 'count': len(rows)}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, body: AlertAcknowledge):
    """Mark a velocity alert as acknowledged."""
    from core.amazon_intel.db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ami_velocity
                SET alert_acknowledged = TRUE,
                    alert_acknowledged_by = %s,
                    alert_acknowledged_at = NOW()
                WHERE id = %s
                RETURNING id
            """, (body.acknowledged_by, alert_id))
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise HTTPException(404, f"Alert {alert_id} not found")
    return {'id': alert_id, 'acknowledged': True, 'acknowledged_by': body.acknowledged_by}


# ── Top products ───────────────────────────────────────────────────────────────

@router.get("/top-products")
async def get_top_products(
    marketplace: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(20, le=100),
):
    """Top products by revenue from ami_orders, with M-number lookup."""
    from core.amazon_intel.db import get_conn

    start = date.today() - timedelta(days=days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    o.asin,
                    o.m_number,
                    MAX(o.product_name) AS product_name,
                    o.marketplace,
                    SUM(o.quantity) AS units,
                    SUM(o.item_price_amount * o.quantity) AS revenue,
                    ROUND(AVG(o.item_price_amount), 2) AS avg_price,
                    MAX(o.item_price_currency) AS currency
                FROM ami_orders o
                WHERE o.order_date >= %(start)s
                  AND (%(mkt)s IS NULL OR o.marketplace = %(mkt)s)
                  AND (o.shipment_status IS NULL OR o.shipment_status != 'Cancelled')
                  AND o.asin IS NOT NULL
                GROUP BY o.asin, o.m_number, o.marketplace
                ORDER BY revenue DESC NULLS LAST
                LIMIT %(limit)s
            """, {'start': start, 'mkt': marketplace, 'limit': limit})
            cols = [d[0] for d in cur.description]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                for k, v in d.items():
                    if hasattr(v, '__float__') and v is not None:
                        d[k] = float(v)
                rows.append(d)

    return {'products': rows, 'count': len(rows), 'days': days}


# ── Data quality ───────────────────────────────────────────────────────────────

@router.get("/data-quality")
async def get_data_quality():
    """
    Data freshness and integrity check.
    Green: orders synced < 6h, double_count_risk: false
    Amber: orders synced 6-24h
    Red: orders not synced in 24h+
    """
    from core.amazon_intel.db import get_conn

    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Orders
            cur.execute("""
                SELECT MAX(synced_at), MIN(order_date), MAX(order_date), COUNT(*)
                FROM ami_orders
            """)
            o = cur.fetchone()
            orders_last_synced = o[0]
            orders_date_range = f"{o[1]} → {o[2]}" if o[1] else None
            orders_count = int(o[3]) if o[3] else 0

            # Traffic
            cur.execute("""
                SELECT MAX(synced_at), MIN(date), MAX(date), COUNT(*)
                FROM ami_daily_traffic
            """)
            t = cur.fetchone()
            traffic_last_synced = t[0]
            traffic_date_range = f"{t[1]} → {t[2]}" if t[1] else None
            traffic_count = int(t[3]) if t[3] else 0

            # Legacy table last written (should be null after Sprint 1)
            cur.execute("""
                SELECT MAX(created_at) FROM ami_business_report_legacy
            """)
            legacy_row = cur.fetchone()
            legacy_last_written = legacy_row[0] if legacy_row else None

    def _staleness(last_synced) -> str:
        if not last_synced:
            return 'never'
        if last_synced.tzinfo is None:
            last_synced = last_synced.replace(tzinfo=timezone.utc)
        hours = (now - last_synced).total_seconds() / 3600
        if hours < 6:
            return 'fresh'
        if hours < 24:
            return 'stale'
        return 'very_stale'

    orders_staleness = _staleness(orders_last_synced)

    return {
        'orders_last_synced': orders_last_synced.isoformat() if orders_last_synced else None,
        'orders_staleness': orders_staleness,
        'orders_date_range': orders_date_range,
        'orders_count': orders_count,
        'traffic_last_synced': traffic_last_synced.isoformat() if traffic_last_synced else None,
        'traffic_date_range': traffic_date_range,
        'traffic_count': traffic_count,
        'legacy_table_last_written': legacy_last_written.isoformat() if legacy_last_written else None,
        'double_count_risk': False,
        'status': orders_staleness,
        'explanation': (
            "Revenue figures come from individual order records (not aggregated reports). "
            "Each order line is stored once with a UNIQUE constraint on "
            "(amazon_order_id, order_item_id). Running the sync multiple times "
            "cannot inflate these figures."
        ),
    }


# ── Backfill endpoints ─────────────────────────────────────────────────────────

@router.post("/backfill")
async def backfill_analytics(
    region: str = Query('EU', description="Region to backfill"),
    days: int = Query(90, ge=1, le=365, description="Days of history to pull"),
):
    """
    Trigger one-time backfill for orders and daily traffic.
    Runs synchronously — may take several minutes for 90-day window.
    """
    from core.amazon_intel.spapi.orders import backfill_orders
    from core.amazon_intel.spapi.analytics import sync_daily_traffic

    results = {}

    try:
        results['orders'] = backfill_orders(region=region, days_back=days)
    except Exception as e:
        results['orders'] = {'status': 'error', 'error': str(e)}

    try:
        results['daily_traffic'] = sync_daily_traffic(region=region, days_back=days)
    except Exception as e:
        results['daily_traffic'] = {'status': 'error', 'error': str(e)}

    return {'region': region, 'days': days, 'results': results}
