"""
Database schema and query helpers for Etsy Intelligence.

All tables use the `etsy_` prefix to namespace within Cairn's PostgreSQL.
Uses psycopg2 directly (matching core/amazon_intel/db.py pattern).
"""
import os
import psycopg2
from contextlib import contextmanager


def get_db_url() -> str:
    return os.getenv('DATABASE_URL', 'postgresql://cairn:cairn_nbne_2026@192.168.1.228:5432/claw')


@contextmanager
def get_conn():
    conn = psycopg2.connect(get_db_url(), connect_timeout=5)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema():
    """Create all etsy_* tables if they don't exist. Called at Cairn startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_SCHEMA)
            conn.commit()


_SQL_SCHEMA = """
-- Etsy shop registry
CREATE TABLE IF NOT EXISTS etsy_shops (
    id              SERIAL PRIMARY KEY,
    shop_id         BIGINT UNIQUE NOT NULL,
    shop_name       TEXT NOT NULL,
    url             TEXT,
    review_count    INTEGER DEFAULT 0,
    review_average  NUMERIC(3,2) DEFAULT 0,
    total_sales     INTEGER DEFAULT 0,
    active_listings INTEGER DEFAULT 0,
    last_synced     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Etsy listing data
CREATE TABLE IF NOT EXISTS etsy_listings (
    id              SERIAL PRIMARY KEY,
    listing_id      BIGINT UNIQUE NOT NULL,
    shop_id         BIGINT REFERENCES etsy_shops(shop_id),
    title           TEXT,
    description     TEXT,
    price           NUMERIC(10,2),
    currency        TEXT DEFAULT 'GBP',
    quantity        INTEGER DEFAULT 0,
    tags            TEXT[],
    materials       TEXT[],
    views           INTEGER DEFAULT 0,
    favourites      INTEGER DEFAULT 0,
    num_images      INTEGER DEFAULT 0,
    state           TEXT,
    url             TEXT,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP,
    last_synced     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- SKU mapping (connect to M-number)
    sku             TEXT,
    m_number        TEXT,

    -- Analysis fields (populated by health scoring)
    health_score    NUMERIC(3,1),
    issues          TEXT[],
    recommendations TEXT[]
);
CREATE INDEX IF NOT EXISTS idx_etsy_listings_shop
    ON etsy_listings(shop_id);
CREATE INDEX IF NOT EXISTS idx_etsy_listings_score
    ON etsy_listings(health_score);
CREATE INDEX IF NOT EXISTS idx_etsy_listings_state
    ON etsy_listings(state);

-- Etsy sales / receipts
CREATE TABLE IF NOT EXISTS etsy_sales (
    id              SERIAL PRIMARY KEY,
    receipt_id      BIGINT UNIQUE NOT NULL,
    shop_id         BIGINT REFERENCES etsy_shops(shop_id),
    listing_id      BIGINT,
    buyer_email     TEXT,
    sale_date       TIMESTAMP,
    quantity        INTEGER DEFAULT 0,
    price           NUMERIC(10,2),
    shipping        NUMERIC(10,2) DEFAULT 0,
    discount        NUMERIC(10,2) DEFAULT 0,
    total           NUMERIC(10,2),
    status          TEXT,
    last_synced     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_etsy_sales_shop
    ON etsy_sales(shop_id);
CREATE INDEX IF NOT EXISTS idx_etsy_sales_date
    ON etsy_sales(sale_date);

-- Point-in-time snapshots for trend analysis
CREATE TABLE IF NOT EXISTS etsy_listing_snapshots (
    id              SERIAL PRIMARY KEY,
    listing_id      BIGINT NOT NULL,
    snapshot_date   DATE NOT NULL,
    views_30d       INTEGER,
    favourites_30d  INTEGER,
    sales_30d       INTEGER DEFAULT 0,
    revenue_30d     NUMERIC(10,2) DEFAULT 0,
    conversion_rate NUMERIC(5,4),
    health_score    NUMERIC(3,1),
    issues          TEXT[],
    recommendations TEXT[],
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_etsy_snapshots_listing_date
    ON etsy_listing_snapshots(listing_id, snapshot_date);

-- OAuth 2.0 token storage (one row per authenticated user)
CREATE TABLE IF NOT EXISTS etsy_oauth_tokens (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT UNIQUE NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TIMESTAMP NOT NULL,
    scopes          TEXT,
    code_verifier   TEXT,
    state           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def upsert_shop(shop_data: dict) -> None:
    """Upsert a shop record."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO etsy_shops (shop_id, shop_name, url, review_count,
                                        review_average, total_sales, active_listings,
                                        last_synced)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (shop_id)
                DO UPDATE SET
                    shop_name = EXCLUDED.shop_name,
                    url = EXCLUDED.url,
                    review_count = EXCLUDED.review_count,
                    review_average = EXCLUDED.review_average,
                    total_sales = EXCLUDED.total_sales,
                    active_listings = EXCLUDED.active_listings,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                shop_data['shop_id'], shop_data['shop_name'],
                shop_data.get('url'), shop_data.get('review_count', 0),
                shop_data.get('review_average', 0), shop_data.get('total_sales', 0),
                shop_data.get('active_listings', 0),
            ))
            conn.commit()


def upsert_listings(listings: list[dict]) -> int:
    """Batch upsert listing records. Returns count upserted."""
    from psycopg2.extras import execute_values
    if not listings:
        return 0

    values = [
        (
            l['listing_id'], l['shop_id'], l.get('title'), l.get('description'),
            l.get('price'), l.get('currency', 'GBP'), l.get('quantity', 0),
            l.get('tags'), l.get('materials'),
            l.get('views', 0), l.get('favourites', 0),
            l.get('num_images', 0), l.get('state'), l.get('url'),
            l.get('created_at'), l.get('updated_at'), l.get('sku'),
        )
        for l in listings
    ]

    sql = """INSERT INTO etsy_listings
                 (listing_id, shop_id, title, description, price, currency,
                  quantity, tags, materials, views, favourites, num_images,
                  state, url, created_at, updated_at, sku, last_synced)
             VALUES %s
             ON CONFLICT (listing_id)
             DO UPDATE SET
                 title = EXCLUDED.title,
                 description = EXCLUDED.description,
                 price = EXCLUDED.price,
                 currency = EXCLUDED.currency,
                 quantity = EXCLUDED.quantity,
                 tags = EXCLUDED.tags,
                 materials = EXCLUDED.materials,
                 views = EXCLUDED.views,
                 favourites = EXCLUDED.favourites,
                 num_images = EXCLUDED.num_images,
                 state = EXCLUDED.state,
                 url = EXCLUDED.url,
                 updated_at = EXCLUDED.updated_at,
                 sku = EXCLUDED.sku,
                 last_synced = CURRENT_TIMESTAMP"""

    template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)"

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, values, template=template, page_size=200)
            conn.commit()

    return len(values)


def upsert_sales(sales: list[dict]) -> int:
    """Batch upsert sales/receipt records. Returns count upserted."""
    from psycopg2.extras import execute_values
    if not sales:
        return 0

    values = [
        (
            s['receipt_id'], s['shop_id'], s.get('listing_id'),
            s.get('buyer_email'), s.get('sale_date'),
            s.get('quantity', 0), s.get('price'),
            s.get('shipping', 0), s.get('discount', 0),
            s.get('total'), s.get('status'),
        )
        for s in sales
    ]

    sql = """INSERT INTO etsy_sales
                 (receipt_id, shop_id, listing_id, buyer_email, sale_date,
                  quantity, price, shipping, discount, total, status, last_synced)
             VALUES %s
             ON CONFLICT (receipt_id)
             DO UPDATE SET
                 listing_id = EXCLUDED.listing_id,
                 quantity = EXCLUDED.quantity,
                 price = EXCLUDED.price,
                 shipping = EXCLUDED.shipping,
                 discount = EXCLUDED.discount,
                 total = EXCLUDED.total,
                 status = EXCLUDED.status,
                 last_synced = CURRENT_TIMESTAMP"""

    template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)"

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, values, template=template, page_size=200)
            conn.commit()

    return len(values)


def upsert_snapshots(snapshots: list[dict]) -> int:
    """Batch upsert listing snapshots. Returns count upserted."""
    from psycopg2.extras import execute_values
    if not snapshots:
        return 0

    values = [
        (
            s['listing_id'], s['snapshot_date'],
            s.get('views_30d'), s.get('favourites_30d'),
            s.get('sales_30d', 0), s.get('revenue_30d', 0),
            s.get('conversion_rate'), s.get('health_score'),
            s.get('issues'), s.get('recommendations'),
        )
        for s in snapshots
    ]

    sql = """INSERT INTO etsy_listing_snapshots
                 (listing_id, snapshot_date, views_30d, favourites_30d,
                  sales_30d, revenue_30d, conversion_rate, health_score,
                  issues, recommendations)
             VALUES %s
             ON CONFLICT (listing_id, snapshot_date)
             DO UPDATE SET
                 views_30d = EXCLUDED.views_30d,
                 favourites_30d = EXCLUDED.favourites_30d,
                 sales_30d = EXCLUDED.sales_30d,
                 revenue_30d = EXCLUDED.revenue_30d,
                 conversion_rate = EXCLUDED.conversion_rate,
                 health_score = EXCLUDED.health_score,
                 issues = EXCLUDED.issues,
                 recommendations = EXCLUDED.recommendations"""

    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, values, page_size=200)
            conn.commit()

    return len(values)


def get_shops() -> list[dict]:
    """Return all synced shops."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT shop_id, shop_name, url, review_count, review_average,
                       total_sales, active_listings, last_synced
                FROM etsy_shops ORDER BY shop_name
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
            return rows


def get_listings(*, shop_id: int = None, state: str = None,
                 min_score: float = None, max_score: float = None,
                 limit: int = 50, offset: int = 0) -> dict:
    """Query listings with filters."""
    conditions = []
    params = []

    if shop_id:
        conditions.append("shop_id = %s")
        params.append(shop_id)
    if state:
        conditions.append("state = %s")
        params.append(state)
    if min_score is not None:
        conditions.append("health_score >= %s")
        params.append(min_score)
    if max_score is not None:
        conditions.append("health_score <= %s")
        params.append(max_score)

    where = ' AND '.join(conditions) if conditions else '1=1'

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT listing_id, shop_id, title, price, currency, quantity,
                       tags, views, favourites, num_images, state, url,
                       sku, health_score, issues, recommendations
                FROM etsy_listings
                WHERE {where}
                ORDER BY health_score ASC NULLS LAST
                LIMIT %s OFFSET %s
            """, params + [limit, offset])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

            cur.execute(f"SELECT COUNT(*) FROM etsy_listings WHERE {where}", params)
            total = cur.fetchone()[0]

    return {'total': total, 'limit': limit, 'offset': offset, 'listings': rows}


# ── OAuth token helpers ──────────────────────────────────────────────────────

def save_oauth_state(state: str, code_verifier: str) -> None:
    """Store PKCE state + verifier before redirect. Uses user_id=0 as placeholder."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO etsy_oauth_tokens
                    (user_id, access_token, refresh_token, expires_at,
                     state, code_verifier)
                VALUES (0, '', '', CURRENT_TIMESTAMP, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET state = EXCLUDED.state,
                              code_verifier = EXCLUDED.code_verifier,
                              updated_at = CURRENT_TIMESTAMP
            """, (state, code_verifier))
            conn.commit()


def get_oauth_state(state: str) -> dict | None:
    """Retrieve stored state + code_verifier for callback validation."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT state, code_verifier FROM etsy_oauth_tokens
                WHERE state = %s
            """, (state,))
            row = cur.fetchone()
            if not row:
                return None
            return {'state': row[0], 'code_verifier': row[1]}


def save_oauth_token(user_id: int, access_token: str, refresh_token: str,
                     expires_at, scopes: str = None) -> None:
    """Store or update OAuth tokens after code exchange or refresh."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO etsy_oauth_tokens
                    (user_id, access_token, refresh_token, expires_at, scopes,
                     state, code_verifier)
                VALUES (%s, %s, %s, %s, %s, NULL, NULL)
                ON CONFLICT (user_id)
                DO UPDATE SET access_token = EXCLUDED.access_token,
                              refresh_token = EXCLUDED.refresh_token,
                              expires_at = EXCLUDED.expires_at,
                              scopes = EXCLUDED.scopes,
                              state = NULL,
                              code_verifier = NULL,
                              updated_at = CURRENT_TIMESTAMP
            """, (user_id, access_token, refresh_token, expires_at, scopes))
            conn.commit()


def get_oauth_token() -> dict | None:
    """Get the stored OAuth token (most recent non-placeholder)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, access_token, refresh_token, expires_at, scopes
                FROM etsy_oauth_tokens
                WHERE access_token != '' AND user_id != 0
                ORDER BY updated_at DESC LIMIT 1
            """)
            row = cur.fetchone()
            if not row:
                return None
            return {
                'user_id': row[0],
                'access_token': row[1],
                'refresh_token': row[2],
                'expires_at': row[3],
                'scopes': row[4],
            }


def get_listing(listing_id: int) -> dict | None:
    """Get a single listing by listing_id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT listing_id, shop_id, title, description, price, currency,
                       quantity, tags, materials, views, favourites, num_images,
                       state, url, created_at, updated_at, sku, m_number,
                       health_score, issues, recommendations, last_synced
                FROM etsy_listings WHERE listing_id = %s
            """, (listing_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            result = dict(zip(cols, row))
            for k, v in result.items():
                if hasattr(v, 'isoformat'):
                    result[k] = v.isoformat()
            return result
