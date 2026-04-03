"""
Snapshot assembly — joins flatfile content, business report performance,
advertising data, and Manufacture margin data into a single scoreable
row per ASIN.
"""
import json
from datetime import date
from core.amazon_intel.db import get_conn
from core.amazon_intel.scoring import calculate_health_score
from core.amazon_intel.diagnosis import run_diagnosis


async def build_snapshots(marketplace: str = None) -> dict:
    """
    Assemble listing_snapshots from the latest uploaded data.
    1. Start with flatfile data (keyed by SKU/ASIN)
    2. Left-join business report data on ASIN
    3. Left-join advertising data on ASIN (aggregated)
    4. Left-join Manufacture margin data via M-number
    5. Run health scoring + diagnosis
    6. Upsert into ami_listing_snapshots
    """
    today = date.today()
    flatfile_rows = _get_latest_flatfile_data()
    biz_data = _get_latest_business_data()
    ad_data = _get_aggregated_ad_data()

    # Get all unique M-numbers for Manufacture API lookup
    m_numbers = set()
    sku_to_m = _get_sku_to_m_mapping()

    for row in flatfile_rows:
        m = sku_to_m.get(row['sku'])
        if m:
            m_numbers.add(m)

    # Fetch Manufacture data (graceful degradation if offline)
    manufacture_data = {}
    if m_numbers:
        try:
            from core.amazon_intel.manufacture_client import batch_product_data
            manufacture_data = await batch_product_data(list(m_numbers))
        except Exception:
            pass  # proceed without margin data

    snapshots = []
    for row in flatfile_rows:
        asin = row.get('asin')
        sku = row['sku']
        m_number = sku_to_m.get(sku)

        # Build snapshot dict
        snap = {
            'asin': asin or sku,  # use SKU as fallback identifier
            'sku': sku,
            'm_number': m_number,
            'marketplace': marketplace,
            'snapshot_date': today,
            'title': row.get('title'),
            'bullet_count': row.get('bullet_count', 0),
            'image_count': row.get('image_count', 0),
            'has_description': bool(row.get('description')),
            'keyword_count': row.get('keyword_count', 0),
            'your_price': row.get('your_price'),
            'fulfilment': row.get('fulfilment'),
            'brand': row.get('brand'),
            'flatfile_upload_id': row.get('upload_id'),
        }

        # Join business report data
        biz = biz_data.get(asin) if asin else None
        if biz:
            snap['sessions_30d'] = biz.get('sessions', 0)
            snap['page_views_30d'] = biz.get('page_views', 0)
            snap['conversion_rate'] = biz.get('unit_session_percentage')
            snap['buy_box_pct'] = biz.get('buy_box_percentage')
            snap['units_ordered_30d'] = biz.get('units_ordered', 0)
            snap['ordered_revenue_30d'] = biz.get('ordered_product_sales')
            snap['bizrpt_upload_id'] = biz.get('upload_id')

        # Join advertising data (aggregated per ASIN)
        ad = ad_data.get(asin) if asin else None
        if ad:
            snap['ad_spend_30d'] = ad.get('spend')
            snap['ad_impressions'] = ad.get('impressions')
            snap['ad_clicks'] = ad.get('clicks')
            snap['acos'] = ad.get('acos')
            snap['roas'] = ad.get('roas')
            snap['ad_upload_id'] = ad.get('upload_id')

        # Join Manufacture data
        if m_number and m_number in manufacture_data:
            mfg = manufacture_data[m_number]
            snap['cost_price'] = mfg.get('cost_price')
            # Calculate gross margin if we have price and cost
            if snap.get('your_price') and mfg.get('cost_price'):
                snap['gross_margin'] = round(
                    (snap['your_price'] - mfg['cost_price']) / snap['your_price'], 4
                )

        # Run scoring and diagnosis
        snap['health_score'] = calculate_health_score(snap)
        diagnosis_result = run_diagnosis(snap)
        snap['issues'] = diagnosis_result['issues']
        snap['diagnosis_codes'] = diagnosis_result['diagnosis_codes']
        snap['recommendations'] = diagnosis_result['recommendations']

        # Data sources tracking
        sources = ['flatfile']
        if biz:
            sources.append('business_report')
        if ad:
            sources.append('advertising')
        if m_number and m_number in manufacture_data:
            sources.append('manufacture')
        snap['data_sources'] = sources

        snapshots.append(snap)

    # Upsert into database
    stored = _store_snapshots(snapshots)

    return {
        'snapshot_date': today.isoformat(),
        'total_listings': len(flatfile_rows),
        'snapshots_created': stored,
        'with_performance_data': sum(1 for s in snapshots if s.get('sessions_30d') is not None),
        'with_ad_data': sum(1 for s in snapshots if s.get('ad_spend_30d') is not None),
        'with_margin_data': sum(1 for s in snapshots if s.get('gross_margin') is not None),
    }


def _get_latest_flatfile_data() -> list[dict]:
    """Get flatfile data from the most recent upload."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Find the latest flatfile upload(s)
            cur.execute(
                """SELECT id FROM ami_uploads
                   WHERE file_type = 'flatfile' AND status = 'complete'
                   ORDER BY uploaded_at DESC LIMIT 10"""
            )
            upload_ids = [row[0] for row in cur.fetchall()]
            if not upload_ids:
                return []

            placeholders = ','.join(['%s'] * len(upload_ids))
            cur.execute(
                f"""SELECT upload_id, sku, asin, parent_child, parent_sku,
                           product_type, title, brand, bullet_count, image_count,
                           description, keyword_count, your_price, fulfilment,
                           colour, size, material
                    FROM ami_flatfile_data
                    WHERE upload_id IN ({placeholders})""",
                upload_ids,
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_latest_business_data() -> dict[str, dict]:
    """Get business report data from the most recent upload, keyed by child ASIN."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM ami_uploads
                   WHERE file_type = 'business_report' AND status = 'complete'
                   ORDER BY uploaded_at DESC LIMIT 1"""
            )
            row = cur.fetchone()
            if not row:
                return {}
            upload_id = row[0]

            cur.execute(
                """SELECT upload_id, child_asin, sessions, page_views,
                          buy_box_percentage, units_ordered,
                          unit_session_percentage, ordered_product_sales
                   FROM ami_business_report_data
                   WHERE upload_id = %s""",
                (upload_id,),
            )
            cols = [d[0] for d in cur.description]
            result = {}
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                result[d['child_asin']] = d
            return result


def _get_aggregated_ad_data() -> dict[str, dict]:
    """Get advertising data aggregated per ASIN from the most recent upload."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM ami_uploads
                   WHERE file_type = 'advertising' AND status = 'complete'
                   ORDER BY uploaded_at DESC LIMIT 1"""
            )
            row = cur.fetchone()
            if not row:
                return {}
            upload_id = row[0]

            cur.execute(
                """SELECT asin,
                          SUM(impressions) as impressions,
                          SUM(clicks) as clicks,
                          SUM(spend) as spend,
                          SUM(sales_7d) as sales,
                          SUM(orders_7d) as orders
                   FROM ami_advertising_data
                   WHERE upload_id = %s AND asin IS NOT NULL
                   GROUP BY asin""",
                (upload_id,),
            )
            result = {}
            for row in cur.fetchall():
                asin = row[0]
                spend = float(row[3]) if row[3] else 0
                sales = float(row[4]) if row[4] else 0
                result[asin] = {
                    'upload_id': upload_id,
                    'impressions': row[1],
                    'clicks': row[2],
                    'spend': spend,
                    'sales': sales,
                    'acos': round(spend / sales, 4) if sales > 0 else None,
                    'roas': round(sales / spend, 4) if spend > 0 else None,
                }
            return result


def _get_sku_to_m_mapping() -> dict[str, str]:
    """Load the full SKU→M-number mapping."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sku, m_number FROM ami_sku_mapping")
            return {row[0]: row[1] for row in cur.fetchall()}


def _store_snapshots(snapshots: list[dict]) -> int:
    """Upsert snapshots into ami_listing_snapshots. Returns count stored."""
    stored = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for s in snapshots:
                try:
                    cur.execute("SAVEPOINT snap_save")
                    cur.execute(
                        """INSERT INTO ami_listing_snapshots
                               (asin, sku, m_number, marketplace, snapshot_date,
                                title, bullet_count, image_count, has_description,
                                keyword_count, your_price, fulfilment, brand,
                                sessions_30d, page_views_30d, conversion_rate,
                                buy_box_pct, units_ordered_30d, ordered_revenue_30d,
                                ad_spend_30d, ad_impressions, ad_clicks, acos, roas,
                                cost_price, gross_margin,
                                health_score, issues, diagnosis_codes, recommendations,
                                flatfile_upload_id, bizrpt_upload_id, ad_upload_id,
                                data_sources)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (asin, snapshot_date)
                           DO UPDATE SET
                               sku = EXCLUDED.sku,
                               m_number = EXCLUDED.m_number,
                               title = EXCLUDED.title,
                               bullet_count = EXCLUDED.bullet_count,
                               image_count = EXCLUDED.image_count,
                               has_description = EXCLUDED.has_description,
                               keyword_count = EXCLUDED.keyword_count,
                               your_price = EXCLUDED.your_price,
                               sessions_30d = EXCLUDED.sessions_30d,
                               conversion_rate = EXCLUDED.conversion_rate,
                               buy_box_pct = EXCLUDED.buy_box_pct,
                               health_score = EXCLUDED.health_score,
                               issues = EXCLUDED.issues,
                               diagnosis_codes = EXCLUDED.diagnosis_codes,
                               recommendations = EXCLUDED.recommendations,
                               data_sources = EXCLUDED.data_sources""",
                        (s.get('asin'), s.get('sku'), s.get('m_number'),
                         s.get('marketplace'), s.get('snapshot_date'),
                         s.get('title'), s.get('bullet_count', 0),
                         s.get('image_count', 0), s.get('has_description', False),
                         s.get('keyword_count', 0), s.get('your_price'),
                         s.get('fulfilment'), s.get('brand'),
                         s.get('sessions_30d'), s.get('page_views_30d'),
                         s.get('conversion_rate'), s.get('buy_box_pct'),
                         s.get('units_ordered_30d'), s.get('ordered_revenue_30d'),
                         s.get('ad_spend_30d'), s.get('ad_impressions'),
                         s.get('ad_clicks'), s.get('acos'), s.get('roas'),
                         s.get('cost_price'), s.get('gross_margin'),
                         s.get('health_score'), s.get('issues'),
                         s.get('diagnosis_codes'), s.get('recommendations'),
                         s.get('flatfile_upload_id'), s.get('bizrpt_upload_id'),
                         s.get('ad_upload_id'), s.get('data_sources')),
                    )
                    stored += 1
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT snap_save")

            conn.commit()
    return stored


def query_snapshots(*, marketplace: str = None, min_score: float = None,
                    max_score: float = None, diagnosis: str = None,
                    limit: int = 50, offset: int = 0,
                    order_by: str = 'health_score ASC') -> dict:
    """Query snapshots with filters."""
    conditions = []
    params = []

    # Only return the latest snapshot per ASIN
    conditions.append(
        """(asin, snapshot_date) IN (
            SELECT asin, MAX(snapshot_date) FROM ami_listing_snapshots GROUP BY asin
        )"""
    )

    if marketplace:
        conditions.append("marketplace = %s")
        params.append(marketplace)
    if min_score is not None:
        conditions.append("health_score >= %s")
        params.append(min_score)
    if max_score is not None:
        conditions.append("health_score <= %s")
        params.append(max_score)
    if diagnosis:
        conditions.append("diagnosis_codes @> ARRAY[%s]")
        params.append(diagnosis)

    where = ' AND '.join(conditions) if conditions else '1=1'

    # Sanitise order_by to prevent injection
    allowed_orders = {
        'health_score ASC', 'health_score DESC',
        'sessions_30d ASC', 'sessions_30d DESC',
        'conversion_rate ASC', 'conversion_rate DESC',
        'your_price ASC', 'your_price DESC',
    }
    if order_by not in allowed_orders:
        order_by = 'health_score ASC'

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT asin, sku, m_number, title, health_score,
                           diagnosis_codes, sessions_30d, conversion_rate,
                           acos, your_price, bullet_count, image_count,
                           marketplace, snapshot_date
                    FROM ami_listing_snapshots
                    WHERE {where}
                    ORDER BY {order_by} NULLS LAST
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

            # Serialise date objects
            for r in rows:
                for k, v in r.items():
                    if hasattr(v, 'isoformat'):
                        r[k] = v.isoformat()
                    elif isinstance(v, list):
                        r[k] = v  # TEXT[] comes back as Python list

            cur.execute(f"SELECT COUNT(*) FROM ami_listing_snapshots WHERE {where}",
                        params)
            total = cur.fetchone()[0]

    return {'total': total, 'limit': limit, 'offset': offset, 'snapshots': rows}


def get_latest_snapshot(asin: str) -> dict | None:
    """Get the most recent snapshot for a specific ASIN."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM ami_listing_snapshots
                   WHERE asin = %s
                   ORDER BY snapshot_date DESC LIMIT 1""",
                (asin,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            result = dict(zip(cols, row))
            for k, v in result.items():
                if hasattr(v, 'isoformat'):
                    result[k] = v.isoformat()
            return result
