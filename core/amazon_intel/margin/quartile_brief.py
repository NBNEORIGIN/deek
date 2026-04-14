"""
Quartile ACOS brief (Phase 0 — account-level assumptions).

Generates a per-SKU recommendation brief for Quartile (external ads agency).
Joins ami_advertising_data (spend + ad-attributed sales) with ami_orders
(total revenue + units) to derive organic rate, current ACOS/TACOS, and
a recommended ACOS target.

Phase 0 uses a single account-level non-ad-cost assumption (default: 82%
of price covers blended COGS + Amazon fees + target margin, leaving 18%
max TACOS headroom). Phase 3 replaces this with per-SKU true-margin data.

See spec §5 and revised spec v2 §5 for the math and the known caveats.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional


# ── Defaults (account-level, configurable per call) ────────────────────────────
# If these change, mirror them in the brief header so the Quartile rep sees
# the basis alongside the recommendations.
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_TARGET_MARGIN_PCT = 0.06      # 6% net margin on the Amazon account (GBP)
DEFAULT_NON_AD_COST_PCT = 0.82        # blended COGS + Amazon fees + target margin
# max TACOS = 1 − non_ad_cost_pct, computed at runtime

# Classification thresholds
REDUCE_RATIO = 1.2        # current_acos > recommended * 1.2 → REDUCE
INCREASE_RATIO = 0.5      # current_acos < recommended * 0.5 → INCREASE (if volume ok)
PAUSE_ACOS = 1.0          # current_acos > 1.0 → spend exceeds ad revenue → PAUSE
MIN_SPEND_FOR_RECOMMENDATION = 1.0   # < £1 / $1 spend: exclude, nothing to say
MIN_UNITS_FOR_INCREASE = 20          # need >20 units in window to suggest scaling
LOW_VOLUME_FLAG_UNITS = 10           # units < 10: flag recommendation as low-confidence
ORGANIC_DEPENDENCY_THRESHOLD = 0.7   # organic_rate > 0.7 → flag as organic-dependent


Action = str  # "REDUCE" | "INCREASE" | "PAUSE" | "HOLD"


@dataclass
class SkuAdAggregate:
    """One row from aggregating ami_advertising_data for a SKU within a profile."""
    asin: str
    sku: Optional[str]
    profile_id: str
    country_code: str
    account_name: str
    spend: float
    ad_sales: float
    ad_orders: int
    impressions: int
    clicks: int


@dataclass
class SkuOrdersAggregate:
    """One row from aggregating ami_orders for an (asin, marketplace)."""
    asin: str
    marketplace: str
    units: int
    revenue: float


@dataclass
class Recommendation:
    asin: str
    sku: Optional[str]
    account_name: str
    country_code: str
    action: Action
    reason: str
    caveats: list[str] = field(default_factory=list)
    # Current-state snapshot
    spend: float = 0.0
    ad_sales: float = 0.0
    total_revenue: float = 0.0
    units: int = 0
    current_acos: Optional[float] = None
    current_tacos: Optional[float] = None
    organic_rate: Optional[float] = None
    # Target
    recommended_acos: Optional[float] = None


# ── Pure classification (no DB) ────────────────────────────────────────────────


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    try:
        if denominator == 0:
            return None
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def classify_sku(
    ad: SkuAdAggregate,
    orders: Optional[SkuOrdersAggregate],
    *,
    target_margin_pct: float = DEFAULT_TARGET_MARGIN_PCT,
    non_ad_cost_pct: float = DEFAULT_NON_AD_COST_PCT,
) -> Optional[Recommendation]:
    """Apply the v0 Quartile brief classification to one SKU.

    Returns None if the SKU has no meaningful ad activity to optimise
    (spend below MIN_SPEND_FOR_RECOMMENDATION).

    All floats in/out. DB → dataclass conversion happens in the caller.
    """
    spend = float(ad.spend or 0)
    ad_sales = float(ad.ad_sales or 0)
    total_revenue = float(orders.revenue) if orders else 0.0
    units = int(orders.units) if orders else 0

    if spend < MIN_SPEND_FOR_RECOMMENDATION:
        return None

    max_tacos = max(0.0, 1.0 - non_ad_cost_pct)

    current_acos = _safe_div(spend, ad_sales)
    current_tacos = _safe_div(spend, total_revenue)
    organic_rate: Optional[float] = None
    if total_revenue > 0:
        # Organic share = revenue not attributed to ads. Clamp negative values
        # (attribution-window artefact) to 0, flag as caveat.
        organic_abs = total_revenue - ad_sales
        organic_rate = max(0.0, min(1.0, organic_abs / total_revenue))

    caveats: list[str] = []

    # Compute recommended ACOS when we have the ingredients.
    recommended_acos: Optional[float] = None
    if organic_rate is not None and organic_rate < 1.0:
        recommended_acos = max_tacos / (1.0 - organic_rate)
    elif organic_rate is None:
        # No total-revenue data — can't compute TACOS. Fall back to max_tacos
        # as a conservative ACOS target, flag the caveat.
        recommended_acos = max_tacos
        caveats.append("no-orders-data — recommendation uses max_tacos directly, not a derived ACOS")

    if orders is None or total_revenue == 0:
        caveats.append("no-orders-data")
    if units and units < LOW_VOLUME_FLAG_UNITS:
        caveats.append(f"low-volume ({units} units in window)")
    if organic_rate is not None and organic_rate > ORGANIC_DEPENDENCY_THRESHOLD:
        caveats.append(f"organic-rate-dependent ({organic_rate:.0%} organic) — cutting ads may erode ranking")
    if ad_sales > total_revenue > 0:
        caveats.append("ad-attributed sales exceed total revenue — attribution-window artefact")

    # Classify.
    action: Action
    reason: str

    if ad_sales <= 0:
        action = "PAUSE"
        reason = f"Zero ad-attributed sales on £{spend:.2f} spend"
    elif current_acos is not None and current_acos >= PAUSE_ACOS:
        # ACOS >= 100% means ad spend at or above ad-attributed revenue.
        # Even at break-even on ads, COGS and fees still have to be paid out of that,
        # so it's a guaranteed loss on advertised units.
        action = "PAUSE"
        reason = f"ACOS {current_acos:.0%} — ad spend at or above ad-attributed revenue"
    elif recommended_acos is None or current_acos is None:
        action = "HOLD"
        reason = "Insufficient data to compute recommended ACOS"
    elif current_acos > recommended_acos * REDUCE_RATIO:
        action = "REDUCE"
        reason = (
            f"Current ACOS {current_acos:.0%} vs recommended {recommended_acos:.0%} — "
            f"reducing protects margin"
        )
    elif (
        current_acos < recommended_acos * INCREASE_RATIO
        and units >= MIN_UNITS_FOR_INCREASE
    ):
        action = "INCREASE"
        reason = (
            f"Current ACOS {current_acos:.0%} well below recommended "
            f"{recommended_acos:.0%} — margin supports more spend"
        )
    else:
        action = "HOLD"
        reason = f"Current ACOS {current_acos:.0%} within band of recommended {recommended_acos:.0%}"

    return Recommendation(
        asin=ad.asin,
        sku=ad.sku,
        account_name=ad.account_name,
        country_code=ad.country_code,
        action=action,
        reason=reason,
        caveats=caveats,
        spend=round(spend, 2),
        ad_sales=round(ad_sales, 2),
        total_revenue=round(total_revenue, 2),
        units=units,
        current_acos=round(current_acos, 4) if current_acos is not None else None,
        current_tacos=round(current_tacos, 4) if current_tacos is not None else None,
        organic_rate=round(organic_rate, 4) if organic_rate is not None else None,
        recommended_acos=round(recommended_acos, 4) if recommended_acos is not None else None,
    )


def classify_all(
    ad_rows: Iterable[SkuAdAggregate],
    orders_rows: Iterable[SkuOrdersAggregate],
    *,
    target_margin_pct: float = DEFAULT_TARGET_MARGIN_PCT,
    non_ad_cost_pct: float = DEFAULT_NON_AD_COST_PCT,
) -> list[Recommendation]:
    """Run classify_sku over every ad aggregate, joining by (asin, marketplace=country_code)."""
    orders_by_key: dict[tuple[str, str], SkuOrdersAggregate] = {
        (o.asin, o.marketplace): o for o in orders_rows
    }
    out: list[Recommendation] = []
    for ad in ad_rows:
        key = (ad.asin, ad.country_code)
        rec = classify_sku(
            ad,
            orders_by_key.get(key),
            target_margin_pct=target_margin_pct,
            non_ad_cost_pct=non_ad_cost_pct,
        )
        if rec is not None:
            out.append(rec)
    return out


# ── DB query wrappers ─────────────────────────────────────────────────────────


def fetch_ad_aggregates(
    marketplace: Optional[str] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[SkuAdAggregate]:
    """Aggregate ami_advertising_data over the lookback window, joined to
    ami_advertising_profiles so we carry account_name + country_code."""
    from core.amazon_intel.db import get_conn

    sql = """
        SELECT d.asin,
               MAX(d.sku) AS sku,
               COALESCE(d.profile_id, '') AS profile_id,
               COALESCE(p.country_code, '?') AS country_code,
               COALESCE(p.account_name, '?') AS account_name,
               SUM(d.spend) AS spend,
               SUM(d.sales_7d) AS ad_sales,
               SUM(d.orders_7d) AS ad_orders,
               SUM(d.impressions) AS impressions,
               SUM(d.clicks) AS clicks
          FROM ami_advertising_data d
          LEFT JOIN ami_advertising_profiles p ON p.profile_id = d.profile_id
         WHERE d.asin IS NOT NULL
           AND d.created_at >= NOW() - make_interval(days => %(days)s)
    """
    params: dict[str, Any] = {"days": lookback_days}
    if marketplace:
        sql += " AND p.country_code = %(mkt)s"
        params["mkt"] = marketplace
    sql += """
         GROUP BY d.asin, d.profile_id, p.country_code, p.account_name
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        SkuAdAggregate(
            asin=r[0],
            sku=r[1],
            profile_id=r[2] or "",
            country_code=r[3] or "?",
            account_name=r[4] or "?",
            spend=float(r[5] or 0),
            ad_sales=float(r[6] or 0),
            ad_orders=int(r[7] or 0),
            impressions=int(r[8] or 0),
            clicks=int(r[9] or 0),
        )
        for r in rows
    ]


def fetch_orders_aggregates(
    marketplace: Optional[str] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[SkuOrdersAggregate]:
    """Aggregate ami_orders over the lookback window."""
    from core.amazon_intel.db import get_conn

    sql = """
        SELECT asin, marketplace,
               SUM(quantity) AS units,
               SUM(COALESCE(item_price_amount, 0) * quantity) AS revenue
          FROM ami_orders
         WHERE asin IS NOT NULL
           AND order_date >= CURRENT_DATE - make_interval(days => %(days)s)
    """
    params: dict[str, Any] = {"days": lookback_days}
    if marketplace:
        sql += " AND marketplace = %(mkt)s"
        params["mkt"] = marketplace
    sql += """
         GROUP BY asin, marketplace
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        SkuOrdersAggregate(
            asin=r[0],
            marketplace=r[1],
            units=int(r[2] or 0),
            revenue=float(r[3] or 0),
        )
        for r in rows
    ]


def generate_brief(
    marketplace: Optional[str] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    target_margin_pct: float = DEFAULT_TARGET_MARGIN_PCT,
    non_ad_cost_pct: float = DEFAULT_NON_AD_COST_PCT,
) -> dict:
    """End-to-end: query DB, classify, return structured brief."""
    ad_rows = fetch_ad_aggregates(marketplace=marketplace, lookback_days=lookback_days)
    order_rows = fetch_orders_aggregates(marketplace=marketplace, lookback_days=lookback_days)

    recs = classify_all(
        ad_rows, order_rows,
        target_margin_pct=target_margin_pct,
        non_ad_cost_pct=non_ad_cost_pct,
    )

    # Sort: PAUSE first (most urgent), then REDUCE by magnitude, then INCREASE, then HOLD.
    action_priority = {"PAUSE": 0, "REDUCE": 1, "INCREASE": 2, "HOLD": 3}

    def sort_key(r: Recommendation) -> tuple:
        excess = 0.0
        if r.current_acos is not None and r.recommended_acos:
            excess = r.current_acos - r.recommended_acos
        # PAUSE/REDUCE: bigger excess first. INCREASE: bigger spend first. HOLD: alphabetical.
        return (action_priority.get(r.action, 99), -abs(excess), -r.spend)

    recs.sort(key=sort_key)

    counts = {"PAUSE": 0, "REDUCE": 0, "INCREASE": 0, "HOLD": 0}
    for r in recs:
        counts[r.action] = counts.get(r.action, 0) + 1

    return {
        "marketplace": marketplace or "ALL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "basis": {
            "lookback_days": lookback_days,
            "target_margin_pct": target_margin_pct,
            "non_ad_cost_pct": non_ad_cost_pct,
            "max_tacos": round(max(0.0, 1.0 - non_ad_cost_pct), 4),
        },
        "summary": {
            "total_skus_with_spend": len(recs),
            "counts": counts,
        },
        "recommendations": [asdict(r) for r in recs],
    }


# ── Text rendering (for copy-paste email to Quartile rep) ──────────────────────


def render_brief_text(brief: dict) -> str:
    """Human-readable text version of generate_brief() output.

    The Quartile rep can't ingest JSON — this is the form that goes in email.
    """
    lines: list[str] = []
    mkt = brief.get("marketplace", "ALL")
    basis = brief.get("basis", {})
    counts = brief.get("summary", {}).get("counts", {})
    total = brief.get("summary", {}).get("total_skus_with_spend", 0)

    lines.append(f"Subject: ACOS target adjustments — {mkt} — {datetime.now(timezone.utc).date().isoformat()}")
    lines.append("")
    lines.append(
        f"Summary: {total} SKUs reviewed. "
        f"{counts.get('PAUSE', 0)} pauses, "
        f"{counts.get('REDUCE', 0)} reductions, "
        f"{counts.get('INCREASE', 0)} increases, "
        f"{counts.get('HOLD', 0)} hold."
    )
    lines.append(
        f"Basis: {basis.get('lookback_days', '?')}-day window, "
        f"account-level {basis.get('target_margin_pct', 0) * 100:.1f}% target margin, "
        f"max TACOS {basis.get('max_tacos', 0) * 100:.1f}%. "
        f"Per-SKU margin refinement available once Phase 3 lands."
    )
    lines.append("")

    def _fmt_pct(v: Optional[float]) -> str:
        return f"{v * 100:.0f}%" if v is not None else "—"

    def _block(title: str, action: str, recs: list[dict]) -> list[str]:
        out: list[str] = []
        subset = [r for r in recs if r["action"] == action]
        if not subset:
            return out
        out.append(f"{title} ({len(subset)}):")
        for r in subset:
            star = " (*)" if any(c.startswith("organic-rate-dependent") for c in r.get("caveats", [])) else ""
            out.append(
                f"  SKU {r.get('sku') or '—'}  ASIN {r['asin']}  {r['country_code']}/{r['account_name']}{star}"
            )
            out.append(
                f"    ACOS {_fmt_pct(r.get('current_acos'))} → recommended {_fmt_pct(r.get('recommended_acos'))}"
                f"  |  spend £{r.get('spend', 0):.2f}  units {r.get('units', 0)}"
            )
            out.append(f"    Reason: {r.get('reason', '')}")
            if r.get("caveats"):
                out.append(f"    Caveats: {'; '.join(r['caveats'])}")
        out.append("")
        return out

    all_recs = brief.get("recommendations", [])
    lines += _block("PAUSE", "PAUSE", all_recs)
    lines += _block("REDUCE ACOS", "REDUCE", all_recs)
    lines += _block("INCREASE ACOS (margin supports more spend)", "INCREASE", all_recs)
    # HOLD section is noisy — omit unless explicitly requested.

    lines.append(
        "Notes: SKUs marked (*) are organic-rate-dependent — reducing ads may "
        "erode ranking and therefore organic share. Monitor BSR for 14 days after "
        "any change to these."
    )
    return "\n".join(lines)
