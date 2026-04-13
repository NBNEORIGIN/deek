"""
Catalog Items API enrichment — per-ASIN listing content.

Endpoint: GET /catalog/2022-04-01/items/{asin}
Docs: https://developer-docs.amazon.com/sp-api/docs/catalog-items-api-v2022-04-01-reference

Pulls full listing content: title, bullets, description, images, variation
relationships, A+ presence, browse nodes, brand. Stores in ami_listing_content.

Rate limit: 5 requests/second burst, restore rate 5/s (Catalog Items).
We use 200ms inter-request delay to stay comfortably under.

This module is read-only — no listing modifications.
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from .client import Region, REGION_MARKETPLACE, REGION_MARKETPLACE_CODE, spapi_get, RateLimitError
from core.amazon_intel.db import get_conn

logger = logging.getLogger(__name__)

CATALOG_API_VERSION = '2022-04-01'
INCLUDED_DATA = [
    'attributes',
    'images',
    'productTypes',
    'relationships',
    'summaries',
    'classifications',
]
REQUEST_DELAY = 0.25  # seconds between API calls


def _content_hash(data: dict) -> str:
    """SHA-256 of the content-relevant fields for change detection."""
    relevant = {
        'title': data.get('title'),
        'bullets': [data.get(f'bullet{i}') for i in range(1, 6)],
        'description': data.get('description'),
        'image_urls': data.get('image_urls'),
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def fetch_catalog_item(asin: str, region: Region = 'EU') -> dict:
    """
    Fetch a single ASIN from the Catalog Items API.
    Returns the raw SP-API response dict.
    """
    marketplace_id = REGION_MARKETPLACE[region]
    path = f'/catalog/2022-04-01/items/{asin}'
    params = {
        'marketplaceIds': marketplace_id,
        'includedData': ','.join(INCLUDED_DATA),
    }
    return spapi_get(region, path, params)


def parse_catalog_item(raw: dict, region: Region) -> dict:
    """
    Parse Catalog Items API response into ami_listing_content row shape.

    The API returns nested structures — we flatten to our schema.
    """
    marketplace = REGION_MARKETPLACE_CODE[region]
    marketplace_id = REGION_MARKETPLACE[region]
    asin = raw.get('asin', '')

    # Summaries — title, brand, browse node (marketplace-specific)
    summaries = raw.get('summaries', [])
    summary = _find_for_marketplace(summaries, marketplace_id) or (summaries[0] if summaries else {})
    title = summary.get('itemName', '')
    brand = summary.get('brand', '')
    browse_nodes = summary.get('browseClassification', {})

    # Attributes — bullets, description, keywords
    attributes = raw.get('attributes', {})
    bullets = _extract_bullets(attributes)
    description = _extract_description(attributes)

    # Images
    images_data = raw.get('images', [])
    image_set = _find_for_marketplace(images_data, marketplace_id) or (images_data[0] if images_data else {})
    image_list = image_set.get('images', [])
    main_image = next((img.get('link', '') for img in image_list if img.get('variant', '') == 'MAIN'), '')
    all_image_urls = [img.get('link', '') for img in image_list if img.get('link')]

    # Product type
    product_types = raw.get('productTypes', [])
    product_type = product_types[0].get('productType', '') if product_types else ''

    # Relationships — variations
    relationships = raw.get('relationships', [])
    rel_set = _find_for_marketplace(relationships, marketplace_id) or (relationships[0] if relationships else {})
    variation_info = _extract_variations(rel_set)

    # Classifications
    classifications = raw.get('classifications', [])
    if not isinstance(classifications, list):
        classifications = []
    classification_set = _find_for_marketplace(classifications, marketplace_id)
    if not classification_set and classifications:
        classification_set = classifications[0] if isinstance(classifications[0], dict) else {}
    item_class = classification_set.get('classificationId', '') if isinstance(classification_set, dict) else ''
    browse_node_list = []
    if browse_nodes:
        browse_node_list.append({
            'id': browse_nodes.get('classificationId', ''),
            'name': browse_nodes.get('displayName', ''),
        })

    # A+ detection (presence of A+ module references in attributes)
    aplus_present = bool(attributes.get('a_plus_content', []) or attributes.get('aplusContent', []))

    # Pricing from summaries
    list_price = summary.get('listPrice', {})

    row = {
        'asin': asin,
        'marketplace': marketplace,
        'region': region,
        'title': title,
        'bullet1': bullets[0] if len(bullets) > 0 else None,
        'bullet2': bullets[1] if len(bullets) > 1 else None,
        'bullet3': bullets[2] if len(bullets) > 2 else None,
        'bullet4': bullets[3] if len(bullets) > 3 else None,
        'bullet5': bullets[4] if len(bullets) > 4 else None,
        'description': description,
        'main_image_url': main_image,
        'image_urls': all_image_urls,
        'image_count': len(all_image_urls),
        'aplus_present': aplus_present,
        'aplus_modules': attributes.get('a_plus_content') or attributes.get('aplusContent'),
        'brand': brand,
        'brand_registered': bool(summary.get('brandRegistered')),
        'parent_asin': variation_info.get('parent_asin'),
        'variation_type': variation_info.get('variation_type'),
        'variation_theme': variation_info.get('variation_theme'),
        'child_asins': variation_info.get('child_asins', []),
        'product_type': product_type,
        'browse_nodes': browse_node_list,
        'item_classification': item_class,
        'list_price_amount': float(list_price.get('amount', 0)) if list_price.get('amount') else None,
        'list_price_currency': list_price.get('currency'),
        'catalog_json': raw,
    }
    row['content_hash'] = _content_hash(row)
    return row


def upsert_listing_content(row: dict) -> dict:
    """
    Upsert a parsed catalog item into ami_listing_content.
    Returns {'action': 'inserted'|'updated'|'unchanged', 'asin': ...}
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check existing
            cur.execute(
                "SELECT content_hash FROM ami_listing_content WHERE asin = %s AND marketplace = %s",
                (row['asin'], row['marketplace']),
            )
            existing = cur.fetchone()

            if existing and existing[0] == row['content_hash']:
                return {'action': 'unchanged', 'asin': row['asin']}

            # Track changes for history if updating
            if existing:
                _record_changes(cur, row)

            cur.execute("""
                INSERT INTO ami_listing_content (
                    asin, marketplace, region, title,
                    bullet1, bullet2, bullet3, bullet4, bullet5,
                    description, main_image_url, image_urls, image_count,
                    aplus_present, aplus_modules, brand, brand_registered,
                    parent_asin, variation_type, variation_theme, child_asins,
                    product_type, browse_nodes, item_classification,
                    list_price_amount, list_price_currency,
                    catalog_json, content_hash, last_enriched_at
                ) VALUES (
                    %(asin)s, %(marketplace)s, %(region)s, %(title)s,
                    %(bullet1)s, %(bullet2)s, %(bullet3)s, %(bullet4)s, %(bullet5)s,
                    %(description)s, %(main_image_url)s, %(image_urls)s, %(image_count)s,
                    %(aplus_present)s, %(aplus_modules)s, %(brand)s, %(brand_registered)s,
                    %(parent_asin)s, %(variation_type)s, %(variation_theme)s, %(child_asins)s,
                    %(product_type)s, %(browse_nodes)s, %(item_classification)s,
                    %(list_price_amount)s, %(list_price_currency)s,
                    %(catalog_json)s, %(content_hash)s, NOW()
                )
                ON CONFLICT (asin, marketplace) DO UPDATE SET
                    title = EXCLUDED.title,
                    bullet1 = EXCLUDED.bullet1, bullet2 = EXCLUDED.bullet2,
                    bullet3 = EXCLUDED.bullet3, bullet4 = EXCLUDED.bullet4,
                    bullet5 = EXCLUDED.bullet5,
                    description = EXCLUDED.description,
                    main_image_url = EXCLUDED.main_image_url,
                    image_urls = EXCLUDED.image_urls, image_count = EXCLUDED.image_count,
                    aplus_present = EXCLUDED.aplus_present,
                    aplus_modules = EXCLUDED.aplus_modules,
                    brand = EXCLUDED.brand, brand_registered = EXCLUDED.brand_registered,
                    parent_asin = EXCLUDED.parent_asin,
                    variation_type = EXCLUDED.variation_type,
                    variation_theme = EXCLUDED.variation_theme,
                    child_asins = EXCLUDED.child_asins,
                    product_type = EXCLUDED.product_type,
                    browse_nodes = EXCLUDED.browse_nodes,
                    item_classification = EXCLUDED.item_classification,
                    list_price_amount = EXCLUDED.list_price_amount,
                    list_price_currency = EXCLUDED.list_price_currency,
                    catalog_json = EXCLUDED.catalog_json,
                    content_hash = EXCLUDED.content_hash,
                    last_enriched_at = NOW()
            """, {
                **row,
                'image_urls': json.dumps(row['image_urls']),
                'aplus_modules': json.dumps(row['aplus_modules']) if row['aplus_modules'] else None,
                'variation_theme': json.dumps(row['variation_theme']) if row['variation_theme'] else None,
                'child_asins': json.dumps(row['child_asins']),
                'browse_nodes': json.dumps(row['browse_nodes']),
                'catalog_json': json.dumps(row['catalog_json']),
            })
            conn.commit()

    action = 'updated' if existing else 'inserted'
    return {'action': action, 'asin': row['asin']}


def _record_changes(cur, new_row: dict):
    """Record field-level changes to ami_listing_content_history."""
    tracked_fields = ['title', 'bullet1', 'bullet2', 'bullet3', 'bullet4', 'bullet5',
                      'description', 'main_image_url', 'image_count', 'brand',
                      'parent_asin', 'product_type']
    cur.execute(
        """SELECT title, bullet1, bullet2, bullet3, bullet4, bullet5,
                  description, main_image_url, image_count, brand,
                  parent_asin, product_type
           FROM ami_listing_content WHERE asin = %s AND marketplace = %s""",
        (new_row['asin'], new_row['marketplace']),
    )
    old = cur.fetchone()
    if not old:
        return
    old_dict = dict(zip(tracked_fields, old))
    for field in tracked_fields:
        old_val = str(old_dict.get(field) or '')
        new_val = str(new_row.get(field) or '')
        if old_val != new_val:
            cur.execute(
                """INSERT INTO ami_listing_content_history
                   (asin, marketplace, field_name, old_value, new_value)
                   VALUES (%s, %s, %s, %s, %s)""",
                (new_row['asin'], new_row['marketplace'], field,
                 old_val[:5000] if old_val else None,
                 new_val[:5000] if new_val else None),
            )


def enrich_asins(asins: list[str], region: Region = 'EU',
                 batch_size: int = 50, skip_recent_hours: int = 24) -> dict:
    """
    Enrich a list of ASINs via Catalog Items API.
    Skips ASINs enriched within skip_recent_hours.
    Returns summary dict.
    """
    marketplace = REGION_MARKETPLACE_CODE[region]
    results = {'total': len(asins), 'enriched': 0, 'skipped': 0,
               'errors': 0, 'unchanged': 0, 'error_asins': []}

    # Filter out recently enriched
    asins_to_enrich = asins
    if skip_recent_hours > 0:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT asin FROM ami_listing_content
                       WHERE marketplace = %s
                         AND last_enriched_at > NOW() - INTERVAL '%s hours'""",
                    (marketplace, skip_recent_hours),
                )
                recent = {row[0] for row in cur.fetchall()}
        asins_to_enrich = [a for a in asins if a not in recent]
        results['skipped'] = len(asins) - len(asins_to_enrich)

    for i, asin in enumerate(asins_to_enrich):
        try:
            raw = fetch_catalog_item(asin, region)
            parsed = parse_catalog_item(raw, region)
            outcome = upsert_listing_content(parsed)

            if outcome['action'] == 'unchanged':
                results['unchanged'] += 1
            else:
                results['enriched'] += 1

            if (i + 1) % 50 == 0:
                logger.info("Catalog enrichment progress: %d/%d (region=%s)",
                            i + 1, len(asins_to_enrich), region)

        except RateLimitError:
            logger.warning("Rate limited at ASIN %s, backing off 5s", asin)
            time.sleep(5)
            results['errors'] += 1
            results['error_asins'].append(asin)
        except Exception as e:
            logger.error("Failed to enrich ASIN %s: %s", asin, str(e)[:200])
            results['errors'] += 1
            results['error_asins'].append(asin)

        time.sleep(REQUEST_DELAY)

    results['error_asins'] = results['error_asins'][:50]
    return results


def get_asins_for_enrichment(region: Region = 'EU', limit: int = 500) -> list[str]:
    """
    Get ASINs that need enrichment — from ami_sku_mapping, ordered by
    least-recently-enriched first.
    """
    marketplace = REGION_MARKETPLACE_CODE[region]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sm.asin, MIN(lc.last_enriched_at) AS last_enriched
                FROM ami_sku_mapping sm
                LEFT JOIN ami_listing_content lc
                    ON sm.asin = lc.asin AND lc.marketplace = %s
                WHERE sm.asin IS NOT NULL AND sm.asin != ''
                GROUP BY sm.asin
                ORDER BY last_enriched ASC NULLS FIRST
                LIMIT %s
            """, (marketplace, limit))
            return [row[0] for row in cur.fetchall()]


def run_enrichment(region: Region = 'EU', limit: int = 100,
                   skip_recent_hours: int = 24) -> dict:
    """
    Full enrichment run: get ASINs needing enrichment, fetch and store.
    Called post-inventory-sync in the scheduler.
    """
    asins = get_asins_for_enrichment(region, limit=limit)
    if not asins:
        return {'total': 0, 'message': 'no ASINs to enrich'}
    return enrich_asins(asins, region, skip_recent_hours=skip_recent_hours)


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _find_for_marketplace(items: list, marketplace_id: str) -> dict | None:
    """Find the marketplace-specific entry in a list of marketplace-keyed items."""
    if not items or not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        mids = item.get('marketplaceId', '') or item.get('marketplace_id', '')
        if mids == marketplace_id:
            return item
    # Fallback: return first dict entry if no marketplace match
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def _extract_bullets(attributes: dict) -> list[str]:
    """Extract bullet points from attributes."""
    bullets = []
    bp = attributes.get('bullet_point', [])
    if isinstance(bp, list):
        for item in bp:
            if isinstance(item, dict):
                val = item.get('value', '')
            else:
                val = str(item)
            if val:
                bullets.append(val)
    return bullets[:5]


def _extract_description(attributes: dict) -> str:
    """Extract product description from attributes."""
    desc = attributes.get('product_description', [])
    if isinstance(desc, list) and desc:
        item = desc[0]
        if isinstance(item, dict):
            return item.get('value', '')
        return str(item)
    if isinstance(desc, str):
        return desc
    return ''


def _extract_variations(rel_set: dict) -> dict:
    """Extract variation info from relationships."""
    if not rel_set or not isinstance(rel_set, dict):
        return {}

    relationships = rel_set.get('relationships', [])
    if not relationships:
        return {}

    result = {}
    for rel in relationships:
        rel_type = rel.get('type', '')
        if rel_type == 'VARIATION':
            child_asins = [c.get('asin', '') for c in rel.get('childAsins', []) if c.get('asin')]
            parent = rel.get('parentAsins', [{}])
            parent_asin = parent[0].get('asin', '') if parent else ''
            result = {
                'parent_asin': parent_asin or None,
                'variation_type': 'VARIATION',
                'variation_theme': rel.get('variationTheme'),
                'child_asins': child_asins,
            }
            break

    return result
