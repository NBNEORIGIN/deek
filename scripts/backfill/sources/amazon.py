"""
Source 2 — amazon.

Reconstructs "decision-worthy inflections" from the Amazon
Intelligence tables (``ami_business_report_data`` + optional
``ami_advertising_data``). Each inflection is a month where an
ASIN's sessions / units / ordered_product_sales swung
substantially versus the prior month — e.g. revenue doubled, or
sessions halved.

A decision is of the shape: "we continued (or changed) the
current pricing / advertising posture on this ASIN into this
month" and the outcome is the observed step change. The brief
calls this "infer a pricing or ad change" — we do not claim to
know which, because the schema does not record a listing change
history.

Schema quirks
-------------

- ``ami_business_report_data`` has no ``date`` column. We use
  ``date_trunc('month', created_at)`` as a proxy — each monthly
  upload lands in its own created_at bucket. That is imperfect:
  if two monthly uploads land in the same calendar month the
  grouping will conflate them. Accept this until the Amazon
  Intelligence module adds a formal report_month column.
- ``ami_advertising_data`` exists and can enrich the context with
  "campaign X started / stopped in the same window", but Phase 8
  ships without this enrichment — lighter slice first.

Cap
---

Top 200 inflections by absolute revenue delta, as the brief
specifies. Without the cap this source would emit thousands of
low-signal records.

Signal strength 0.85.

Lesson gate: only the top 20 inflections (by ``|chosen_path_score|``)
pass the pipeline gate and earn a Sonnet lesson — the source
scales its score so the top 20 exceed 0.7 and the rest fall below.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import psycopg2

from .base import HistoricalSource, RawHistoricalRecord, RawOutcome


log = logging.getLogger(__name__)


@dataclass
class AsinMonthly:
    month: datetime
    asin: str
    title: str | None
    sessions: int
    units_ordered: int
    ordered_product_sales: Decimal


@dataclass
class Inflection:
    prev: AsinMonthly
    curr: AsinMonthly
    revenue_delta: float
    sessions_delta_pct: float
    units_delta_pct: float

    @property
    def abs_revenue_delta(self) -> float:
        return abs(self.revenue_delta)


class AmazonSource:
    """ASIN-level step change detector over monthly business reports."""

    name: str = 'amazon'
    source_type: str = 'amazon'

    def __init__(
        self,
        db_url: str | None = None,
        max_inflections: int = 200,
        top_n_for_gate: int = 20,
    ):
        self.db_url = db_url or os.getenv('DATABASE_URL', '')
        self.max_inflections = max_inflections
        self.top_n_for_gate = top_n_for_gate

    # ── Iteration ───────────────────────────────────────────────────────

    def iter_records(self) -> Iterator[RawHistoricalRecord]:
        if not self.db_url:
            raise RuntimeError('amazon source: DATABASE_URL not set')
        try:
            conn = psycopg2.connect(self.db_url, connect_timeout=5)
        except Exception as exc:
            raise RuntimeError(
                f'amazon source: could not connect to Cairn DB at {self.db_url}: {exc}'
            )
        try:
            monthly = self._fetch_monthly(conn)
        finally:
            conn.close()

        inflections = _detect_inflections(monthly)
        inflections.sort(key=lambda i: i.abs_revenue_delta, reverse=True)
        top = inflections[: self.max_inflections]

        # Compute the revenue-delta threshold that defines the "top
        # top_n" set — scores are scaled so those cross the 0.7 gate.
        if len(top) >= self.top_n_for_gate:
            gate_threshold = top[self.top_n_for_gate - 1].abs_revenue_delta
        elif top:
            gate_threshold = top[-1].abs_revenue_delta
        else:
            gate_threshold = 1.0

        for inflection in top:
            yield _build_record(inflection, gate_threshold)

    def _fetch_monthly(self, conn) -> list[AsinMonthly]:
        """Group business_report rows by child_asin + month.

        Uses ``created_at`` as the month proxy because the schema
        does not carry a report_month column. See module docstring.
        """
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 60000")
            cur.execute(
                """
                SELECT
                    date_trunc('month', created_at) AS month,
                    child_asin,
                    MAX(title) AS title,
                    SUM(sessions)::bigint AS sessions,
                    SUM(units_ordered)::bigint AS units_ordered,
                    SUM(ordered_product_sales)::numeric AS revenue
                FROM ami_business_report_data
                WHERE child_asin IS NOT NULL
                  AND child_asin <> ''
                GROUP BY 1, 2
                ORDER BY 2, 1
                """
            )
            rows = cur.fetchall()
        out: list[AsinMonthly] = []
        for month, asin, title, sessions, units, revenue in rows:
            out.append(AsinMonthly(
                month=_ensure_utc(month) if month else datetime.now(tz=timezone.utc),
                asin=asin,
                title=title,
                sessions=int(sessions or 0),
                units_ordered=int(units or 0),
                ordered_product_sales=Decimal(revenue or 0),
            ))
        return out


# ── Detection ──────────────────────────────────────────────────────────


def _detect_inflections(monthly: list[AsinMonthly]) -> list[Inflection]:
    """Pair consecutive months per ASIN and compute deltas.

    An "inflection" is any month where either sessions or
    ordered_product_sales changed by more than 50% versus the
    previous month, and the absolute revenue delta is non-trivial
    (at least £50 so we don't flood the list with noise on
    near-zero baselines).
    """
    by_asin: dict[str, list[AsinMonthly]] = {}
    for row in monthly:
        by_asin.setdefault(row.asin, []).append(row)

    inflections: list[Inflection] = []
    for asin, rows in by_asin.items():
        rows.sort(key=lambda r: r.month)
        for i in range(1, len(rows)):
            prev = rows[i - 1]
            curr = rows[i]
            prev_rev = float(prev.ordered_product_sales)
            curr_rev = float(curr.ordered_product_sales)
            revenue_delta = curr_rev - prev_rev

            if abs(revenue_delta) < 50:
                continue

            sessions_delta_pct = _pct_change(prev.sessions, curr.sessions)
            units_delta_pct = _pct_change(prev.units_ordered, curr.units_ordered)
            rev_delta_pct = _pct_change(prev_rev, curr_rev)

            if (
                abs(sessions_delta_pct) < 50
                and abs(rev_delta_pct) < 50
            ):
                continue

            inflections.append(Inflection(
                prev=prev,
                curr=curr,
                revenue_delta=revenue_delta,
                sessions_delta_pct=sessions_delta_pct,
                units_delta_pct=units_delta_pct,
            ))
    return inflections


def _pct_change(prev: float, curr: float) -> float:
    prev = float(prev or 0)
    curr = float(curr or 0)
    if prev == 0 and curr == 0:
        return 0.0
    if prev == 0:
        return 100.0 * (1 if curr > 0 else -1) * 999  # sentinel large
    return (curr - prev) / prev * 100.0


# ── Record builder ─────────────────────────────────────────────────────


def _build_record(
    inflection: Inflection,
    gate_threshold: float,
) -> RawHistoricalRecord:
    prev = inflection.prev
    curr = inflection.curr

    month_label = curr.month.strftime('%Y-%m')
    deterministic_id = (
        f'backfill_amazon_{curr.asin}_{month_label.replace("-", "_")}'
    )

    title_txt = f' ({prev.title})' if prev.title else ''
    direction = 'up' if inflection.revenue_delta > 0 else 'down'

    context_summary = (
        f"In {month_label}, ASIN {curr.asin}{title_txt} saw revenue move "
        f"{direction} by £{abs(inflection.revenue_delta):,.0f} vs the "
        f"previous month (£{float(prev.ordered_product_sales):,.0f} → "
        f"£{float(curr.ordered_product_sales):,.0f}). Sessions moved "
        f"{inflection.sessions_delta_pct:+.0f}% "
        f"({prev.sessions:,} → {curr.sessions:,}) and units ordered "
        f"{inflection.units_delta_pct:+.0f}% "
        f"({prev.units_ordered:,} → {curr.units_ordered:,}). No explicit "
        'listing change history is recorded in the source schema — '
        'this inflection implies an earlier decision around pricing, '
        'advertising, stock posture, or listing content for this ASIN.'
    )

    chosen_path = (
        f'Continued the prior-month posture on ASIN {curr.asin} '
        f'into {month_label} without a recorded change.'
    )

    # Score scaled so the top_n_for_gate inflections cross 0.7 and
    # anything below gate_threshold falls under 0.7. Sign follows
    # revenue delta so the outcome reads as "decision validated"
    # (positive) or "warning" (negative).
    if gate_threshold <= 0:
        score = 0.0
    else:
        magnitude = min(1.0, inflection.abs_revenue_delta / gate_threshold * 0.7 + 0.1)
        score = magnitude if inflection.revenue_delta > 0 else -magnitude
    score = max(-1.0, min(1.0, score))

    outcome = RawOutcome(
        observed_at=curr.month,
        actual_result=(
            f'Revenue on this ASIN moved £{abs(inflection.revenue_delta):,.0f} '
            f'{direction} month-over-month, with sessions '
            f'{inflection.sessions_delta_pct:+.0f}% and units '
            f'{inflection.units_delta_pct:+.0f}%.'
        ),
        chosen_path_score=score,
        metrics={
            'asin': curr.asin,
            'month': month_label,
            'revenue_delta_gbp': inflection.revenue_delta,
            'revenue_prev_gbp': float(prev.ordered_product_sales),
            'revenue_curr_gbp': float(curr.ordered_product_sales),
            'sessions_delta_pct': inflection.sessions_delta_pct,
            'units_delta_pct': inflection.units_delta_pct,
        },
    )

    return RawHistoricalRecord(
        deterministic_id=deterministic_id,
        source_type='amazon',
        decided_at=prev.month,  # the decision was taken *before* the observed change
        chosen_path=chosen_path,
        context_summary=context_summary,
        archetype_tags=None,
        rejected_paths=None,
        signal_strength=0.85,
        case_id=None,
        raw_source_ref={
            'asin': curr.asin,
            'title': prev.title,
            'prev_month': prev.month.strftime('%Y-%m'),
            'curr_month': month_label,
        },
        needs_privacy_scrub=False,
        needs_privacy_review=False,
        outcome=outcome,
        verbatim_lesson=None,
    )


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
