"""
Render module API routes — mounted at /render/* in Cairn.

Provides context endpoint for Claude chat injection and catalogue summary.
"""
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/render", tags=["Render"])


def _get_conn():
    """Get a psycopg2 connection to the shared Cairn DB."""
    import os
    import psycopg2
    from psycopg2.extras import RealDictCursor
    dsn = os.getenv("DATABASE_URL", "postgresql://cairn:cairn_nbne_2026@localhost:5432/cairn")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


@router.get("/health")
async def render_health():
    """Module health check."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM render_catalogue_listing")
            listings = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM render_catalogue_variant")
            variants = cur.fetchone()[0]
        conn.close()
        return {"status": "ok", "module": "render", "listings": listings, "variants": variants}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/cairn/context")
async def render_cairn_context():
    """
    Catalogue summary for Claude chat context injection.

    Returns aggregate stats, recent Amazon publishes, and unpublished variants.
    """
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            # Summary counts
            cur.execute("""
                SELECT
                  (SELECT count(*) FROM render_catalogue_listing)                                      AS total_listings,
                  (SELECT count(*) FROM render_catalogue_variant)                                      AS total_variants,
                  (SELECT count(*) FROM render_catalogue_variant WHERE amazon_status = 'live')         AS amazon_live,
                  (SELECT count(*) FROM render_catalogue_variant WHERE amazon_status = 'pending')      AS amazon_pending,
                  (SELECT count(*) FROM render_catalogue_variant WHERE amazon_status = 'unpublished')  AS amazon_unpublished,
                  (SELECT count(*) FROM render_ean_pool WHERE assigned_to IS NULL)                     AS ean_pool_remaining
            """)
            row = cur.fetchone()
            summary = {
                "total_listings":     row[0],
                "total_variants":     row[1],
                "amazon_live":        row[2],
                "amazon_pending":     row[3],
                "amazon_unpublished": row[4],
                "ean_pool_remaining": row[5],
            }

            # Recent publishes (last 10)
            cur.execute("""
                SELECT sku, title_full, amazon_asin, amazon_published_at, amazon_status
                FROM render_catalogue_variant
                WHERE amazon_published_at IS NOT NULL
                ORDER BY amazon_published_at DESC
                LIMIT 10
            """)
            recent_publishes = [
                {
                    "sku": r[0],
                    "title_full": r[1],
                    "amazon_asin": r[2],
                    "amazon_published_at": r[3].isoformat() if r[3] else None,
                    "amazon_status": r[4],
                }
                for r in cur.fetchall()
            ]

            # Unpublished variants
            cur.execute("""
                SELECT v.sku, v.title_full, l.internal_ref
                FROM render_catalogue_variant v
                JOIN render_catalogue_listing l ON l.id = v.listing_id
                WHERE v.amazon_status = 'unpublished'
                ORDER BY v.sku
            """)
            unpublished_variants = [
                {"sku": r[0], "title_full": r[1], "internal_ref": r[2]}
                for r in cur.fetchall()
            ]

        conn.close()
        return {
            "summary": summary,
            "recent_publishes": recent_publishes,
            "unpublished_variants": unpublished_variants,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
