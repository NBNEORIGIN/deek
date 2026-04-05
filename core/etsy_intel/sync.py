"""
Sync service — fetches data from Etsy API, upserts to database,
calculates 30-day metrics, runs health scoring, stores snapshots.
"""
import os
import logging
from datetime import date, datetime, timedelta, timezone

from core.etsy_intel.api_client import EtsyClient
from core.etsy_intel.db import (
    get_conn, upsert_shop, upsert_listings, upsert_sales, upsert_snapshots,
)
from core.etsy_intel.scoring import calculate_health_score

log = logging.getLogger(__name__)


def _get_shop_identifiers() -> list[str]:
    """Get configured shop IDs or names from env or database.

    Etsy API v3 accepts both numeric shop IDs and shop name strings
    in URL paths. We store numeric IDs in the database but accept
    either form in ETSY_SHOP_IDS for convenience.
    """
    env_ids = os.getenv('ETSY_SHOP_IDS', '')
    if env_ids:
        return [x.strip() for x in env_ids.split(',') if x.strip()]

    # Fall back to shops already in database
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT shop_id FROM etsy_shops ORDER BY shop_id")
            return [str(row[0]) for row in cur.fetchall()]


async def sync_all() -> dict:
    """
    Full sync: shops, listings, receipts, scoring, snapshots.
    Returns a summary of what was synced.
    """
    client = EtsyClient()
    try:
        shop_identifiers = _get_shop_identifiers()
        if not shop_identifiers:
            return {'error': 'No shop IDs configured. Set ETSY_SHOP_IDS env var.'}

        result = {
            'sync_date': date.today().isoformat(),
            'shops': [],
        }

        for shop_ident in shop_identifiers:
            shop_result = await _sync_shop(client, shop_ident)
            result['shops'].append(shop_result)

        # Totals
        result['total_listings'] = sum(s.get('listings_synced', 0) for s in result['shops'])
        result['total_sales'] = sum(s.get('sales_synced', 0) for s in result['shops'])
        result['total_snapshots'] = sum(s.get('snapshots_created', 0) for s in result['shops'])

        return result
    finally:
        await client.close()


async def _sync_shop(client: EtsyClient, shop_identifier: str) -> dict:
    """Sync a single shop: info, listings, sales, scoring, snapshots.

    shop_identifier can be a numeric shop_id or a shop name string.
    The Etsy API v3 accepts both in URL paths.
    """
    today = date.today()
    shop_result = {'shop_identifier': shop_identifier}

    # 1. Sync shop info — resolves name to numeric shop_id
    try:
        shop_data = await client.resolve_shop(shop_identifier)
        shop_id = shop_data['shop_id']
        shop_result['shop_id'] = shop_id
        upsert_shop({
            'shop_id': shop_id,
            'shop_name': shop_data.get('shop_name', ''),
            'url': shop_data.get('url', ''),
            'review_count': shop_data.get('review_count', 0),
            'review_average': shop_data.get('review_average', 0),
            'total_sales': shop_data.get('transaction_sold_count', 0),
            'active_listings': shop_data.get('listing_active_count', 0),
        })
        shop_result['shop_name'] = shop_data.get('shop_name', '')
        log.info('Synced shop info: %s (ID: %d)', shop_data.get('shop_name'), shop_id)
    except Exception as e:
        log.error('Failed to sync shop %s info: %s', shop_identifier, e)
        shop_result['shop_error'] = str(e)
        return shop_result

    # 2. Sync active listings
    try:
        raw_listings = await client.get_active_listings(shop_id)
        listings = []
        for raw in raw_listings:
            listing = _parse_listing(raw, shop_id)
            listings.append(listing)

        count = upsert_listings(listings)
        shop_result['listings_synced'] = count
        log.info('Synced %d listings for shop %d', count, shop_id)
    except Exception as e:
        log.error('Failed to sync listings for shop %d: %s', shop_id, e)
        shop_result['listings_error'] = str(e)
        shop_result['listings_synced'] = 0

    # 3. Sync recent receipts (last 30 days)
    try:
        thirty_days_ago = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
        raw_receipts = await client.get_receipts(shop_id, min_created=thirty_days_ago)
        sales = _parse_receipts(raw_receipts, shop_id)
        count = upsert_sales(sales)
        shop_result['sales_synced'] = count
        log.info('Synced %d sales for shop %d', count, shop_id)
    except Exception as e:
        log.warning('Failed to sync sales for shop %d: %s', shop_id, e)
        shop_result['sales_synced'] = 0

    # 4. Calculate 30-day metrics and run health scoring
    try:
        snapshots = _build_listing_snapshots(shop_id, today)
        count = upsert_snapshots(snapshots)
        shop_result['snapshots_created'] = count
        log.info('Created %d snapshots for shop %d', count, shop_id)

        # Update listing health scores in the main listings table
        _update_listing_scores(snapshots)
    except Exception as e:
        log.error('Failed to build snapshots for shop %d: %s', shop_id, e)
        shop_result['snapshots_created'] = 0

    return shop_result


def _parse_listing(raw: dict, shop_id: int) -> dict:
    """Parse an Etsy API listing response into our schema."""
    # Extract image count from includes or separate field
    images = raw.get('images', [])
    num_images = len(images) if isinstance(images, list) else raw.get('num_images', 0)

    # Price: Etsy returns amount as {"amount": 1299, "divisor": 100, "currency_code": "GBP"}
    price_obj = raw.get('price', {})
    if isinstance(price_obj, dict):
        amount = price_obj.get('amount', 0)
        divisor = price_obj.get('divisor', 100)
        price = amount / divisor if divisor else 0
        currency = price_obj.get('currency_code', 'GBP')
    else:
        price = float(price_obj) if price_obj else 0
        currency = 'GBP'

    # Timestamps: Etsy uses epoch seconds
    created_ts = raw.get('created_timestamp')
    updated_ts = raw.get('last_modified_timestamp') or raw.get('updated_timestamp')

    return {
        'listing_id': raw['listing_id'],
        'shop_id': shop_id,
        'title': raw.get('title', ''),
        'description': raw.get('description', ''),
        'price': price,
        'currency': currency,
        'quantity': raw.get('quantity', 0),
        'tags': raw.get('tags', []),
        'materials': raw.get('materials', []),
        'views': raw.get('views', 0),
        'favourites': raw.get('num_favorers', 0),
        'num_images': num_images,
        'state': raw.get('state', 'active'),
        'url': raw.get('url', ''),
        'created_at': datetime.fromtimestamp(created_ts, tz=timezone.utc) if created_ts else None,
        'updated_at': datetime.fromtimestamp(updated_ts, tz=timezone.utc) if updated_ts else None,
        'sku': (raw.get('skus') or [None])[0],  # first SKU if available
    }


def _parse_receipts(raw_receipts: list[dict], shop_id: int) -> list[dict]:
    """Parse Etsy receipt responses into our sales schema."""
    sales = []
    for r in raw_receipts:
        # Each receipt can have multiple transactions (line items)
        transactions = r.get('transactions', [])
        for txn in transactions:
            price_obj = txn.get('price', {})
            if isinstance(price_obj, dict):
                amount = price_obj.get('amount', 0)
                divisor = price_obj.get('divisor', 100)
                txn_price = amount / divisor if divisor else 0
            else:
                txn_price = float(price_obj) if price_obj else 0

            # Shipping from receipt level
            ship_obj = r.get('total_shipping_cost', {})
            if isinstance(ship_obj, dict):
                shipping = ship_obj.get('amount', 0) / ship_obj.get('divisor', 100)
            else:
                shipping = 0

            # Discount
            disc_obj = r.get('discount_amt', {})
            if isinstance(disc_obj, dict):
                discount = disc_obj.get('amount', 0) / disc_obj.get('divisor', 100)
            else:
                discount = float(disc_obj) if disc_obj else 0

            # Total from receipt
            total_obj = r.get('grandtotal', {})
            if isinstance(total_obj, dict):
                total = total_obj.get('amount', 0) / total_obj.get('divisor', 100)
            else:
                total = float(total_obj) if total_obj else 0

            create_ts = r.get('create_timestamp')
            sales.append({
                'receipt_id': r['receipt_id'],
                'shop_id': shop_id,
                'listing_id': txn.get('listing_id'),
                'buyer_email': r.get('buyer_email'),
                'sale_date': datetime.fromtimestamp(create_ts, tz=timezone.utc) if create_ts else None,
                'quantity': txn.get('quantity', 1),
                'price': txn_price,
                'shipping': shipping,
                'discount': discount,
                'total': total,
                'status': r.get('status', 'unknown'),
            })

    return sales


def _build_listing_snapshots(shop_id: int, snapshot_date: date) -> list[dict]:
    """
    Build snapshots for all listings in a shop.
    Calculates 30-day metrics from the sales table and listing stats.
    """
    # Get all listings for this shop
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT listing_id, title, description, price, tags,
                       views, favourites, num_images, state
                FROM etsy_listings
                WHERE shop_id = %s AND state = 'active'
            """, (shop_id,))
            cols = [d[0] for d in cur.description]
            listings = [dict(zip(cols, row)) for row in cur.fetchall()]

    if not listings:
        return []

    # Get 30-day sales per listing
    sales_30d = _get_sales_30d(shop_id)

    snapshots = []
    for listing in listings:
        lid = listing['listing_id']
        s30 = sales_30d.get(lid, {'quantity': 0, 'revenue': 0})

        # Build scoring input
        scoring_input = {
            'title': listing.get('title'),
            'description': listing.get('description'),
            'tags': listing.get('tags'),
            'num_images': listing.get('num_images', 0),
            'price': listing.get('price'),
            'views_30d': listing.get('views'),  # Etsy views is all-time; best we have
            'favourites_30d': listing.get('favourites'),
            'sales_30d': s30['quantity'],
        }

        # Conversion rate: sales / views (if views > 0)
        views = listing.get('views') or 0
        conv_rate = None
        if views > 0 and s30['quantity'] > 0:
            conv_rate = round(s30['quantity'] / views, 4)
        scoring_input['conversion_rate'] = conv_rate

        score, issues, recs = calculate_health_score(scoring_input)

        snapshots.append({
            'listing_id': lid,
            'snapshot_date': snapshot_date,
            'views_30d': listing.get('views'),
            'favourites_30d': listing.get('favourites'),
            'sales_30d': s30['quantity'],
            'revenue_30d': s30['revenue'],
            'conversion_rate': conv_rate,
            'health_score': score,
            'issues': issues,
            'recommendations': recs,
        })

    return snapshots


def _get_sales_30d(shop_id: int) -> dict:
    """Get 30-day sales grouped by listing_id."""
    cutoff = date.today() - timedelta(days=30)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT listing_id,
                       COALESCE(SUM(quantity), 0) as total_qty,
                       COALESCE(SUM(total), 0) as total_revenue
                FROM etsy_sales
                WHERE shop_id = %s AND sale_date >= %s
                GROUP BY listing_id
            """, (shop_id, cutoff))
            result = {}
            for row in cur.fetchall():
                result[row[0]] = {
                    'quantity': row[1],
                    'revenue': float(row[2]) if row[2] else 0,
                }
            return result


def _update_listing_scores(snapshots: list[dict]) -> None:
    """Update health_score, issues, recommendations on the listings table."""
    if not snapshots:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            for snap in snapshots:
                cur.execute("""
                    UPDATE etsy_listings
                    SET health_score = %s, issues = %s, recommendations = %s
                    WHERE listing_id = %s
                """, (
                    snap['health_score'], snap.get('issues'),
                    snap.get('recommendations'), snap['listing_id'],
                ))
            conn.commit()
