"""
ad_spend_per_sku — one-shot CSV exporter for Amazon Sponsored Products
ad spend per (sku, asin, marketplace, profile_id) over a date range.

Reads:
  ami_advertising_data       — populated by core/amazon_intel/spapi/advertising.py
  ami_advertising_profiles   — joined for the marketplace mapping

Writes:
  CSV to stdout (so the caller can redirect: `... > spend.csv`)

Aggregation:
  GROUP BY sku, asin, marketplace, profile_id over rows where
  report_date BETWEEN --start AND --end. Metrics are SUMmed.
  acos and roas are RECOMPUTED from the summed totals (NOT averaged)
  to avoid the cardinality-weighting trap of averaging ratios.

  Rows where spend = 0 AND impressions = 0 are dropped (no signal).

Marketplace handling:
  ami_advertising_profiles.country_code is the source of truth.
  Amazon historically uses 'UK' in some surfaces and 'GB' in others;
  we normalise UK -> GB on output. (Same direction the SP-API has been
  moving — GB is ISO-3166, UK is the legacy alias.)

Freshness gate:
  Before generating the CSV we run a per-marketplace MAX(report_date)
  check. If anything is more than 2 days behind today, we WARN to
  stderr but proceed — the operator can re-trigger the sync via
  cron /etc/cron.d/cairn-spapi or:
      docker exec deploy-deek-api-1 python scripts/run_ami_sync.py --force

  Use --strict-freshness to refuse instead of warning.

Date range:
  Capped at 90 days. Larger ranges are refused — the brief is
  deliberate about this; the table can carry years of history at
  ~10k rows/day so a 12-month query would be 3M+ rows.

Usage (Hetzner / public Deek):
    docker exec -i deploy-deek-api-1 \
        python scripts/ad_spend_per_sku.py --start 2026-04-01 --end 2026-04-30 \
        > spend.csv

Usage (local / Rex):
    docker exec -i jo-pip-api \
        python scripts/ad_spend_per_sku.py --start 2026-04-01 --end 2026-04-30 \
        > spend.csv

Or freshness-only (no CSV output):
    docker exec deploy-deek-api-1 \
        python scripts/ad_spend_per_sku.py --freshness-check
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Resolve repo root so this works whether invoked as a module or a file
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env if present (no-op inside Docker where env is already injected)
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / '.env')
except ImportError:
    pass

from core.amazon_intel.db import get_conn  # noqa: E402

MAX_RANGE_DAYS = 90
STALE_THRESHOLD_DAYS = 2
UNKNOWN_SKU = '(unknown)'

CSV_COLUMNS = [
    'sku',
    'asin',
    'marketplace',
    'profile_id',
    'impressions',
    'clicks',
    'spend',
    'attributed_sales_7d',
    'attributed_units_7d',
    'acos',
    'roas',
]


def _parse_date(s: str) -> date:
    """Parse YYYY-MM-DD. argparse-friendly: raises ArgumentTypeError on bad input."""
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date {s!r}; expected YYYY-MM-DD"
        )


def _normalise_marketplace(code: str | None) -> str:
    """UK -> GB; None/empty -> '(unknown)'."""
    if not code:
        return UNKNOWN_SKU
    code = code.strip().upper()
    if code == 'UK':
        return 'GB'
    return code


def freshness_check(cur) -> list[tuple[str, date | None, int]]:
    """Per-marketplace (max report_date, row count). Marketplace is post-normalisation
    (UK -> GB), so the same physical country shows up as one row even if the underlying
    profile rows mix UK and GB.
    """
    cur.execute(
        """
        SELECT p.country_code        AS country_code,
               MAX(ad.report_date)   AS latest,
               COUNT(*)              AS rows
        FROM ami_advertising_data ad
        JOIN ami_advertising_profiles p USING (profile_id)
        GROUP BY p.country_code
        ORDER BY p.country_code
        """
    )
    # Re-aggregate after UK->GB normalisation in Python so the operator sees
    # the same labels they'll see in the CSV.
    raw = cur.fetchall()
    bucket: dict[str, tuple[date | None, int]] = {}
    for cc, latest, n in raw:
        mk = _normalise_marketplace(cc)
        cur_latest, cur_n = bucket.get(mk, (None, 0))
        new_latest = max([d for d in (cur_latest, latest) if d is not None], default=None)
        bucket[mk] = (new_latest, cur_n + (n or 0))
    return [(mk, lat, n) for mk, (lat, n) in sorted(bucket.items())]


def print_freshness(rows: list[tuple[str, date | None, int]], today: date) -> list[str]:
    """Print to stderr; return list of stale marketplace labels."""
    print('[ad_spend_per_sku] freshness check:', file=sys.stderr)
    print('  marketplace | latest report_date | rows | staleness', file=sys.stderr)
    print('  ----------- | ------------------ | ---- | ---------', file=sys.stderr)
    stale: list[str] = []
    for mk, latest, n in rows:
        if latest is None:
            staleness = 'NO DATED ROWS'
            stale.append(mk)
        else:
            days = (today - latest).days
            staleness = f'{days}d behind'
            if days > STALE_THRESHOLD_DAYS:
                stale.append(mk)
                staleness += '  ⚠'
        print(f'  {mk:<11} | {str(latest):<18} | {n:<4} | {staleness}', file=sys.stderr)
    return stale


def export_csv(cur, start: date, end: date) -> tuple[int, Decimal]:
    """Run the aggregation query and stream CSV to stdout.
    Returns (row_count, total_spend) for the sanity check.
    """
    cur.execute(
        """
        SELECT
            COALESCE(NULLIF(TRIM(ad.sku), ''), %s)   AS sku,
            COALESCE(ad.asin, '')                    AS asin,
            p.country_code                           AS country_code,
            ad.profile_id                            AS profile_id,
            SUM(ad.impressions)                      AS impressions,
            SUM(ad.clicks)                           AS clicks,
            SUM(ad.spend)                            AS spend,
            SUM(ad.sales_7d)                         AS attributed_sales_7d,
            SUM(ad.orders_7d)                        AS attributed_units_7d
        FROM ami_advertising_data ad
        JOIN ami_advertising_profiles p USING (profile_id)
        WHERE ad.report_date BETWEEN %s AND %s
        GROUP BY 1, 2, 3, 4
        HAVING NOT (COALESCE(SUM(ad.spend), 0) = 0 AND COALESCE(SUM(ad.impressions), 0) = 0)
        ORDER BY SUM(ad.spend) DESC NULLS LAST,
                 SUM(ad.impressions) DESC NULLS LAST,
                 sku, country_code
        """,
        (UNKNOWN_SKU, start, end),
    )

    writer = csv.writer(sys.stdout)
    writer.writerow(CSV_COLUMNS)

    n = 0
    total_spend = Decimal('0')
    for sku, asin, country_code, profile_id, imps, clicks, spend, sales, units in cur:
        spend_d = Decimal(spend or 0)
        sales_d = Decimal(sales or 0)
        # Recompute on the SUM totals, not on per-row ratios.
        acos = (spend_d / sales_d) if sales_d > 0 else None
        roas = (sales_d / spend_d) if spend_d > 0 else None
        writer.writerow([
            sku,
            asin,
            _normalise_marketplace(country_code),
            profile_id,
            int(imps or 0),
            int(clicks or 0),
            f'{spend_d:.2f}',
            f'{sales_d:.2f}',
            int(units or 0),
            f'{acos:.4f}' if acos is not None else '',
            f'{roas:.4f}' if roas is not None else '',
        ])
        n += 1
        total_spend += spend_d

    return n, total_spend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m scripts.ad_spend_per_sku',
        description=__doc__.strip().split('\n')[0],
    )
    parser.add_argument('--start', type=_parse_date,
                        help='Inclusive start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=_parse_date,
                        help='Inclusive end date (YYYY-MM-DD)')
    parser.add_argument('--freshness-check', action='store_true',
                        help='Print per-marketplace freshness and exit (no CSV output)')
    parser.add_argument('--strict-freshness', action='store_true',
                        help='Refuse to generate CSV if any marketplace is >2 days stale '
                             '(default: warn and proceed)')
    args = parser.parse_args(argv)

    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            fresh_rows = freshness_check(cur)
            stale = print_freshness(fresh_rows, today)

            if args.freshness_check:
                return 0

            # Need a date range to actually export.
            if args.start is None or args.end is None:
                print('[ad_spend_per_sku] --start and --end are required '
                      '(unless --freshness-check)', file=sys.stderr)
                return 2

            if args.end < args.start:
                print(f'[ad_spend_per_sku] --end {args.end} is before --start {args.start}',
                      file=sys.stderr)
                return 2

            range_days = (args.end - args.start).days + 1
            if range_days > MAX_RANGE_DAYS:
                print(
                    f'[ad_spend_per_sku] refusing: requested {range_days} day range '
                    f'exceeds cap of {MAX_RANGE_DAYS} days. Run multiple smaller queries '
                    f'and concat the CSVs if you need a longer view.',
                    file=sys.stderr,
                )
                return 2

            if stale:
                msg = (f'[ad_spend_per_sku] WARNING: marketplaces stale (>{STALE_THRESHOLD_DAYS}d '
                       f'or no dated rows): {", ".join(stale)}. CSV will silently '
                       f'underreport these. Re-run the sync first:\n'
                       f'  docker exec deploy-deek-api-1 python scripts/run_ami_sync.py --force')
                print(msg, file=sys.stderr)
                if args.strict_freshness:
                    print('[ad_spend_per_sku] --strict-freshness set; aborting.',
                          file=sys.stderr)
                    return 1

            print(f'[ad_spend_per_sku] exporting {args.start} to {args.end} ({range_days} days)',
                  file=sys.stderr)
            n, total = export_csv(cur, args.start, args.end)
            print(f'[ad_spend_per_sku] wrote {n} rows; total spend in range = {total:.2f}',
                  file=sys.stderr)

    return 0


if __name__ == '__main__':
    sys.exit(main())
