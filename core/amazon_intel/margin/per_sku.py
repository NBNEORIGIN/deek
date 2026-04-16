"""
Per-SKU margin engine.

Joins four sources into a single per-(asin, marketplace) margin breakdown:

  1. ami_orders                     — gross revenue, units, over lookback
  2. ami_fee_snapshots              — referral + FBA + other fees per unit
  3. Manufacture /api/costs/price/… — material + labour + overhead per unit
  4. ami_advertising_data           — ad spend allocated per ASIN (optional)

Revenue is converted to net using marketplace VAT rate (see ..vat).

Output (per SKU):
    net_revenue, units, gross_revenue, fees, cogs, ad_spend,
    gross_profit, gross_margin_pct, net_profit, net_margin_pct,
    confidence (HIGH | MEDIUM | LOW)

Confidence rules:
    HIGH    — fees present (Success status) AND cost source == 'override'
    MEDIUM  — fees present AND cost source == 'blank' non-composite
    LOW     — anything else (missing fees, fallback cost, composite blank)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Optional

from ..db import get_conn
from ..manufacture_client import get_costs_bulk
from ..vat import net_revenue
from .quartile_brief import MARKETPLACE_ALIASES

logger = logging.getLogger(__name__)


ZERO = Decimal('0')
TWO_PLACES = Decimal('0.01')


@dataclass
class SkuMargin:
    asin: str
    marketplace: str
    m_number: Optional[str]
    units: int
    gross_revenue: Decimal
    net_revenue: Decimal
    fees_per_unit: Optional[Decimal]
    fees_total: Optional[Decimal]
    cogs_per_unit: Optional[Decimal]
    cogs_total: Optional[Decimal]
    ad_spend: Decimal
    gross_profit: Optional[Decimal]            # net_revenue - fees - cogs
    gross_margin_pct: Optional[Decimal]        # gross_profit / net_revenue
    net_profit: Optional[Decimal]              # gross_profit - ad_spend
    net_margin_pct: Optional[Decimal]          # net_profit / net_revenue
    blank_raw: Optional[str]
    blank_normalized: Optional[str]
    fee_source: str                            # 'snapshot' | 'missing'
    cost_source: str                           # 'override' | 'blank' | 'fallback' | 'missing'
    is_composite: bool
    confidence: str                            # HIGH | MEDIUM | LOW


def _marketplace_aliases(marketplace: str) -> list[str]:
    return MARKETPLACE_ALIASES.get(marketplace.upper(), [marketplace.upper()])


def _fetch_orders_aggregate(
    marketplace: str,
    lookback_days: int,
) -> dict[tuple[str, str], dict]:
    """
    Aggregate orders by (asin, marketplace). Marketplace alias logic handles
    the UK↔GB mismatch between ami_orders.marketplace and the rest of the system.
    Returns dict keyed by (asin, canonical_marketplace).
    """
    aliases = _marketplace_aliases(marketplace)
    canonical = marketplace.upper()
    sql = """
        SELECT asin,
               COUNT(*)                          AS order_line_count,
               SUM(quantity)                     AS units,
               SUM(item_price_amount)            AS gross_revenue,
               MIN(m_number)                     AS m_number
          FROM ami_orders
         WHERE asin IS NOT NULL AND asin <> ''
           AND marketplace = ANY(%(mp)s)
           AND order_date >= CURRENT_DATE - make_interval(days => %(days)s)
         GROUP BY asin
    """
    out: dict[tuple[str, str], dict] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {'mp': aliases, 'days': lookback_days})
            for asin, _lines, units, gross, m_number in cur.fetchall():
                if not units or not gross:
                    continue
                out[(asin, canonical)] = {
                    'units': int(units),
                    'gross_revenue': Decimal(str(gross)),
                    'm_number': m_number,
                }
    return out


def _fetch_fee_snapshots(
    marketplace: str,
) -> dict[str, dict]:
    sql = """
        SELECT asin, total_fees, referral_fee, fba_fee, variable_closing_fee,
               other_fees, api_status, price_point_amount
          FROM ami_fee_snapshots
         WHERE marketplace = %s
    """
    out: dict[str, dict] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (marketplace.upper(),))
            for r in cur.fetchall():
                asin = r[0]
                out[asin] = {
                    'total_fees': r[1],
                    'referral_fee': r[2],
                    'fba_fee': r[3],
                    'variable_closing_fee': r[4],
                    'other_fees': r[5],
                    'api_status': r[6],
                    'price_point': r[7],
                }
    return out


def _fetch_ad_spend(
    marketplace: str,
    lookback_days: int,
) -> dict[str, Decimal]:
    """Ad spend per ASIN over the lookback window."""
    sql = """
        SELECT d.asin, SUM(d.spend) AS spend
          FROM ami_advertising_data d
          LEFT JOIN ami_advertising_profiles p ON p.profile_id = d.profile_id
         WHERE d.asin IS NOT NULL AND d.asin <> ''
           AND d.report_date IS NOT NULL
           AND d.report_date >= CURRENT_DATE - make_interval(days => %(days)s)
           AND p.country_code = %(mkt)s
         GROUP BY d.asin
    """
    out: dict[str, Decimal] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {'mkt': marketplace.upper(), 'days': lookback_days})
            for asin, spend in cur.fetchall():
                out[asin] = Decimal(str(spend or 0))
    return out


def _pct(numer: Decimal, denom: Decimal) -> Optional[Decimal]:
    if denom is None or denom == 0:
        return None
    return (Decimal('100') * numer / denom).quantize(TWO_PLACES)


def _confidence(fee_source: str, cost_source: str, is_composite: bool) -> str:
    if fee_source != 'snapshot':
        return 'LOW'
    if cost_source == 'override':
        return 'HIGH'
    if cost_source == 'blank' and not is_composite:
        return 'MEDIUM'
    return 'LOW'


async def compute_margins(
    marketplace: str,
    lookback_days: int = 30,
) -> list[SkuMargin]:
    """
    Compute per-SKU margins for a marketplace over the lookback window.
    Returns one SkuMargin per distinct ASIN with orders.
    """
    orders = _fetch_orders_aggregate(marketplace, lookback_days)
    if not orders:
        return []

    fees = _fetch_fee_snapshots(marketplace)
    ads = _fetch_ad_spend(marketplace, lookback_days)

    m_numbers = sorted({v['m_number'] for v in orders.values() if v.get('m_number')})
    costs = await get_costs_bulk(m_numbers) if m_numbers else {}

    results: list[SkuMargin] = []
    for (asin, mkt), agg in orders.items():
        units = agg['units']
        gross = agg['gross_revenue']
        net = net_revenue(gross, mkt)
        m_number = agg.get('m_number')

        fee_row = fees.get(asin)
        if fee_row and fee_row.get('total_fees') is not None and fee_row.get('api_status') == 'Success':
            fees_per_unit = Decimal(str(fee_row['total_fees']))
            fees_total = (fees_per_unit * units).quantize(TWO_PLACES)
            fee_source = 'snapshot'
        else:
            fees_per_unit = None
            fees_total = None
            fee_source = 'missing'

        cost_row = costs.get(m_number) if m_number else None
        if cost_row and cost_row.get('cost_gbp') is not None:
            cogs_per_unit = Decimal(str(cost_row['cost_gbp']))
            cogs_total = (cogs_per_unit * units).quantize(TWO_PLACES)
            cost_source = cost_row.get('source') or 'missing'
            is_composite = bool(cost_row.get('is_composite'))
            blank_raw = cost_row.get('blank_raw')
            blank_normalized = cost_row.get('blank_normalized')
        else:
            cogs_per_unit = None
            cogs_total = None
            cost_source = 'missing'
            is_composite = False
            blank_raw = None
            blank_normalized = None

        ad_spend = ads.get(asin, ZERO)

        if fees_total is not None and cogs_total is not None:
            gross_profit = (net - fees_total - cogs_total).quantize(TWO_PLACES)
            net_profit = (gross_profit - ad_spend).quantize(TWO_PLACES)
            gross_margin_pct = _pct(gross_profit, net)
            net_margin_pct = _pct(net_profit, net)
        else:
            gross_profit = None
            net_profit = None
            gross_margin_pct = None
            net_margin_pct = None

        results.append(SkuMargin(
            asin=asin,
            marketplace=mkt,
            m_number=m_number,
            units=units,
            gross_revenue=gross.quantize(TWO_PLACES),
            net_revenue=net,
            fees_per_unit=fees_per_unit,
            fees_total=fees_total,
            cogs_per_unit=cogs_per_unit,
            cogs_total=cogs_total,
            ad_spend=ad_spend.quantize(TWO_PLACES),
            gross_profit=gross_profit,
            gross_margin_pct=gross_margin_pct,
            net_profit=net_profit,
            net_margin_pct=net_margin_pct,
            blank_raw=blank_raw,
            blank_normalized=blank_normalized,
            fee_source=fee_source,
            cost_source=cost_source,
            is_composite=is_composite,
            confidence=_confidence(fee_source, cost_source, is_composite),
        ))
    return results


def margin_to_dict(m: SkuMargin) -> dict:
    """JSON-safe dict — decimals → floats."""
    d = asdict(m)
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            d[k] = float(v)
    return d


def bucket_margins(margins: list[SkuMargin]) -> dict:
    """
    Summary buckets for the top-line margin panel. Uses net_margin_pct
    quartiles, but drops SKUs without a computed margin.
    """
    scored = [m for m in margins if m.net_margin_pct is not None]
    if not scored:
        return {
            'total_skus': len(margins),
            'scored_skus': 0,
            'buckets': {'healthy': 0, 'thin': 0, 'unprofitable': 0, 'unknown': len(margins)},
            'total_net_revenue': 0.0,
            'total_net_profit': 0.0,
        }
    healthy = thin = unprofitable = 0
    for m in scored:
        pct = float(m.net_margin_pct or 0)
        if pct >= 20:
            healthy += 1
        elif pct >= 5:
            thin += 1
        else:
            unprofitable += 1
    total_net_rev = sum((float(m.net_revenue) for m in scored), 0.0)
    total_net_profit = sum((float(m.net_profit or 0) for m in scored), 0.0)
    return {
        'total_skus': len(margins),
        'scored_skus': len(scored),
        'buckets': {
            'healthy': healthy,
            'thin': thin,
            'unprofitable': unprofitable,
            'unknown': len(margins) - len(scored),
        },
        'total_net_revenue': round(total_net_rev, 2),
        'total_net_profit': round(total_net_profit, 2),
    }
