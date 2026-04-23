#!/usr/bin/env python3
"""Deek nudges cutover — flip DEEK_NUDGES_SHADOW to false.

Scheduled one-shot via cron for 2026-05-20 09:00 UTC. Checks that
nudges have been logged enough to be worth surfacing, then:

  1. Updates /opt/nbne/deek/deploy/.env — DEEK_NUDGES_SHADOW=false
  2. Restarts deek-api so the env takes effect
  3. Writes a cutover record to data/nudges_cutover.jsonl

Mirrors scripts/impressions_cutover.py and scripts/crosslink_cutover.py.

Safety gates:
    - At least 20 debug rows logged in deek_nudges
    - Span of at least 3 days between first and last row
    - Env file exists and is writable
    - deek-api container is running

Usage:
    python scripts/nudges_cutover.py              # safe mode
    python scripts/nudges_cutover.py --dry-run    # report only
    python scripts/nudges_cutover.py --force      # skip gates
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ENV_FILE = Path(os.getenv('DEEK_ENV_FILE', '/opt/nbne/deek/deploy/.env'))
CUTOVER_LOG = Path(os.getenv(
    'DEEK_NUDGES_CUTOVER_LOG',
    str(REPO_ROOT / 'data' / 'nudges_cutover.jsonl'),
))
CONTAINER_NAME = os.getenv('DEEK_API_CONTAINER', 'deploy-deek-api-1')
DEPLOY_DIR = Path(os.getenv('DEEK_DEPLOY_DIR', '/opt/nbne/deek/deploy'))

MIN_RECORDS = 20
MIN_SPAN_HOURS = 72

log = logging.getLogger('nudges_cutover')


def read_shadow_stats() -> dict:
    """Count rows + span in cairn_intel.deek_nudges."""
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        return {'records': 0, 'span_hours': 0, 'reviewed_count': 0,
                'error': 'DATABASE_URL not set'}
    try:
        with psycopg2.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*),
                              COALESCE(
                                EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at)))/3600.0,
                                0
                              )::int,
                              COUNT(*) FILTER (WHERE toby_reviewed = TRUE)
                         FROM cairn_intel.deek_nudges"""
                )
                n, span_h, useful = cur.fetchone()
        return {
            'records': int(n or 0),
            'span_hours': int(span_h or 0),
            'reviewed_count': int(useful or 0),
        }
    except Exception as exc:
        return {'records': 0, 'span_hours': 0, 'reviewed_count': 0,
                'error': f'{type(exc).__name__}: {exc}'}


def run_gates(stats: dict, force: bool = False) -> tuple[bool, list[str]]:
    if force:
        return True, ['--force: all gates bypassed']
    reasons: list[str] = []
    if stats.get('error'):
        reasons.append(f'stats read failed: {stats["error"]}')
    n = stats.get('records', 0)
    if n < MIN_RECORDS:
        reasons.append(f'only {n} debug rows (need >= {MIN_RECORDS})')
    span = stats.get('span_hours', 0)
    if span < MIN_SPAN_HOURS:
        reasons.append(f'span only {span}h (need >= {MIN_SPAN_HOURS}h)')
    return len(reasons) == 0, reasons


def flip_env_file(env_file: Path) -> bool:
    if not env_file.exists():
        log.error('env file not found: %s', env_file)
        return False
    try:
        lines = env_file.read_text(encoding='utf-8').splitlines(keepends=True)
    except Exception as exc:
        log.error('cannot read env: %s', exc)
        return False
    key = 'DEEK_NUDGES_SHADOW'
    wanted = f'{key}=false\n'
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.lstrip().startswith(f'{key}=') and not line.lstrip().startswith('#'):
            out.append(wanted)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and not out[-1].endswith('\n'):
            out.append('\n')
        out.append('\n# Quote review — cutover (auto-applied)\n')
        out.append(wanted)
    tmp = env_file.with_suffix(env_file.suffix + '.nudgcut-tmp')
    try:
        tmp.write_text(''.join(out), encoding='utf-8')
        shutil.move(str(tmp), str(env_file))
    except Exception as exc:
        log.error('cannot write env: %s', exc)
        try:
            tmp.unlink()
        except Exception:
            pass
        return False
    return True


def restart_container(name: str) -> bool:
    try:
        short = name.replace('deploy-', '').replace('-1', '')
        subprocess.run(
            ['docker', 'compose', 'up', '-d', '--force-recreate', short],
            cwd=str(DEPLOY_DIR),
            check=True,
            capture_output=True,
            timeout=60,
        )
        r = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Running}}', name],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip() == 'true'
    except Exception as exc:
        log.error('restart failed: %s', exc)
        return False


def write_cutover_record(record: dict) -> None:
    try:
        CUTOVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CUTOVER_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record) + '\n')
    except Exception as exc:
        log.warning('cutover log write failed: %s', exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    stats = read_shadow_stats()
    log.info('--- deek nudges shadow stats ---')
    for k, v in stats.items():
        log.info('  %s = %s', k, v)

    ok, reasons = run_gates(stats, force=args.force)
    record = {
        'ran_at': datetime.now(timezone.utc).isoformat(),
        'stats': stats,
        'gates_passed': ok,
        'reasons': reasons,
        'forced': bool(args.force),
        'dry_run': bool(args.dry_run),
        'cutover_applied': False,
    }

    if not ok:
        log.warning('CUTOVER BLOCKED: %s', '; '.join(reasons))
        write_cutover_record(record)
        return 0

    if args.dry_run:
        log.info('DRY RUN — gates passed; would flip env + restart')
        write_cutover_record(record)
        return 0

    log.info('cutting over: flipping env + restarting container')
    if not flip_env_file(ENV_FILE):
        record['abort_reason'] = 'env flip failed'
        write_cutover_record(record)
        return 1
    if not restart_container(CONTAINER_NAME):
        record['cutover_applied'] = True
        record['abort_reason'] = 'restart failed (env already flipped)'
        write_cutover_record(record)
        return 1
    record['cutover_applied'] = True
    write_cutover_record(record)
    log.info('CUTOVER COMPLETE — DEEK_NUDGES_SHADOW=false')
    return 0


if __name__ == '__main__':
    sys.exit(main())
