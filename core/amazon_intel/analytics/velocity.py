"""
Velocity compute job — runs after every full sync.

Computes daily velocity and trend alerts for all ASINs with orders in the last 90 days.
Writes to ami_velocity (upsert on marketplace + asin + computed_date).

Alert types (priority order):
  VELOCITY_DROP — was selling > 2/day, now down > 50% in 7 days
  ZERO_DAYS     — had sales, now 0 units in 7 days
  SURGE         — volume more than doubled vs prior 7 days

De-duplication: does not re-raise an alert of the same type for an ASIN+marketplace
that already has an unacknowledged alert of that type within the last 7 days.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from psycopg2.extras import execute_values

from core.amazon_intel.db import get_conn

logger = logging.getLogger(__name__)


def _get_active_asins(conn, compute_date: date) -> list[tuple[str, str]]:
    """Return (marketplace, asin) pairs with any orders in the last 90 days."""
    start = compute_date - timedelta(days=90)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT marketplace, asin
            FROM ami_orders
            WHERE order_date >= %s
              AND asin IS NOT NULL
              AND shipment_status != 'Cancelled'
        """, (start,))
        return cur.fetchall()


def _compute_window(conn, marketplace: str, asin: str,
                    compute_date: date, days: int) -> tuple[float, int]:
    """
    Sum units and count days-with-orders for a given window ending on compute_date.
    Returns (velocity, units_total).
    """
    window_start = compute_date - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(quantity), 0),
                   COUNT(DISTINCT order_date)
            FROM ami_orders
            WHERE marketplace = %s
              AND asin = %s
              AND order_date > %s
              AND order_date <= %s
              AND shipment_status != 'Cancelled'
        """, (marketplace, asin, window_start, compute_date))
        row = cur.fetchone()
    units = int(row[0]) if row else 0
    days_with_orders = int(row[1]) if row else 0
    velocity = round(units / days, 3) if units > 0 else 0.0
    return velocity, units, days_with_orders


def _compute_revenue_window(conn, marketplace: str, asin: str,
                             compute_date: date, days: int) -> float:
    """Sum item_price_amount for a window."""
    window_start = compute_date - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM(item_price_amount * quantity), 0)
            FROM ami_orders
            WHERE marketplace = %s
              AND asin = %s
              AND order_date > %s
              AND order_date <= %s
              AND shipment_status != 'Cancelled'
        """, (marketplace, asin, window_start, compute_date))
        row = cur.fetchone()
    return float(row[0]) if row and row[0] else 0.0


def _has_recent_unacked_alert(conn, marketplace: str, asin: str,
                               alert_type: str, compute_date: date) -> bool:
    """Return True if there's an unacknowledged alert of this type in the last 7 days."""
    since = compute_date - timedelta(days=7)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM ami_velocity
            WHERE marketplace = %s
              AND asin = %s
              AND alert = %s
              AND alert_acknowledged = FALSE
              AND computed_date >= %s
            LIMIT 1
        """, (marketplace, asin, alert_type, since))
        return cur.fetchone() is not None


def _classify_alert(v7: float, v7_prior: float, units_7d: int,
                    days_with_orders: int) -> Optional[str]:
    """
    Return the highest-priority alert or None.
    Priority: VELOCITY_DROP > ZERO_DAYS > SURGE
    """
    # VELOCITY_DROP: was selling > 2/day, now down > 50%
    if (v7_prior is not None and v7_prior > 2.0
            and v7 is not None and v7 < v7_prior * 0.5):
        return 'VELOCITY_DROP'

    # ZERO_DAYS: was selling, now 0 units for 7+ days
    if (units_7d == 0
            and days_with_orders is not None
            and days_with_orders > 0):
        return 'ZERO_DAYS'

    # SURGE: volume more than doubled vs prior 7 days
    if (v7_prior is not None and v7_prior > 0.5
            and v7 is not None and v7 > v7_prior * 2.0):
        return 'SURGE'

    return None


def compute_velocity(db_conn=None, compute_date: date = None) -> dict:
    """
    For every (marketplace, asin) with orders in the last 90 days:
    - Compute 7-day velocity, prior 7-day velocity, 30-day velocity
    - Compute trend_pct
    - Classify alert if conditions met
    - Upsert to ami_velocity for compute_date

    Args:
        db_conn:      psycopg2 connection (if None, opens its own)
        compute_date: date to compute for (default: today)

    Returns:
        {computed_count, alerts_raised}
    """
    if compute_date is None:
        compute_date = date.today()

    logger.info("Velocity compute: %s", compute_date)

    if db_conn is not None:
        return _run_velocity(db_conn, compute_date)
    with get_conn() as conn:
        return _run_velocity(conn, compute_date)


def _run_velocity(conn, compute_date: date) -> dict:
    try:
        active_pairs = _get_active_asins(conn, compute_date)
        logger.info("Active ASIN pairs: %d", len(active_pairs))

        rows_to_upsert = []
        alerts_raised = 0

        for marketplace, asin in active_pairs:
            # Current 7-day window
            v7, units_7d, days_current = _compute_window(
                conn, marketplace, asin, compute_date, days=7)

            # Prior 7-day window (days 8-14 ago)
            prior_end = compute_date - timedelta(days=7)
            v7_prior, _, _ = _compute_window(
                conn, marketplace, asin, prior_end, days=7)

            # 30-day window
            v30, _, _ = _compute_window(
                conn, marketplace, asin, compute_date, days=30)

            # Revenue 7d
            revenue_7d = _compute_revenue_window(
                conn, marketplace, asin, compute_date, days=7)

            # trend_pct
            if v7_prior and v7_prior > 0:
                trend_pct = round((v7 - v7_prior) / v7_prior, 4)
            else:
                trend_pct = None

            # Alert classification (check for de-dup)
            raw_alert = _classify_alert(v7, v7_prior, units_7d, days_current)
            alert = None
            if raw_alert:
                if not _has_recent_unacked_alert(conn, marketplace, asin, raw_alert, compute_date):
                    alert = raw_alert
                    alerts_raised += 1

            # Get m_number for this ASIN+marketplace
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT m_number FROM ami_sku_mapping
                    WHERE asin = %s
                    LIMIT 1
                """, (asin,))
                m_row = cur.fetchone()
            m_number = m_row[0] if m_row else None

            rows_to_upsert.append((
                marketplace, asin, m_number, compute_date,
                v7 or None, v7_prior or None, v30 or None,
                trend_pct, units_7d, revenue_7d or None,
                alert,
            ))

        if rows_to_upsert:
            sql = """
                INSERT INTO ami_velocity
                    (marketplace, asin, m_number, computed_date,
                     velocity_7d, velocity_7d_prior, velocity_30d,
                     trend_pct, units_7d, revenue_7d, alert)
                VALUES %s
                ON CONFLICT (marketplace, asin, computed_date) DO UPDATE SET
                    velocity_7d       = EXCLUDED.velocity_7d,
                    velocity_7d_prior = EXCLUDED.velocity_7d_prior,
                    velocity_30d      = EXCLUDED.velocity_30d,
                    trend_pct         = EXCLUDED.trend_pct,
                    units_7d          = EXCLUDED.units_7d,
                    revenue_7d        = EXCLUDED.revenue_7d,
                    alert             = EXCLUDED.alert
            """
            with conn.cursor() as cur:
                execute_values(cur, sql, rows_to_upsert, page_size=500)
            conn.commit()

        result = {
            'computed_count': len(rows_to_upsert),
            'alerts_raised': alerts_raised,
            'compute_date': str(compute_date),
            'status': 'complete',
        }
        logger.info("Velocity compute complete: %s", result)
        return result

    except Exception:
        raise
