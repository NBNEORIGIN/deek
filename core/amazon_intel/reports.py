"""
Weekly report generation and Cairn context endpoint.

Produces a structured report sorted by health score (worst first),
with sections for critical listings, quick wins, margin alerts,
and content audit summary.
"""
import json
from datetime import date, datetime
from core.amazon_intel.db import get_conn



def generate_weekly_report(marketplace: str = None) -> dict:
    """
    Generate a weekly health report from the latest snapshots.
    Stores in ami_weekly_reports and returns the report.
    """
    today = date.today()
    stats = _compute_report_stats(marketplace)
    top_underperformers = _get_top_underperformers(marketplace, limit=20)
    quick_wins = _get_quick_wins(marketplace)
    margin_alerts = _get_margin_alerts(marketplace)
    content_audit = _get_content_audit(marketplace)

    report = {
        'report_date': today.isoformat(),
        'marketplace': marketplace,
        'stats': stats,
        'top_underperformers': top_underperformers,
        'quick_wins': quick_wins,
        'margin_alerts': margin_alerts,
        'content_audit': content_audit,
    }

    summary = _build_summary_text(stats, quick_wins, margin_alerts, content_audit)

    # Store in database
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ami_weekly_reports
                       (report_date, marketplace, total_asins, avg_health_score,
                        critical_count, attention_count, healthy_count,
                        no_data_count, report_json, summary)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (report_date, marketplace)
                   DO UPDATE SET
                       total_asins = EXCLUDED.total_asins,
                       avg_health_score = EXCLUDED.avg_health_score,
                       critical_count = EXCLUDED.critical_count,
                       attention_count = EXCLUDED.attention_count,
                       healthy_count = EXCLUDED.healthy_count,
                       no_data_count = EXCLUDED.no_data_count,
                       report_json = EXCLUDED.report_json,
                       summary = EXCLUDED.summary""",
                (today, marketplace, stats['total'], stats.get('avg_score'),
                 stats['critical'], stats['attention'], stats['healthy'],
                 stats['no_data'], json.dumps(report, default=str), summary),
            )
            conn.commit()

    report['summary'] = summary
    return report


def _compute_report_stats(marketplace: str = None) -> dict:
    """Compute aggregate stats from latest snapshots."""
    mkt_filter = "AND marketplace = %s" if marketplace else ""
    params = [marketplace] if marketplace else []

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Latest snapshot per ASIN
            base_cte = f"""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) *
                    FROM ami_listing_snapshots
                    WHERE 1=1 {mkt_filter}
                    ORDER BY asin, snapshot_date DESC
                )
            """

            cur.execute(f"""
                {base_cte}
                SELECT
                    COUNT(*) as total,
                    ROUND(AVG(health_score)::numeric, 1) as avg_score,
                    COUNT(*) FILTER (WHERE health_score <= 4) as critical,
                    COUNT(*) FILTER (WHERE health_score > 4 AND health_score <= 7) as attention,
                    COUNT(*) FILTER (WHERE health_score > 7) as healthy,
                    COUNT(*) FILTER (WHERE sessions_30d IS NULL) as no_data
                FROM latest
            """, params)
            row = cur.fetchone()
            return {
                'total': row[0],
                'avg_score': float(row[1]) if row[1] else None,
                'critical': row[2],
                'attention': row[3],
                'healthy': row[4],
                'no_data': row[5],
            }


def _get_top_underperformers(marketplace: str = None, limit: int = 20) -> list[dict]:
    """Get the worst-performing listings."""
    mkt_filter = "AND marketplace = %s" if marketplace else ""
    params = [marketplace] if marketplace else []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) *
                    FROM ami_listing_snapshots
                    WHERE health_score IS NOT NULL {mkt_filter}
                    ORDER BY asin, snapshot_date DESC
                )
                SELECT asin, sku, m_number, title, health_score,
                       diagnosis_codes, sessions_30d, conversion_rate,
                       acos, gross_margin, bullet_count, image_count
                FROM latest
                ORDER BY health_score ASC
                LIMIT %s
            """, params + [limit])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_quick_wins(marketplace: str = None) -> dict:
    """Find listings that are easy to improve."""
    mkt_filter = "AND marketplace = %s" if marketplace else ""
    params = [marketplace] if marketplace else []

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Images needed — good conversion but few images
            cur.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) *
                    FROM ami_listing_snapshots
                    WHERE 1=1 {mkt_filter}
                    ORDER BY asin, snapshot_date DESC
                )
                SELECT COUNT(*) FROM latest
                WHERE conversion_rate > 0.10 AND image_count < 6
            """, params)
            images_needed = cur.fetchone()[0]

            # Bullets needed — getting traffic but missing bullets
            cur.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) *
                    FROM ami_listing_snapshots
                    WHERE 1=1 {mkt_filter}
                    ORDER BY asin, snapshot_date DESC
                )
                SELECT COUNT(*) FROM latest
                WHERE sessions_30d > 50 AND bullet_count < 5
            """, params)
            bullets_needed = cur.fetchone()[0]

    return {
        'images_needed': images_needed,
        'bullets_needed': bullets_needed,
    }


def _get_margin_alerts(marketplace: str = None) -> list[dict]:
    """Listings with margin below 20%."""
    mkt_filter = "AND marketplace = %s" if marketplace else ""
    params = [marketplace] if marketplace else []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) *
                    FROM ami_listing_snapshots
                    WHERE gross_margin IS NOT NULL {mkt_filter}
                    ORDER BY asin, snapshot_date DESC
                )
                SELECT asin, sku, m_number, title, gross_margin, your_price, acos
                FROM latest
                WHERE gross_margin < 0.20
                ORDER BY gross_margin ASC
                LIMIT 50
            """, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_content_audit(marketplace: str = None) -> dict:
    """Aggregate content quality stats."""
    mkt_filter = "AND marketplace = %s" if marketplace else ""
    params = [marketplace] if marketplace else []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) *
                    FROM ami_listing_snapshots
                    WHERE 1=1 {mkt_filter}
                    ORDER BY asin, snapshot_date DESC
                )
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE bullet_count < 5) as missing_bullets,
                    COUNT(*) FILTER (WHERE image_count < 6) as low_images,
                    COUNT(*) FILTER (WHERE has_description = FALSE) as no_description,
                    COUNT(*) FILTER (WHERE keyword_count = 0) as no_keywords
                FROM latest
            """, params)
            row = cur.fetchone()
            total = row[0] or 1
            return {
                'total': total,
                'missing_bullets': row[1],
                'missing_bullets_pct': round(row[1] / total * 100, 1),
                'low_images': row[2],
                'low_images_pct': round(row[2] / total * 100, 1),
                'no_description': row[3],
                'no_description_pct': round(row[3] / total * 100, 1),
                'no_keywords': row[4],
                'no_keywords_pct': round(row[4] / total * 100, 1),
            }


def _build_summary_text(stats: dict, quick_wins: dict,
                        margin_alerts: list, content_audit: dict) -> str:
    """Build a human-readable summary string."""
    parts = [
        f"{stats['total']} ASINs analysed.",
        f"{stats['critical']} critical.",
    ]
    if content_audit.get('missing_bullets'):
        parts.append(
            f"{content_audit['missing_bullets']} missing bullets "
            f"({content_audit['missing_bullets_pct']}%)."
        )
    if quick_wins.get('images_needed'):
        parts.append(f"{quick_wins['images_needed']} quick wins on images.")
    if margin_alerts:
        parts.append(f"{len(margin_alerts)} margin alerts.")
    return ' '.join(parts)


def get_latest_report(marketplace: str = None) -> dict | None:
    """Get the most recent weekly report."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if marketplace:
                cur.execute(
                    """SELECT report_date, marketplace, total_asins,
                              avg_health_score, critical_count, attention_count,
                              healthy_count, no_data_count, report_json, summary
                       FROM ami_weekly_reports
                       WHERE marketplace = %s
                       ORDER BY report_date DESC LIMIT 1""",
                    (marketplace,),
                )
            else:
                cur.execute(
                    """SELECT report_date, marketplace, total_asins,
                              avg_health_score, critical_count, attention_count,
                              healthy_count, no_data_count, report_json, summary
                       FROM ami_weekly_reports
                       ORDER BY report_date DESC LIMIT 1"""
                )
            row = cur.fetchone()
            if not row:
                return None
            return {
                'report_date': row[0].isoformat() if row[0] else None,
                'marketplace': row[1],
                'total_asins': row[2],
                'avg_health_score': float(row[3]) if row[3] else None,
                'critical_count': row[4],
                'attention_count': row[5],
                'healthy_count': row[6],
                'no_data_count': row[7],
                'report': row[8],
                'summary': row[9],
            }


def build_cairn_context() -> dict:
    """
    Build the Cairn context endpoint response per CAIRN_MODULES.md spec.
    Called by GET /ami/cairn/context.
    """
    stats = _compute_report_stats()
    quick_wins = _get_quick_wins()
    content_audit = _get_content_audit()
    margin_alerts = _get_margin_alerts()

    # Data freshness
    freshness = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for ft in ('flatfile', 'business_report', 'advertising'):
                cur.execute(
                    """SELECT MAX(uploaded_at) FROM ami_uploads
                       WHERE file_type = %s AND status = 'complete'""",
                    (ft,),
                )
                row = cur.fetchone()
                freshness[f'last_{ft}'] = row[0].isoformat() if row and row[0] else None

            cur.execute("SELECT MAX(snapshot_date) FROM ami_listing_snapshots")
            row = cur.fetchone()
            freshness['snapshot_date'] = row[0].isoformat() if row and row[0] else None

    # Top issues by count
    top_issues = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH latest AS (
                    SELECT DISTINCT ON (asin) issues
                    FROM ami_listing_snapshots
                    ORDER BY asin, snapshot_date DESC
                ),
                unnested AS (
                    SELECT unnest(issues) as issue FROM latest
                )
                SELECT issue, COUNT(*) as cnt FROM unnested
                GROUP BY issue ORDER BY cnt DESC LIMIT 10
            """)
            for row in cur.fetchall():
                top_issues.append({'code': row[0], 'count': row[1]})

    summary_text = _build_summary_text(stats, quick_wins, margin_alerts, content_audit)

    # Revenue section from ami_orders (Sprint 1 — authoritative, no double-counting)
    revenue = get_revenue_context()

    return {
        'module': 'amazon_intelligence',
        'generated_at': datetime.now().isoformat(),
        'data_freshness': freshness,
        'summary': stats,
        'top_issues': top_issues,
        'quick_wins': quick_wins,
        'margin_alerts': len(margin_alerts),
        'summary_text': summary_text,
        'revenue': revenue,
    }


def get_revenue_context() -> dict:
    """
    Pull clean revenue figures from ami_orders for Cairn chat context.
    This replaces any revenue figures previously derived from ami_listing_snapshots.
    Source: ami_orders (atomic order lines, UNIQUE on amazon_order_id+order_item_id).
    No double-counting risk regardless of sync frequency.
    """
    from datetime import timedelta, timezone

    def _sum_orders(conn, days: int) -> dict:
        start = date.today() - timedelta(days=days)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(SUM(item_price_amount * quantity), 0) AS revenue,
                    COALESCE(SUM(quantity), 0) AS units,
                    MAX(item_price_currency) AS currency
                FROM ami_orders
                WHERE order_date >= %(start)s
                  AND (shipment_status IS NULL OR shipment_status != 'Cancelled')
            """, {'start': start})
            row = cur.fetchone()
        return {
            'revenue': float(row[0]) if row and row[0] else 0.0,
            'units': int(row[1]) if row and row[1] else 0,
            'currency': row[2] if row and row[2] else 'GBP',
        }

    def _sum_orders_by_marketplace(conn, days: int) -> dict:
        start = date.today() - timedelta(days=days)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT marketplace,
                       COALESCE(SUM(item_price_amount * quantity), 0),
                       COALESCE(SUM(quantity), 0)
                FROM ami_orders
                WHERE order_date >= %(start)s
                  AND (shipment_status IS NULL OR shipment_status != 'Cancelled')
                GROUP BY marketplace
                ORDER BY SUM(item_price_amount * quantity) DESC NULLS LAST
            """, {'start': start})
            return {
                row[0]: {'revenue': float(row[1]), 'units': int(row[2])}
                for row in cur.fetchall()
            }

    def _top_products(conn, days: int, limit: int = 5) -> list:
        start = date.today() - timedelta(days=days)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT asin, m_number, MAX(product_name), marketplace,
                       SUM(quantity), COALESCE(SUM(item_price_amount * quantity), 0)
                FROM ami_orders
                WHERE order_date >= %(start)s
                  AND (shipment_status IS NULL OR shipment_status != 'Cancelled')
                  AND asin IS NOT NULL
                GROUP BY asin, m_number, marketplace
                ORDER BY SUM(item_price_amount * quantity) DESC NULLS LAST
                LIMIT %(limit)s
            """, {'start': start, 'limit': limit})
            return [
                {
                    'asin': row[0],
                    'm_number': row[1],
                    'product_name': row[2],
                    'marketplace': row[3],
                    'units': int(row[4]),
                    'revenue': float(row[5]),
                }
                for row in cur.fetchall()
            ]

    def _get_active_alerts(conn, limit: int = 5) -> list:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT marketplace, asin, m_number, alert, velocity_7d, velocity_7d_prior
                FROM ami_velocity
                WHERE alert IS NOT NULL AND alert_acknowledged = FALSE
                ORDER BY computed_date DESC
                LIMIT %s
            """, (limit,))
            return [
                {
                    'marketplace': row[0],
                    'asin': row[1],
                    'm_number': row[2],
                    'alert': row[3],
                    'velocity_7d': float(row[4]) if row[4] else None,
                    'velocity_7d_prior': float(row[5]) if row[5] else None,
                }
                for row in cur.fetchall()
            ]

    def _get_last_order_date(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(order_date) FROM ami_orders")
            row = cur.fetchone()
        return row[0].isoformat() if row and row[0] else None

    try:
        with get_conn() as conn:
            today_data = _sum_orders(conn, days=1)
            last_7 = _sum_orders(conn, days=7)
            last_30 = _sum_orders(conn, days=30)
            by_mkt = _sum_orders_by_marketplace(conn, days=30)
            top5 = _top_products(conn, days=30, limit=5)
            active_alerts = _get_active_alerts(conn, limit=5)
            last_order = _get_last_order_date(conn)

        return {
            'source': 'ami_orders',
            'double_count_risk': False,
            'today': today_data,
            'last_7_days': last_7,
            'last_30_days': last_30,
            'by_marketplace': by_mkt,
            'top_5_products_30d': top5,
            'active_alerts': active_alerts,
            'last_order_date': last_order,
        }
    except Exception as e:
        return {
            'source': 'ami_orders',
            'double_count_risk': False,
            'error': str(e),
            'today': None,
            'last_7_days': None,
            'last_30_days': None,
        }
