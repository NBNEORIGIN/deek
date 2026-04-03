"""
Database schema and query helpers for Amazon Listing Intelligence.

All tables use the `ami_` prefix to namespace within Cairn's PostgreSQL.
Uses psycopg2 directly (matching core/context/indexer.py pattern).
"""
import os
import psycopg2
from contextlib import contextmanager


def get_db_url() -> str:
    return os.getenv('DATABASE_URL', 'postgresql://postgres:postgres123@localhost:5432/claw')


@contextmanager
def get_conn():
    conn = psycopg2.connect(get_db_url(), connect_timeout=5)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema():
    """Create all ami_* tables if they don't exist. Called at Cairn startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_SCHEMA)
            conn.commit()


_SQL_SCHEMA = """
-- SKU → M-number canonical mapping (seeded from stock sheet CSV)
CREATE TABLE IF NOT EXISTS ami_sku_mapping (
    id              SERIAL PRIMARY KEY,
    sku             VARCHAR(100) NOT NULL,
    m_number        VARCHAR(20) NOT NULL,
    new_sku         VARCHAR(100),
    country         VARCHAR(10),
    description     VARCHAR(500),
    blank_name      VARCHAR(100),
    is_personalised BOOLEAN DEFAULT FALSE,
    asin            VARCHAR(20),
    source          VARCHAR(30) NOT NULL DEFAULT 'stock_sheet',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ami_sku_sku
    ON ami_sku_mapping(sku);
CREATE INDEX IF NOT EXISTS idx_ami_sku_m
    ON ami_sku_mapping(m_number);
CREATE INDEX IF NOT EXISTS idx_ami_sku_asin
    ON ami_sku_mapping(asin);

-- Upload log
CREATE TABLE IF NOT EXISTS ami_uploads (
    id              SERIAL PRIMARY KEY,
    filename        VARCHAR(500) NOT NULL,
    file_type       VARCHAR(30) NOT NULL,
    marketplace     VARCHAR(10),
    row_count       INTEGER DEFAULT 0,
    skip_count      INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    errors          JSONB DEFAULT '[]',
    status          VARCHAR(20) DEFAULT 'pending',
    uploaded_at     TIMESTAMP DEFAULT NOW(),
    processed_at    TIMESTAMP
);

-- Parsed flatfile rows
CREATE TABLE IF NOT EXISTS ami_flatfile_data (
    id              SERIAL PRIMARY KEY,
    upload_id       INTEGER REFERENCES ami_uploads(id) ON DELETE CASCADE,
    sku             VARCHAR(100) NOT NULL,
    asin            VARCHAR(20),
    product_id_type VARCHAR(20),
    parent_child    VARCHAR(20),
    parent_sku      VARCHAR(100),
    product_type    VARCHAR(200),
    title           TEXT,
    brand           VARCHAR(200),
    bullet1         TEXT,
    bullet2         TEXT,
    bullet3         TEXT,
    bullet4         TEXT,
    bullet5         TEXT,
    description     TEXT,
    generic_keyword1 TEXT,
    generic_keyword2 TEXT,
    generic_keyword3 TEXT,
    generic_keyword4 TEXT,
    generic_keyword5 TEXT,
    main_image_url  VARCHAR(2000),
    image_count     INTEGER DEFAULT 0,
    your_price      NUMERIC(10,2),
    fulfilment      VARCHAR(20),
    colour          VARCHAR(200),
    size            VARCHAR(200),
    material        VARCHAR(200),
    browse_node_1   VARCHAR(200),
    browse_node_2   VARCHAR(200),
    keyword_count   INTEGER DEFAULT 0,
    bullet_count    INTEGER DEFAULT 0,
    raw_json        JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ami_ff_sku ON ami_flatfile_data(sku);
CREATE INDEX IF NOT EXISTS idx_ami_ff_asin ON ami_flatfile_data(asin);
CREATE INDEX IF NOT EXISTS idx_ami_ff_upload ON ami_flatfile_data(upload_id);

-- Business report performance data
CREATE TABLE IF NOT EXISTS ami_business_report_data (
    id                      SERIAL PRIMARY KEY,
    upload_id               INTEGER REFERENCES ami_uploads(id) ON DELETE CASCADE,
    parent_asin             VARCHAR(20),
    child_asin              VARCHAR(20) NOT NULL,
    title                   TEXT,
    sessions                INTEGER DEFAULT 0,
    session_percentage      NUMERIC(6,4),
    page_views              INTEGER DEFAULT 0,
    buy_box_percentage      NUMERIC(6,4),
    units_ordered           INTEGER DEFAULT 0,
    unit_session_percentage NUMERIC(6,4),
    ordered_product_sales   NUMERIC(12,2),
    total_order_items       INTEGER DEFAULT 0,
    created_at              TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ami_biz_asin ON ami_business_report_data(child_asin);
CREATE INDEX IF NOT EXISTS idx_ami_biz_upload ON ami_business_report_data(upload_id);

-- Advertising report data
CREATE TABLE IF NOT EXISTS ami_advertising_data (
    id              SERIAL PRIMARY KEY,
    upload_id       INTEGER REFERENCES ami_uploads(id) ON DELETE CASCADE,
    report_type     VARCHAR(30),
    campaign_name   VARCHAR(500),
    ad_group_name   VARCHAR(500),
    asin            VARCHAR(20),
    sku             VARCHAR(100),
    targeting        VARCHAR(500),
    match_type      VARCHAR(30),
    customer_search_term VARCHAR(500),
    impressions     INTEGER DEFAULT 0,
    clicks          INTEGER DEFAULT 0,
    spend           NUMERIC(10,2) DEFAULT 0,
    sales_7d        NUMERIC(12,2) DEFAULT 0,
    orders_7d       INTEGER DEFAULT 0,
    acos            NUMERIC(6,4),
    roas            NUMERIC(8,4),
    created_at      TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ami_ad_asin ON ami_advertising_data(asin);
CREATE INDEX IF NOT EXISTS idx_ami_ad_upload ON ami_advertising_data(upload_id);

-- Listing snapshots (the core analytical unit)
CREATE TABLE IF NOT EXISTS ami_listing_snapshots (
    id                  SERIAL PRIMARY KEY,
    asin                VARCHAR(100) NOT NULL,
    sku                 VARCHAR(100),
    m_number            VARCHAR(20),
    marketplace         VARCHAR(10),
    snapshot_date       DATE NOT NULL,
    -- Content (from flatfile)
    title               TEXT,
    bullet_count        INTEGER DEFAULT 0,
    image_count         INTEGER DEFAULT 0,
    has_description     BOOLEAN DEFAULT FALSE,
    keyword_count       INTEGER DEFAULT 0,
    your_price          NUMERIC(10,2),
    fulfilment          VARCHAR(20),
    brand               VARCHAR(200),
    -- Performance (from business report)
    sessions_30d        INTEGER,
    page_views_30d      INTEGER,
    conversion_rate     NUMERIC(6,4),
    buy_box_pct         NUMERIC(6,4),
    units_ordered_30d   INTEGER,
    ordered_revenue_30d NUMERIC(12,2),
    -- Ads (from advertising, aggregated)
    ad_spend_30d        NUMERIC(10,2),
    ad_impressions      INTEGER,
    ad_clicks           INTEGER,
    acos                NUMERIC(6,4),
    roas                NUMERIC(8,4),
    -- Cross-module (from Manufacture API)
    cost_price          NUMERIC(10,2),
    gross_margin        NUMERIC(6,4),
    -- Scoring output
    health_score        NUMERIC(4,1),
    issues              TEXT[],
    diagnosis_codes     TEXT[],
    recommendations     TEXT[],
    -- Provenance
    flatfile_upload_id  INTEGER,
    bizrpt_upload_id    INTEGER,
    ad_upload_id        INTEGER,
    data_sources        TEXT[],
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ami_snap_asin_date
    ON ami_listing_snapshots(asin, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ami_snap_m ON ami_listing_snapshots(m_number);
CREATE INDEX IF NOT EXISTS idx_ami_snap_score ON ami_listing_snapshots(health_score);
CREATE INDEX IF NOT EXISTS idx_ami_snap_mkt ON ami_listing_snapshots(marketplace);

-- Weekly reports
CREATE TABLE IF NOT EXISTS ami_weekly_reports (
    id                  SERIAL PRIMARY KEY,
    report_date         DATE NOT NULL,
    marketplace         VARCHAR(10),
    total_asins         INTEGER DEFAULT 0,
    avg_health_score    NUMERIC(4,1),
    critical_count      INTEGER DEFAULT 0,
    attention_count     INTEGER DEFAULT 0,
    healthy_count       INTEGER DEFAULT 0,
    no_data_count       INTEGER DEFAULT 0,
    report_json         JSONB NOT NULL,
    summary             TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ami_report_date_mkt
    ON ami_weekly_reports(report_date, marketplace);
"""


# ── Query helpers ─────────────────────────────────────────────────────────────

def insert_upload(filename: str, file_type: str, marketplace: str = None) -> int:
    """Insert an upload log entry, return its ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ami_uploads (filename, file_type, marketplace)
                   VALUES (%s, %s, %s) RETURNING id""",
                (filename, file_type, marketplace),
            )
            upload_id = cur.fetchone()[0]
            conn.commit()
            return upload_id


def update_upload(upload_id: int, *, row_count: int = 0, skip_count: int = 0,
                  error_count: int = 0, errors: list = None, status: str = 'complete'):
    """Update an upload log entry after processing."""
    import json as _json
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE ami_uploads
                   SET row_count = %s, skip_count = %s, error_count = %s,
                       errors = %s, status = %s, processed_at = NOW()
                   WHERE id = %s""",
                (row_count, skip_count, error_count,
                 _json.dumps(errors or []), status, upload_id),
            )
            conn.commit()


def list_uploads(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, filename, file_type, marketplace, row_count,
                          skip_count, error_count, status, uploaded_at, processed_at
                   FROM ami_uploads ORDER BY uploaded_at DESC LIMIT %s""",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
