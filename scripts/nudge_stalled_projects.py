#!/usr/bin/env python3
"""Stalled-project nudge trigger.

Daily cron. Asks the CRM for projects in LEAD/QUOTED stage with no
recent activity, then queues a Deek nudge per stalled project.
Cooldown prevents re-nudging the same project within 72 hours.

Shadow-mode is handled in core.channels.nudge.send_pending — this
script always queues, the sender decides whether to actually fire.

Usage:
    python scripts/nudge_stalled_projects.py
    python scripts/nudge_stalled_projects.py --dry-run
    python scripts/nudge_stalled_projects.py --stale-days 7
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


CRM_BASE_URL = os.getenv('CRM_BASE_URL', 'https://crm.nbnesigns.co.uk').rstrip('/')
STALE_STAGES = ('LEAD', 'QUOTED', 'NEGOTIATING')


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _token() -> str:
    return (os.getenv('DEEK_API_KEY')
            or os.getenv('CAIRN_API_KEY')
            or os.getenv('CLAW_API_KEY', '')).strip()


def _fetch_stalled_projects(stale_days: int) -> list[dict]:
    """Ask the CRM for projects matching our stale criteria.

    We don't have a dedicated stalled-projects endpoint on the CRM
    yet (would be a follow-up brief). Instead we pull via
    search_crm with a broad query and filter locally — fine at
    current volume (~dozens of active projects).
    """
    token = _token()
    if not token:
        logging.warning('no bearer token — skipping')
        return []
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                f'{CRM_BASE_URL}/api/cairn/search',
                params={'q': '*', 'types': 'project', 'limit': 50},
                headers={'Authorization': f'Bearer {token}'},
            )
    except Exception as exc:
        logging.warning('crm fetch failed: %s', exc)
        return []
    if r.status_code != 200:
        logging.warning('crm returned %d', r.status_code)
        return []
    try:
        data = r.json() or {}
    except Exception:
        return []
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=stale_days)
    out = []
    for row in data.get('results') or []:
        md = row.get('metadata') or {}
        stage = (md.get('stage') or '').upper()
        if stage not in STALE_STAGES:
            continue
        last_activity_raw = (
            md.get('last_activity_at')
            or md.get('updated_at')
            or md.get('updatedAt')
            or ''
        )
        if not last_activity_raw:
            continue
        try:
            last_activity = dt.datetime.fromisoformat(
                str(last_activity_raw).replace('Z', '+00:00')
            )
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        if last_activity >= cutoff:
            continue
        days_stale = int((now - last_activity).total_seconds() / 86400)
        out.append({
            'project_id': row.get('source_id') or '',
            'project_name': md.get('project_name') or row.get('title') or '?',
            'client': md.get('client') or '?',
            'stage': stage,
            'value': md.get('value'),
            'days_stale': days_stale,
            'last_activity_at': last_activity_raw,
        })
    out.sort(key=lambda p: -p['days_stale'])
    return out


def _format_message(project: dict) -> str:
    amt = project.get('value')
    amt_s = f"£{amt:,.0f}" if amt else '(no quoted value)'
    return (
        f"🕰️ *Stalled project nudge*\n\n"
        f"*{project['project_name']}*\n"
        f"Client: {project['client']}\n"
        f"Stage: {project['stage']}  ·  {amt_s}\n"
        f"Last activity: {project['days_stale']} days ago\n\n"
        f"Chase, or log a reason to de-prioritise?"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true',
                    help='Fetch + format but do not queue')
    ap.add_argument('--stale-days', type=int, default=7)
    ap.add_argument('--target-user', default='toby@nbnesigns.com')
    ap.add_argument('--max-per-run', type=int, default=5)
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    log = logging.getLogger('nudge-stalled')

    stalled = _fetch_stalled_projects(args.stale_days)
    log.info('stalled projects found: %d', len(stalled))
    if not stalled:
        return 0

    from core.channels.nudge import queue_nudge
    conn = None
    if not args.dry_run:
        try:
            conn = _connect()
        except Exception as exc:
            log.error('db connect failed: %s', exc)
            return 1

    queued = skipped = errored = 0
    try:
        for project in stalled[:args.max_per_run]:
            message = _format_message(project)
            log.info(
                '  %s  [%s]  %dd stale',
                project['project_name'][:50],
                project['stage'], project['days_stale'],
            )
            if args.dry_run:
                if args.verbose:
                    log.info('    %s', message.replace('\n', ' // '))
                continue
            result = queue_nudge(
                conn,
                kind='stalled_project',
                user_email=args.target_user,
                message=message,
                related_ref=f"project:{project['project_id']}",
                cooldown_hours=72,
                context={
                    'project_id': project['project_id'],
                    'project_name': project['project_name'],
                    'client': project['client'],
                    'stage': project['stage'],
                    'value': project.get('value'),
                    'days_stale': project['days_stale'],
                },
            )
            if result.state == 'pending':
                queued += 1
            elif result.state == 'skipped':
                skipped += 1
                log.debug('    skipped (cooldown)')
            else:
                errored += 1
                log.warning('    error: %s', result.detail)
    finally:
        if conn is not None:
            conn.close()

    log.info('done: queued=%d skipped=%d errors=%d dry_run=%s',
             queued, skipped, errored, args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
