"""
Report generation and Cairn context endpoint for Etsy Intelligence.

Mirrors the AMI pattern: weekly health report + context endpoint
for the business brain dashboard.
"""
from datetime import date, datetime
from core.etsy_intel.db import get_conn


def build_cairn_context() -> dict:
    """
    Build the Cairn context endpoint response.
    Called by GET /etsy/cairn/context.
    """
    shops = _get_shop_summaries()
    stats = _compute_stats()
    top_issues = _get_top_issues()
    sales_30d = _get_sales_summary()
    quick_wins = _get_quick_wins()

    summary_parts = [
        f"{stats['total_listings']} Etsy listings.",
        f"{stats['critical']} critical.",
    ]
    if top_issues:
        top = top_issues[0]
        summary_parts.append(f"{top['count']} need {top['code'].lower().replace('_', ' ')}.")
    if sales_30d['orders'] > 0:
        summary_parts.append(
            f"{sales_30d['orders']} sales last 30d "
            f"(\u00a3{sales_30d['revenue']:,.2f})."
        )
    summary_text = ' '.join(summary_parts)

    return {
        'module': 'etsy_intelligence',
        'generated_at': datetime.now().isoformat(),
        'shops': shops,
        'summary': {
            'total_listings': stats['total_listings'],
            'critical': stats['critical'],
            'attention': stats['attention'],
            'healthy': stats['healthy'],
            'avg_score': stats['avg_score'],
        },
        'top_issues': top_issues,
        'sales_30d': sales_30d,
        'quick_wins': quick_wins,
        'summary_text': summary_text,
    }


def generate_report() -> dict:
    """Generate a health report from current data."""
    stats = _compute_stats()
    underperformers = _get_underperformers(limit=20)
    top_issues = _get_top_issues()
    sales_30d = _get_sales_summary()
    quick_wins = _get_quick_wins()

    return {
        'report_date': date.today().isoformat(),
        'stats': stats,
        'underperformers': underperformers,
        'top_issues': top_issues,
        'sales_30d': sales_30d,
        'quick_wins': quick_wins,
    }


def _get_shop_summaries() -> list[dict]:
    """Get summary for each shop."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT shop_name, active_listings, total_sales, last_synced
                FROM etsy_shops ORDER BY shop_name
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
            return rows


def _compute_stats() -> dict:
    """Aggregate stats from listings table."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    ROUND(AVG(health_score)::numeric, 1) as avg_score,
                    COUNT(*) FILTER (WHERE health_score <= 4) as critical,
                    COUNT(*) FILTER (WHERE health_score > 4 AND health_score <= 7) as attention,
                    COUNT(*) FILTER (WHERE health_score > 7) as healthy
                FROM etsy_listings
                WHERE state = 'active' AND health_score IS NOT NULL
            """)
            row = cur.fetchone()
            return {
                'total_listings': row[0] or 0,
                'avg_score': float(row[1]) if row[1] else None,
                'critical': row[2] or 0,
                'attention': row[3] or 0,
                'healthy': row[4] or 0,
            }


def _get_top_issues() -> list[dict]:
    """Top issues by frequency across all active listings."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH unnested AS (
                    SELECT unnest(issues) as issue
                    FROM etsy_listings
                    WHERE state = 'active' AND issues IS NOT NULL
                )
                SELECT issue, COUNT(*) as cnt
                FROM unnested
                GROUP BY issue
                ORDER BY cnt DESC
                LIMIT 10
            """)
            return [{'code': row[0], 'count': row[1]} for row in cur.fetchall()]


def _get_sales_summary() -> dict:
    """30-day sales summary across all shops."""
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=30)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as orders,
                    COALESCE(SUM(total), 0) as revenue
                FROM etsy_sales
                WHERE sale_date >= %s
            """, (cutoff,))
            row = cur.fetchone()
            orders = row[0] or 0
            revenue = float(row[1]) if row[1] else 0
            avg_order = round(revenue / orders, 2) if orders > 0 else 0
            return {
                'orders': orders,
                'revenue': round(revenue, 2),
                'avg_order': avg_order,
            }


def _get_quick_wins() -> dict:
    """Count listings that are easy to improve."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE tags IS NULL OR array_length(tags, 1) IS NULL
                              OR array_length(tags, 1) < 10
                    ) as tags_needed,
                    COUNT(*) FILTER (
                        WHERE num_images < 5
                    ) as images_needed
                FROM etsy_listings
                WHERE state = 'active'
            """)
            row = cur.fetchone()
            return {
                'tags_needed': row[0] or 0,
                'images_needed': row[1] or 0,
            }


def _get_underperformers(limit: int = 20) -> list[dict]:
    """Get worst-performing active listings."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT listing_id, title, price, health_score, issues,
                       views, favourites, num_images, tags
                FROM etsy_listings
                WHERE state = 'active' AND health_score IS NOT NULL
                ORDER BY health_score ASC
                LIMIT %s
            """, (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
