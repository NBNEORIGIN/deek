"""
SP-API sync scheduler.

Runs inventory + analytics + advertising syncs across all configured regions.
Tracks sync state in ami_spapi_sync_log table.
Designed to be called as a FastAPI BackgroundTask — synchronous, blocking.

Schedule: 4x daily (every 6 hours). Enforced by checking last_completed_at
in the sync log — won't re-run if last sync was < MIN_INTERVAL_HOURS ago,
unless force=True.
"""
import logging
import traceback
from datetime import datetime, timezone
from typing import Callable

from core.amazon_intel.db import get_conn
from .client import Region

logger = logging.getLogger(__name__)

MIN_INTERVAL_HOURS = 6
ACTIVE_REGIONS: list[Region] = ['EU']  # Add 'NA', 'FE' once credentials confirmed


def _log_start(sync_type: str, region: str) -> int:
    """Insert a sync log entry. Returns log ID."""
    logger.info("SP-API sync starting: type=%s region=%s", sync_type, region)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ami_spapi_sync_log (sync_type, region, status)
                   VALUES (%s, %s, 'running') RETURNING id""",
                (sync_type, region),
            )
            log_id = cur.fetchone()[0]
            conn.commit()
    return log_id


def _log_complete(log_id: int, result: dict):
    import json
    logger.info("SP-API sync complete: log_id=%d result=%s", log_id, result)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE ami_spapi_sync_log
                   SET status = 'complete', completed_at = NOW(), result_json = %s
                   WHERE id = %s""",
                (json.dumps(result), log_id),
            )
            conn.commit()


def _log_error(log_id: int, error: str):
    logger.error("SP-API sync error: log_id=%d error=%s", log_id, error[:500])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE ami_spapi_sync_log
                   SET status = 'error', completed_at = NOW(), error = %s
                   WHERE id = %s""",
                (error[:2000], log_id),
            )
            conn.commit()


def _run_logged(sync_type: str, region: str, fn: Callable, **kwargs):
    """
    Wrapper used by individual sync endpoints to get the same logging
    and status tracking as the full scheduler.
    """
    log_id = _log_start(sync_type, region)
    try:
        result = fn(**kwargs)
        _log_complete(log_id, result)
    except Exception as e:
        _log_error(log_id, traceback.format_exc())
        raise


def _is_due(sync_type: str, region: str) -> bool:
    """Return True if this sync type+region hasn't run in MIN_INTERVAL_HOURS."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT completed_at FROM ami_spapi_sync_log
                   WHERE sync_type = %s AND region = %s AND status = 'complete'
                   ORDER BY completed_at DESC LIMIT 1""",
                (sync_type, region),
            )
            row = cur.fetchone()

    if not row or not row[0]:
        return True

    last = row[0]
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    return hours_since >= MIN_INTERVAL_HOURS


def sync_region(region: Region, force: bool = False) -> dict:
    """
    Run all sync types for a single region.
    Returns summary of what ran and results.
    """
    from .inventory import sync_inventory
    from .analytics import sync_analytics
    from .advertising import sync_advertising, ADS_PROFILE_IDS

    results = {}

    # Inventory
    if force or _is_due('inventory', region):
        log_id = _log_start('inventory', region)
        try:
            r = sync_inventory(region)
            _log_complete(log_id, r)
            results['inventory'] = {'status': 'complete', **r}
        except Exception as e:
            err = traceback.format_exc()
            _log_error(log_id, err)
            results['inventory'] = {'status': 'error', 'error': str(e)}
    else:
        results['inventory'] = {'status': 'skipped', 'reason': 'not due'}

    # Analytics
    if force or _is_due('analytics', region):
        log_id = _log_start('analytics', region)
        try:
            r = sync_analytics(region, days=30)
            _log_complete(log_id, r)
            results['analytics'] = {'status': 'complete', **r}
        except Exception as e:
            err = traceback.format_exc()
            _log_error(log_id, err)
            results['analytics'] = {'status': 'error', 'error': str(e)}
    else:
        results['analytics'] = {'status': 'skipped', 'reason': 'not due'}

    # Advertising (only if profile ID is configured)
    profile_id = ADS_PROFILE_IDS.get(region, '')
    if profile_id:
        if force or _is_due('advertising', region):
            log_id = _log_start('advertising', region)
            try:
                r = sync_advertising(region, profile_id=profile_id, days=30)
                _log_complete(log_id, r)
                results['advertising'] = {'status': 'complete', **r}
            except Exception as e:
                err = traceback.format_exc()
                _log_error(log_id, err)
                results['advertising'] = {'status': 'error', 'error': str(e)}
        else:
            results['advertising'] = {'status': 'skipped', 'reason': 'not due'}
    else:
        results['advertising'] = {'status': 'skipped', 'reason': 'no profile id configured'}

    return {'region': region, 'syncs': results}


def run_full_sync(regions: list[Region] | None = None, force: bool = False) -> dict:
    """
    Run sync for all active regions. Called from BackgroundTasks.
    Returns summary dict.
    """
    target_regions = regions or ACTIVE_REGIONS
    results = {}
    for region in target_regions:
        results[region] = sync_region(region, force=force)
    return {'regions': results, 'synced_at': datetime.now(timezone.utc).isoformat()}


def get_sync_status(limit: int = 20) -> list[dict]:
    """Return recent sync log entries."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, sync_type, region, status, started_at, completed_at, error
                   FROM ami_spapi_sync_log
                   ORDER BY started_at DESC LIMIT %s""",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    for r in rows:
        for k, v in r.items():
            if hasattr(v, 'isoformat'):
                r[k] = v.isoformat()
    return rows
