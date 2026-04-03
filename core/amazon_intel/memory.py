"""
Push Amazon listing intelligence data into Cairn memory for
conversational retrieval.

Writes to:
  1. SQLite decisions table (via MemoryStore) — for /memory/retrieve
  2. Weekly report summary — for conversational queries
"""
import os
import uuid
from datetime import datetime
from core.amazon_intel.db import get_conn


def index_snapshots_to_memory():
    """
    Write listing snapshot summaries into Cairn's SQLite memory
    for the amazon-intelligence project. Only indexes listings
    with performance data (no point indexing 3,000 "no data" entries).
    """
    from core.memory.store import MemoryStore

    data_dir = os.getenv('CLAW_DATA_DIR', './data')
    store = MemoryStore('amazon-intelligence', data_dir)
    session_id = f'ami_index_{datetime.now().strftime("%Y%m%d_%H%M")}'

    snapshots = _get_scored_snapshots()
    written = 0

    for snap in snapshots:
        asin = snap['asin']
        sku = snap['sku'] or ''
        m_number = snap['m_number'] or ''
        title = (snap['title'] or '')[:120]
        score = snap['health_score']
        diag = snap['diagnosis_codes'] or []
        issues = snap['issues'] or []

        # Build a searchable description
        parts = [f"ASIN: {asin}"]
        if sku:
            parts.append(f"SKU: {sku}")
        if m_number:
            parts.append(f"M-number: {m_number}")
        parts.append(f"Health: {score}/10")

        if snap.get('sessions_30d') is not None:
            parts.append(f"Sessions: {snap['sessions_30d']:,}")
        if snap.get('conversion_rate') is not None:
            parts.append(f"Conversion: {snap['conversion_rate']:.1%}")
        if snap.get('buy_box_pct') is not None:
            parts.append(f"Buy Box: {snap['buy_box_pct']:.1%}")
        if snap.get('acos') is not None:
            parts.append(f"ACOS: {snap['acos']:.1%}")
        if snap.get('ordered_revenue_30d') is not None:
            parts.append(f"Revenue: \u00a3{snap['ordered_revenue_30d']:,.2f}")

        parts.append(f"Bullets: {snap.get('bullet_count', 0)}")
        parts.append(f"Images: {snap.get('image_count', 0)}")

        if diag:
            parts.append(f"Diagnosis: {', '.join(diag)}")
        if issues:
            parts.append(f"Issues: {', '.join(issues[:5])}")

        description = '. '.join(parts) + '.'

        # Build reasoning with actionable recommendations
        recs = snap.get('recommendations') or []
        reasoning = '; '.join(recs) if recs else 'No specific recommendations.'

        store.record_decision(
            session_id=session_id,
            decision_type='listing_snapshot',
            description=f"{title} | {description}",
            reasoning=reasoning,
            files_affected=[],
            project='amazon-intelligence',
            query=f"listing health {asin} {sku} {m_number} {title}",
            rejected='',
            model_used='analysis_pipeline',
        )
        written += 1

    # Write the weekly report summary as a single high-level entry
    report_summary = _build_report_memory_entry()
    if report_summary:
        store.record_decision(
            session_id=session_id,
            decision_type='weekly_report',
            description=report_summary['description'],
            reasoning=report_summary['detail'],
            files_affected=[],
            project='amazon-intelligence',
            query='amazon listing health report weekly summary underperformers',
            rejected='',
            model_used='analysis_pipeline',
        )
        written += 1

    store.close()

    return {
        'session_id': session_id,
        'snapshots_indexed': written - 1,  # minus the report entry
        'report_indexed': True,
        'total_written': written,
    }


def _get_scored_snapshots() -> list[dict]:
    """Get latest snapshots that have performance data (worth indexing)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (asin)
                    asin, sku, m_number, title, health_score,
                    diagnosis_codes, issues, recommendations,
                    sessions_30d, conversion_rate, buy_box_pct,
                    units_ordered_30d, ordered_revenue_30d,
                    acos, ad_spend_30d,
                    bullet_count, image_count, your_price
                FROM ami_listing_snapshots
                WHERE sessions_30d IS NOT NULL
                ORDER BY asin, snapshot_date DESC
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _build_report_memory_entry() -> dict | None:
    """Build a summary memory entry from the latest weekly report."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT report_date, total_asins, avg_health_score,
                       critical_count, attention_count, healthy_count,
                       no_data_count, summary
                FROM ami_weekly_reports
                ORDER BY report_date DESC LIMIT 1
            """)
            row = cur.fetchone()
            if not row:
                return None

            report_date, total, avg_score, critical, attention, healthy, no_data, summary = row

            description = (
                f"Amazon Listing Health Report — {report_date}. "
                f"{total} ASINs analysed. Avg score: {avg_score}/10. "
                f"Critical: {critical}. Needs attention: {attention}. "
                f"Healthy: {healthy}. No data: {no_data}."
            )

            # Get top underperformers for the detail
            cur.execute("""
                SELECT asin, sku, title, health_score, diagnosis_codes
                FROM ami_listing_snapshots
                WHERE health_score IS NOT NULL AND health_score < 7
                    AND sessions_30d IS NOT NULL
                ORDER BY health_score ASC
                LIMIT 10
            """)
            underperformers = cur.fetchall()

            detail_parts = [summary or '']
            for u in underperformers:
                asin, sku, title, score, diag = u
                title_short = (title or '')[:50]
                detail_parts.append(
                    f"{sku or asin} ({score}/10): {title_short} [{', '.join(diag or [])}]"
                )

            return {
                'description': description,
                'detail': ' | '.join(detail_parts),
            }
