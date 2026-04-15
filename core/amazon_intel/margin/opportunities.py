"""
SKU opportunity ranking + listing-quality correlation.

Builds on quartile_brief: takes the Recommendation rows produced by
classify_all() and scores each SKU by a composite "opportunity score"
combining three signals:

  1. waste_gbp        — £ of ad spend currently above the recommended ACOS.
                        Concrete over-spend that Quartile can claw back.
  2. scale_gbp        — £ of ad-driven revenue Quartile is *leaving on the
                        table* for INCREASE candidates where margin supports
                        more spend.
  3. revenue_weight   — log-scaled total revenue so high-volume SKUs win
                        ties without swamping mid-volume ones.

Opportunity score = (waste + scale) × log10(1 + total_revenue).

The score is denominated in weighted £ so Toby / the Quartile rep can read
it directly rather than reasoning about a normalised index.

For the top N high-ACOS underperformers (REDUCE and PAUSE) we then cross-
reference against ami_listing_content and ask an LLM whether listing
quality issues plausibly correlate with the ad-efficiency problem. This
distinguishes "the listing is fine; ads need tightening" from "the listing
is the reason ads aren't converting — fix the page first."
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .quartile_brief import NEW_PRODUCT_CAVEAT_PREFIX, Recommendation

logger = logging.getLogger(__name__)


# ── Tunables ──────────────────────────────────────────────────────────────────
DEFAULT_TOP_N = 20
DEFAULT_ANALYSIS_LIMIT = 8          # how many high-ACOS rows get LLM review
HIGH_ACOS_THRESHOLD = 0.40          # only analyse listings for ACOS ≥ 40%
ANALYSIS_ACTIONS = ("REDUCE", "PAUSE")
SCALE_WEIGHT = 0.5                  # scale_gbp is hypothetical — discount vs waste_gbp
LLM_TIMEOUT_S = 45


# ── Scoring ──────────────────────────────────────────────────────────────────


@dataclass
class ScoreComponents:
    waste_gbp: float
    scale_gbp: float
    revenue_weight: float
    opportunity_score: float


def score_recommendation(rec: Recommendation) -> ScoreComponents:
    """Return a ScoreComponents for a single Recommendation.

    Math (all £-denominated except revenue_weight):
      waste_gbp = max(0, spend − recommended_acos × ad_sales)
                  — how much of current spend is above the target ACOS line.
                    Only populated when we have both current and recommended ACOS.
      scale_gbp = max(0, (spend / recommended_acos) − ad_sales) × SCALE_WEIGHT
                  — only for INCREASE candidates; how much extra ad-driven
                    revenue target-ACOS would support at current spend.
                    Discounted because it's hypothetical.
      revenue_weight = log10(1 + total_revenue)
                  — gentle lift for high-volume SKUs.
      opportunity_score = (waste_gbp + scale_gbp) × revenue_weight
    """
    # New-product rows are intentionally excluded from the opportunity
    # ranking — their ACOS inefficiency may be real but reflects the launch
    # phase, not something Quartile should act on yet. The override in
    # quartile_brief tags these with a caveat; we check for it here.
    if any(c.startswith(NEW_PRODUCT_CAVEAT_PREFIX) for c in (rec.caveats or [])):
        return ScoreComponents(
            waste_gbp=0.0,
            scale_gbp=0.0,
            revenue_weight=0.0,
            opportunity_score=0.0,
        )

    spend = float(rec.spend or 0)
    ad_sales = float(rec.ad_sales or 0)
    total_revenue = float(rec.total_revenue or 0)
    target_acos = rec.recommended_acos

    waste = 0.0
    if (
        target_acos is not None
        and target_acos >= 0
        and ad_sales > 0
        and rec.current_acos is not None
        and rec.current_acos > target_acos
    ):
        waste = max(0.0, spend - target_acos * ad_sales)

    scale = 0.0
    if rec.action == "INCREASE" and target_acos and target_acos > 0 and spend > 0:
        target_ad_sales = spend / target_acos
        scale = max(0.0, (target_ad_sales - ad_sales)) * SCALE_WEIGHT

    # Revenue weight. Fallback 0.3 when no revenue data so the row doesn't
    # drop to zero purely because orders haven't synced yet.
    if total_revenue > 0:
        revenue_weight = math.log10(1.0 + total_revenue)
    else:
        revenue_weight = 0.3

    opportunity_score = (waste + scale) * revenue_weight

    return ScoreComponents(
        waste_gbp=round(waste, 2),
        scale_gbp=round(scale, 2),
        revenue_weight=round(revenue_weight, 3),
        opportunity_score=round(opportunity_score, 2),
    )


def rank_opportunities(
    recommendations: list[dict],
    limit: int = DEFAULT_TOP_N,
) -> list[dict]:
    """Score + rank a list of recommendation dicts (as returned by generate_brief).

    Returns the top `limit` rows sorted by opportunity_score desc, each
    augmented with `opportunity_score` and `score_components`.
    """
    scored: list[dict] = []
    for r in recommendations:
        # Rebuild a lightweight Recommendation from the dict to reuse scoring.
        rec = _rec_from_dict(r)
        comp = score_recommendation(rec)
        if comp.opportunity_score <= 0:
            continue
        scored.append({
            **r,
            "opportunity_score": comp.opportunity_score,
            "score_components": {
                "waste_gbp": comp.waste_gbp,
                "scale_gbp": comp.scale_gbp,
                "revenue_weight": comp.revenue_weight,
            },
        })
    scored.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return scored[:limit]


def _rec_from_dict(r: dict) -> Recommendation:
    """Minimal shim — we only need the fields score_recommendation reads."""
    return Recommendation(
        asin=r.get("asin", ""),
        sku=r.get("sku"),
        m_number=r.get("m_number"),
        account_name=r.get("account_name", ""),
        country_code=r.get("country_code", ""),
        action=r.get("action", "HOLD"),
        reason=r.get("reason", ""),
        caveats=list(r.get("caveats") or []),
        spend=float(r.get("spend") or 0),
        ad_sales=float(r.get("ad_sales") or 0),
        total_revenue=float(r.get("total_revenue") or 0),
        units=int(r.get("units") or 0),
        current_acos=r.get("current_acos"),
        current_tacos=r.get("current_tacos"),
        organic_rate=r.get("organic_rate"),
        recommended_acos=r.get("recommended_acos"),
    )


# ── Listing content loader ────────────────────────────────────────────────────


def fetch_listing_content(asin: str, marketplace: str) -> Optional[dict]:
    """Pull a single row from ami_listing_content for analysis.

    Returns a trimmed dict with only the fields the LLM needs, or None if
    no row exists. Falls through UK↔GB the same way quartile_brief does.
    """
    from core.amazon_intel.db import get_conn
    from .quartile_brief import _marketplace_variants

    sql = """
        SELECT asin, marketplace, title,
               bullet1, bullet2, bullet3, bullet4, bullet5,
               description, image_count, main_image_url,
               aplus_present, brand, product_type,
               list_price_amount, list_price_currency,
               last_enriched_at
          FROM ami_listing_content
         WHERE asin = %(asin)s
           AND marketplace = ANY(%(mkts)s)
         ORDER BY last_enriched_at DESC NULLS LAST
         LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "asin": asin,
                "mkts": _marketplace_variants(marketplace),
            })
            row = cur.fetchone()

    if not row:
        return None

    bullets = [b for b in (row[3], row[4], row[5], row[6], row[7]) if b]
    return {
        "asin": row[0],
        "marketplace": row[1],
        "title": row[2],
        "bullets": bullets,
        "description": row[8],
        "image_count": row[9] or 0,
        "main_image_url": row[10],
        "aplus_present": bool(row[11]),
        "brand": row[12],
        "product_type": row[13],
        "list_price": {
            "amount": float(row[14]) if row[14] is not None else None,
            "currency": row[15],
        },
        "last_enriched_at": row[16].isoformat() if row[16] else None,
    }


# ── LLM listing quality assessment ────────────────────────────────────────────


_LISTING_ASSESSOR_SYSTEM = (
    "You are an Amazon listing quality reviewer. You assess whether a "
    "listing's content quality plausibly explains poor ad efficiency. You "
    "are blunt, specific, and grounded in what is and isn't on the page. "
    "You do not speculate about keywords you haven't been shown; you judge "
    "only what is visible in the title, bullets, description, image count, "
    "A+ presence, and brand."
)

_LISTING_ASSESSOR_PROMPT = """\
A SKU has poor ad efficiency. Decide whether listing quality correlates with the ACOS problem.

## Ad performance (lookback window)
- Action flagged: {action}
- Current ACOS: {current_acos_pct}
- Recommended ACOS: {recommended_acos_pct}
- Ad spend: £{spend:.2f}
- Ad-attributed revenue: £{ad_sales:.2f}
- Total revenue: £{total_revenue:.2f}
- Units sold: {units}
- Reason classified: {reason}

## Listing content
- Title: {title}
- Bullets ({bullet_count}):
{bullets_bulleted}
- Description: {description_short}
- Image count: {image_count}
- A+ content present: {aplus}
- Brand: {brand}
- Product type: {product_type}
- Last enriched: {last_enriched}

Return strict JSON with this exact shape:
{{
  "quality_score": <integer 1-10, where 10 is excellent>,
  "likely_correlates": <true | false>,
  "verdict": "<one of: 'listing likely explains ACOS', 'listing looks fine — ACOS issue is ads config', 'mixed — listing needs tweaks but not the root cause'>",
  "issues": ["<short concrete issue>", ...],
  "fixes": ["<short concrete fix>", ...]
}}

Rules:
- Issues / fixes: max 4 each, each under 15 words.
- Be concrete ("title missing dimensions" not "title could be better").
- Output JSON only. No prose, no markdown fences.
"""


async def assess_listing_quality(
    rec: dict,
    listing: Optional[dict],
) -> Optional[dict]:
    """Ask Claude whether listing quality correlates with the ACOS problem.

    Returns a dict:
      {"quality_score", "likely_correlates", "verdict", "issues", "fixes",
       "model", "assessed_at", "has_content"}
    Or None if no Anthropic key is available.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    # If we have no listing row at all, return a stub so the UI can show
    # "listing content missing — run catalog sync" rather than silently
    # skipping the row.
    if not listing or not listing.get("title"):
        return {
            "quality_score": None,
            "likely_correlates": None,
            "verdict": "listing content missing — run catalog sync",
            "issues": ["no ami_listing_content row found for this ASIN / marketplace"],
            "fixes": ["trigger Catalog API sync so quality can be assessed"],
            "model": None,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "has_content": False,
        }

    bullets = listing.get("bullets") or []
    bullets_bulleted = (
        "\n".join(f"    {i + 1}. {b}" for i, b in enumerate(bullets))
        if bullets else "    (none)"
    )
    description = listing.get("description") or ""
    description_short = (description[:400] + "…") if len(description) > 400 else description

    def _pct(v):
        return f"{v * 100:.0f}%" if isinstance(v, (int, float)) else "—"

    prompt = _LISTING_ASSESSOR_PROMPT.format(
        action=rec.get("action", "?"),
        current_acos_pct=_pct(rec.get("current_acos")),
        recommended_acos_pct=_pct(rec.get("recommended_acos")),
        spend=float(rec.get("spend") or 0),
        ad_sales=float(rec.get("ad_sales") or 0),
        total_revenue=float(rec.get("total_revenue") or 0),
        units=int(rec.get("units") or 0),
        reason=rec.get("reason", ""),
        title=listing.get("title") or "(missing)",
        bullet_count=len(bullets),
        bullets_bulleted=bullets_bulleted,
        description_short=description_short or "(missing)",
        image_count=listing.get("image_count") or 0,
        aplus="yes" if listing.get("aplus_present") else "no",
        brand=listing.get("brand") or "(unbranded)",
        product_type=listing.get("product_type") or "(uncategorised)",
        last_enriched=listing.get("last_enriched_at") or "(never)",
    )

    from core.models.claude_client import ClaudeClient
    client = ClaudeClient(api_key=api_key)
    try:
        text, _tool, _usage = await asyncio.wait_for(
            client.chat(
                system=_LISTING_ASSESSOR_SYSTEM,
                history=[],
                message=prompt,
            ),
            timeout=LLM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("assess_listing_quality timed out for %s", rec.get("asin"))
        return _stub_assessment("assessment timed out after %ds" % LLM_TIMEOUT_S, has_content=True)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("assess_listing_quality errored for %s: %s", rec.get("asin"), exc)
        return _stub_assessment(f"assessment errored: {type(exc).__name__}", has_content=True)

    parsed = _parse_assessment_json(text)
    if parsed is None:
        return _stub_assessment(
            "model returned non-JSON — raw response kept in logs",
            has_content=True,
        )

    parsed["model"] = client.model
    parsed["assessed_at"] = datetime.now(timezone.utc).isoformat()
    parsed["has_content"] = True
    return parsed


def _stub_assessment(note: str, *, has_content: bool) -> dict:
    return {
        "quality_score": None,
        "likely_correlates": None,
        "verdict": note,
        "issues": [],
        "fixes": [],
        "model": None,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "has_content": has_content,
    }


def _parse_assessment_json(text: str) -> Optional[dict]:
    """Strip any markdown fencing and parse JSON. Returns None on failure."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # Strip leading fence
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Coerce missing keys to sensible defaults.
    return {
        "quality_score": data.get("quality_score"),
        "likely_correlates": data.get("likely_correlates"),
        "verdict": data.get("verdict") or "",
        "issues": list(data.get("issues") or [])[:6],
        "fixes": list(data.get("fixes") or [])[:6],
    }


# ── Orchestration ─────────────────────────────────────────────────────────────


async def analyse_top_opportunities(
    ranked: list[dict],
    *,
    analysis_limit: int = DEFAULT_ANALYSIS_LIMIT,
) -> list[dict]:
    """For the highest-opportunity REDUCE/PAUSE rows with high ACOS, attach a
    listing_analysis block. Runs LLM calls concurrently but caps at
    analysis_limit so the endpoint stays responsive.
    """
    candidates: list[tuple[int, dict]] = []
    for i, row in enumerate(ranked):
        if row.get("action") not in ANALYSIS_ACTIONS:
            continue
        acos = row.get("current_acos")
        if acos is None or acos < HIGH_ACOS_THRESHOLD:
            continue
        candidates.append((i, row))
        if len(candidates) >= analysis_limit:
            break

    async def _one(row: dict) -> Optional[dict]:
        listing = _safe_fetch_listing(row.get("asin"), row.get("country_code"))
        return await assess_listing_quality(row, listing)

    results = await asyncio.gather(
        *[_one(row) for _, row in candidates],
        return_exceptions=True,
    )

    annotated = [dict(r) for r in ranked]
    for (idx, _), result in zip(candidates, results):
        if isinstance(result, Exception):
            logger.warning("listing analysis task raised: %s", result)
            continue
        if result is None:
            continue
        annotated[idx]["listing_analysis"] = result
    return annotated


def _safe_fetch_listing(asin: Optional[str], marketplace: Optional[str]) -> Optional[dict]:
    if not asin or not marketplace:
        return None
    try:
        return fetch_listing_content(asin, marketplace)
    except Exception as exc:  # pragma: no cover
        logger.warning("fetch_listing_content failed for %s/%s: %s", asin, marketplace, exc)
        return None


async def build_opportunities_brief(
    *,
    marketplace: Optional[str] = None,
    lookback_days: int = 30,
    target_margin_pct: float = 0.06,
    non_ad_cost_pct: float = 0.82,
    limit: int = DEFAULT_TOP_N,
    include_listing_analysis: bool = True,
    analysis_limit: int = DEFAULT_ANALYSIS_LIMIT,
    new_product_m_threshold: Optional[int] = None,
    exclude_m_numbers: Optional[list[str]] = None,
) -> dict:
    """End-to-end: generate quartile brief → rank opportunities →
    optionally cross-reference listing content with LLM."""
    from .quartile_brief import generate_brief

    brief = generate_brief(
        marketplace=marketplace,
        lookback_days=lookback_days,
        target_margin_pct=target_margin_pct,
        non_ad_cost_pct=non_ad_cost_pct,
        new_product_m_threshold=new_product_m_threshold,
        exclude_m_numbers=exclude_m_numbers,
    )
    ranked = rank_opportunities(brief.get("recommendations", []), limit=limit)

    analysed_count = 0
    if include_listing_analysis and ranked:
        ranked = await analyse_top_opportunities(ranked, analysis_limit=analysis_limit)
        analysed_count = sum(1 for r in ranked if r.get("listing_analysis"))

    return {
        "marketplace": brief.get("marketplace"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "basis": brief.get("basis"),
        "scoring": {
            "formula": "(waste_gbp + scale_gbp) * log10(1 + total_revenue)",
            "scale_weight": SCALE_WEIGHT,
            "high_acos_threshold": HIGH_ACOS_THRESHOLD,
            "analysis_actions": list(ANALYSIS_ACTIONS),
        },
        "counts": {
            "total_ranked": len(ranked),
            "analysed_listings": analysed_count,
            "total_brief_recommendations": len(brief.get("recommendations", [])),
        },
        "opportunities": ranked,
    }
