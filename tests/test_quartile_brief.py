"""
Classification-logic tests for the Quartile brief generator.

No DB required — tests exercise the pure classify_sku / classify_all functions
with synthetic aggregates. DB query wrappers are exercised in integration
tests once real ads data flows through the Hetzner pipeline.
"""
from core.amazon_intel.margin.quartile_brief import (
    SkuAdAggregate,
    SkuOrdersAggregate,
    classify_sku,
    classify_all,
    render_brief_text,
    DEFAULT_NON_AD_COST_PCT,
    MIN_SPEND_FOR_RECOMMENDATION,
    LOW_VOLUME_FLAG_UNITS,
    ORGANIC_DEPENDENCY_THRESHOLD,
)


def _ad(asin="B0X", sku="SKU1", spend=10.0, ad_sales=50.0, profile="P1",
        country="UK", account="Origin Trading") -> SkuAdAggregate:
    return SkuAdAggregate(
        asin=asin, sku=sku, profile_id=profile, country_code=country,
        account_name=account, spend=spend, ad_sales=ad_sales,
        ad_orders=int(ad_sales / 10), impressions=1000, clicks=50,
    )


def _orders(asin="B0X", marketplace="UK", units=50, revenue=500.0) -> SkuOrdersAggregate:
    return SkuOrdersAggregate(
        asin=asin, marketplace=marketplace, units=units, revenue=revenue
    )


# ── Low spend: excluded ───────────────────────────────────────────────────────


def test_sku_below_min_spend_is_excluded():
    ad = _ad(spend=MIN_SPEND_FOR_RECOMMENDATION - 0.01)
    assert classify_sku(ad, _orders()) is None


# ── PAUSE cases ───────────────────────────────────────────────────────────────


def test_zero_ad_sales_triggers_pause():
    """Spend > 0 but no ad-attributed sales — burning money."""
    rec = classify_sku(_ad(spend=20.0, ad_sales=0.0), _orders())
    assert rec is not None
    assert rec.action == "PAUSE"
    assert "zero" in rec.reason.lower()


def test_acos_over_100pct_triggers_pause():
    """Spend exceeds ad-attributed revenue."""
    rec = classify_sku(_ad(spend=100.0, ad_sales=80.0), _orders(revenue=200.0))
    assert rec is not None
    assert rec.action == "PAUSE"
    assert rec.current_acos is not None and rec.current_acos > 1.0


def test_acos_exactly_100pct_also_triggers_pause():
    """At break-even (ACOS=100%), COGS and fees still have to be paid — it's a
    guaranteed loss. Must not fall through to REDUCE."""
    rec = classify_sku(_ad(spend=50.0, ad_sales=50.0), _orders(revenue=100.0, units=30))
    assert rec is not None
    assert rec.action == "PAUSE"


# ── REDUCE cases ──────────────────────────────────────────────────────────────


def test_high_acos_with_low_organic_triggers_reduce():
    """
    Spend £40, ad sales £100 → ACOS 40%.
    Revenue £200 (so organic rate = 50%).
    max_tacos = 18%, recommended_acos = 18% / 50% = 36%.
    Current 40% > 36% × 1.2 = 43.2%? No — 40% < 43.2%, so HOLD.
    Push ACOS higher to force REDUCE.
    """
    rec = classify_sku(
        _ad(spend=60.0, ad_sales=100.0),   # ACOS 60%
        _orders(revenue=200.0, units=50),   # organic 50%, recommended ACOS 36%
    )
    assert rec is not None
    assert rec.action == "REDUCE"
    assert rec.current_acos is not None and rec.recommended_acos is not None
    assert rec.current_acos > rec.recommended_acos


def test_reduce_includes_current_and_recommended_in_reason():
    rec = classify_sku(
        _ad(spend=60.0, ad_sales=100.0),
        _orders(revenue=200.0, units=50),
    )
    assert rec is not None
    assert rec.action == "REDUCE"
    assert "Current ACOS" in rec.reason
    assert "recommended" in rec.reason


# ── INCREASE cases ────────────────────────────────────────────────────────────


def test_low_acos_with_volume_triggers_increase():
    """ACOS well below recommended AND volume >= MIN_UNITS_FOR_INCREASE → scale up."""
    # Spend £10, ad_sales £200 → ACOS 5%
    # Revenue £1000 (organic 80%) → recommended_acos = 18% / 20% = 90%
    # 5% < 90% × 0.5 = 45% → INCREASE
    rec = classify_sku(
        _ad(spend=10.0, ad_sales=200.0),
        _orders(revenue=1000.0, units=100),   # well above MIN_UNITS_FOR_INCREASE
    )
    assert rec is not None
    assert rec.action == "INCREASE"


def test_low_acos_low_volume_does_not_increase():
    """Same math but too few units — don't recommend scaling."""
    rec = classify_sku(
        _ad(spend=10.0, ad_sales=200.0),
        _orders(revenue=1000.0, units=5),   # below LOW_VOLUME_FLAG_UNITS threshold
    )
    assert rec is not None
    # Must not be INCREASE; low-volume caveat present
    assert rec.action != "INCREASE"
    assert any("low-volume" in c for c in rec.caveats)


# ── HOLD cases ────────────────────────────────────────────────────────────────


def test_acos_within_band_holds():
    """Current ACOS between 0.5x and 1.2x of recommended → HOLD."""
    # Spend £30, ad_sales £100 → ACOS 30%
    # Revenue £200 (organic 50%) → recommended_acos = 36%
    # 30% / 36% = 0.83 → between 0.5x and 1.2x → HOLD
    rec = classify_sku(
        _ad(spend=30.0, ad_sales=100.0),
        _orders(revenue=200.0, units=50),
    )
    assert rec is not None
    assert rec.action == "HOLD"


# ── Organic-rate-dependent caveat ─────────────────────────────────────────────


def test_high_organic_share_flags_caveat():
    """Organic rate > threshold → flag in caveats (cutting ads may erode ranking)."""
    rec = classify_sku(
        _ad(spend=50.0, ad_sales=50.0),       # ACOS 100% — will PAUSE
        _orders(revenue=1000.0, units=100),   # organic 95%
    )
    assert rec is not None
    assert rec.organic_rate is not None and rec.organic_rate > ORGANIC_DEPENDENCY_THRESHOLD
    assert any("organic-rate-dependent" in c for c in rec.caveats)


# ── Missing-orders-data fallback ──────────────────────────────────────────────


def test_no_orders_data_uses_max_tacos_fallback():
    """If orders aggregate is missing entirely, fall back to max_tacos as ACOS target."""
    rec = classify_sku(_ad(spend=20.0, ad_sales=100.0), orders=None)
    assert rec is not None
    assert rec.total_revenue == 0.0
    assert rec.organic_rate is None
    # Recommended falls back to max_tacos = 1 - non_ad_cost_pct
    expected_max_tacos = round(1.0 - DEFAULT_NON_AD_COST_PCT, 4)
    assert rec.recommended_acos == expected_max_tacos
    assert any("no-orders-data" in c for c in rec.caveats)


# ── Attribution-artefact flag ─────────────────────────────────────────────────


def test_ad_sales_greater_than_revenue_flags_caveat():
    """Ad-attributed sales > total revenue can happen due to 7-day attribution
    window overlapping the report boundary. Organic rate clamps to 0; flag it."""
    rec = classify_sku(
        _ad(spend=20.0, ad_sales=150.0),
        _orders(revenue=100.0, units=20),
    )
    assert rec is not None
    assert rec.organic_rate == 0.0
    assert any("attribution-window artefact" in c for c in rec.caveats)


# ── classify_all + sort ───────────────────────────────────────────────────────


def test_classify_all_joins_on_asin_and_country():
    ad_rows = [
        _ad(asin="B01", country="UK"),
        _ad(asin="B02", country="US", account="Origin Designers"),
    ]
    order_rows = [
        _orders(asin="B01", marketplace="UK", units=50, revenue=500.0),
        _orders(asin="B02", marketplace="US", units=20, revenue=200.0),
    ]
    recs = classify_all(ad_rows, order_rows)
    assert len(recs) == 2
    by_asin = {r.asin: r for r in recs}
    assert by_asin["B01"].country_code == "UK"
    assert by_asin["B02"].country_code == "US"
    assert by_asin["B01"].total_revenue == 500.0
    assert by_asin["B02"].total_revenue == 200.0


def test_classify_all_handles_missing_orders_side():
    """Ad row with no matching orders row falls through to no-orders caveat, not exclusion."""
    recs = classify_all([_ad(asin="B01")], [])
    assert len(recs) == 1
    assert recs[0].total_revenue == 0.0


# ── Text rendering ────────────────────────────────────────────────────────────


def test_render_brief_text_includes_all_sections():
    # Inline a mini brief structure rather than hitting the DB
    ad_rows = [
        _ad(asin="B01", sku="SKU-UK-01", spend=60.0, ad_sales=100.0),   # REDUCE
        _ad(asin="B02", sku="SKU-UK-02", spend=20.0, ad_sales=0.0),     # PAUSE
        _ad(asin="B03", sku="SKU-UK-03", spend=10.0, ad_sales=200.0),   # INCREASE
    ]
    order_rows = [
        _orders(asin="B01", marketplace="UK", revenue=200.0, units=50),
        _orders(asin="B02", marketplace="UK", revenue=300.0, units=30),
        _orders(asin="B03", marketplace="UK", revenue=1000.0, units=100),
    ]
    recs = classify_all(ad_rows, order_rows)
    brief = {
        "marketplace": "UK",
        "generated_at": "2026-04-14T00:00:00+00:00",
        "basis": {
            "lookback_days": 30,
            "target_margin_pct": 0.06,
            "non_ad_cost_pct": 0.82,
            "max_tacos": 0.18,
        },
        "summary": {
            "total_skus_with_spend": len(recs),
            "counts": {
                "PAUSE": sum(1 for r in recs if r.action == "PAUSE"),
                "REDUCE": sum(1 for r in recs if r.action == "REDUCE"),
                "INCREASE": sum(1 for r in recs if r.action == "INCREASE"),
                "HOLD": sum(1 for r in recs if r.action == "HOLD"),
            },
        },
        "recommendations": [r.__dict__ for r in recs],
    }
    text = render_brief_text(brief)
    assert "Subject:" in text
    assert "Summary:" in text
    assert "PAUSE" in text
    assert "REDUCE" in text
    assert "INCREASE" in text
    assert "SKU-UK-01" in text
    assert "SKU-UK-02" in text
    assert "SKU-UK-03" in text


