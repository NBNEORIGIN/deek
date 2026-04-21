#!/usr/bin/env python3
"""Post-deploy smoke test — Deek identity layer + web bundle regression.

Runs AFTER a container recreate, BEFORE calling the deploy done. Fails
the deploy script on drift so the bad image doesn't quietly take over
production traffic.

Scope:
  1. Hit GET <url>/api/deek/identity/status.
  2. Compare to tests/smoke/golden_identity.json:
     - identity_hash must match exactly
     - declared_modules must match exactly (order-insensitive)
     - reachable set must be a superset of expected_reachable_on_hetzner
  3. If Docker is available locally AND the deek-web container exists,
     grep the compiled bundle for the placeholder API key and fail if
     it's present. Skips cleanly when not on Hetzner.

Usage:
    python tests/smoke/test_identity_deploy.py --url https://deek.nbnesigns.co.uk

Exit codes:
    0 — all checks passed
    1 — at least one check failed
    2 — test harness failure (fixture missing, malformed response, network)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# stdlib only — this runs on arbitrary deploy hosts with no pip install
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


FIXTURE_PATH = Path(__file__).parent / 'golden_identity.json'
PLACEHOLDER_KEY = 'deek-dev-key-change-in-production'
# Grep EVERY compiled route handler — SWC folds process.env.* into any
# route that reads it at module scope (which is 25 files today), so one
# ad-hoc docker build without the build-arg could silently bake the
# placeholder into any of them.
BUNDLE_DIR = '/app/.next/server/app/api'

# ── Cron-health check (added 2026-04-21) ────────────────────────────
# After the Memory Brief session where the IMAP poll was silently
# failing for 5 days (cron referenced an old script name after the
# cairn→deek rename), we added this to catch the class. Scans the
# tail of common cron log files for invocation-level errors that
# indicate the cron entry is broken. Distinguishes from task-level
# errors (those are noisy and not what we want to surface).
CRON_LOG_PATHS = (
    '/var/log/deek-dream.log',
    '/var/log/deek-dream-maint.log',
    '/var/log/deek-memory-brief.log',
    '/var/log/deek-memory-brief-replies.log',
    '/var/log/cairn-email-ingest.log',
    '/var/log/cairn-triage.log',
    '/var/log/cairn-digest.log',
    '/var/log/cairn-wiki-compile.log',
    '/var/log/cairn-wiki-sync.log',
    '/var/log/cairn-material-prices.log',
    '/var/log/cairn-crm-reflection.log',
)

# Invocation-level failure patterns. Seeing ANY of these in the last
# tail means the cron entry itself is broken (wrong path, missing
# container, etc.) — not that a well-formed task had a runtime error.
CRON_BROKEN_PATTERNS = (
    "can't open file",                    # python: wrong script path
    "No such file or directory",          # bash or docker can't find it
    "No such container",                  # docker exec against a dead container
    "executable file not found in",       # wrong entrypoint
    "command not found",                  # shell couldn't find the binary
    "ImportError:",                       # python can't load the module
    "ModuleNotFoundError:",
)


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(code: str, detail: str) -> None:
    """Structured failure to stderr + human-readable to stdout."""
    log(f'[FAIL] [{code}] {detail}')
    print(json.dumps({'check': code, 'status': 'fail', 'detail': detail}),
          file=sys.stderr)


def passed(code: str) -> None:
    log(f'[ OK ] [{code}]')


def check_identity(url: str, golden: dict) -> list[str]:
    """Return list of failure codes; empty list = all checks passed."""
    failures: list[str] = []
    endpoint = url.rstrip('/') + '/api/deek/identity/status'
    log(f'-> GET {endpoint}')
    # Cloudflare blocks urllib's default UA (Error 1010). Send a real-
    # browser-shaped UA since this endpoint is explicitly intended to
    # be hit by automation on every deploy.
    req = Request(endpoint, headers={
        'Accept': 'application/json',
        'User-Agent': 'deek-smoke-test/1.0 (+https://github.com/NBNEORIGIN/deek)',
    })
    try:
        with urlopen(req, timeout=10) as r:
            status = r.status
            body = r.read().decode('utf-8', errors='replace')
    except HTTPError as exc:
        fail('identity.http',
             f'status_code={exc.code} body={exc.read()[:200]!r}')
        return ['identity.http']
    except (URLError, Exception) as exc:
        fail('identity.fetch', f'{type(exc).__name__}: {exc}')
        return ['identity.fetch']

    if status != 200:
        fail('identity.http',
             f'status_code={status} body={body[:200]}')
        return ['identity.http']

    try:
        live = json.loads(body)
    except Exception as exc:
        fail('identity.json', f'{type(exc).__name__}: response was not JSON')
        return ['identity.json']

    # ── hash match ─────────────────────────────────────────────────────
    live_hash = live.get('identity_hash')
    want_hash = golden['identity_hash']
    if live_hash == want_hash:
        passed('identity.hash')
    else:
        fail('identity.hash',
             f'expected={want_hash} live={live_hash} '
             '(did DEEK_IDENTITY.md or DEEK_MODULES.yaml change without a '
             'fixture update? see tests/smoke/README.md)')
        failures.append('identity.hash')

    # ── declared modules match (order-insensitive) ────────────────────
    live_declared = set(live.get('declared_modules') or [])
    want_declared = set(golden['declared_modules'])
    if live_declared == want_declared:
        passed('identity.declared_modules')
    else:
        missing = sorted(want_declared - live_declared)
        extra = sorted(live_declared - want_declared)
        fail('identity.declared_modules',
             f'missing={missing} extra={extra}')
        failures.append('identity.declared_modules')

    # ── reachable ⊇ expected ─────────────────────────────────────────
    reach = live.get('reachability') or {}
    live_reachable = {n for n, v in reach.items() if v.get('reachable')}
    want_reachable = set(golden['expected_reachable_on_hetzner'])
    missing_reach = want_reachable - live_reachable
    if not missing_reach:
        extras = sorted(live_reachable - want_reachable)
        extras_note = f' (tolerated extras: {extras})' if extras else ''
        passed('identity.reachability' + extras_note)
    else:
        details = []
        for m in sorted(missing_reach):
            err = (reach.get(m) or {}).get('last_error', 'missing from response')
            details.append(f'{m}({err})')
        fail('identity.reachability',
             f'expected reachable but not: {", ".join(details)}')
        failures.append('identity.reachability')

    return failures


def check_cron_health() -> list[str]:
    """Scan cron log tails for invocation-level failures.

    The class of bug this catches: cron entry references a path that
    no longer exists (rename gone wrong, script moved, container
    renamed). Every 15 minutes the cron fires, fails the same way,
    logs an identical error, and no one notices until the downstream
    work (email parsing, wiki sync, etc.) goes stale.

    Uses the log file's MTIME as the freshness proxy — if a log
    hasn't been written to for over CRON_STALE_HOURS, we don't count
    any of its old errors (the cron may have been fixed since, or
    retired; flagging historical failures permanently blocks
    deploys long after the underlying fix). If the log IS fresh,
    any broken-invocation pattern in the last tail is still a fail
    — because a fresh log means the cron fired recently, and a
    fresh log containing "No such file" means it failed recently.

    Skips cleanly when the log files aren't reachable (dev-box run).

    Returns list of failure codes; empty = passed.
    """
    import time
    failures: list[str] = []

    existing_logs = [p for p in CRON_LOG_PATHS if os.path.isfile(p)]
    if not existing_logs:
        log('[SKIP] [cron.health] no cron logs reachable on this host')
        return []

    # 2h mtime window — long enough to catch a cron that's been
    # broken across multiple recent firings, short enough that a
    # log containing only historical failures doesn't block deploys
    # after the underlying cron has been fixed.
    CRON_STALE_HOURS = 2
    now = time.time()
    stale_cutoff = CRON_STALE_HOURS * 3600

    broken_logs: list[tuple[str, str]] = []
    stale_count = 0
    for path in existing_logs:
        try:
            mtime = os.path.getmtime(path)
            if now - mtime > stale_cutoff:
                stale_count += 1
                continue  # log hasn't been written in >2h; ignore
            with open(path, 'rb') as f:
                try:
                    f.seek(-65536, 2)
                except OSError:
                    f.seek(0)
                tail = f.read().decode('utf-8', errors='replace').splitlines()[-40:]
        except Exception as exc:
            log(f'[SKIP] [cron.health] cannot read {path}: {exc}')
            continue

        for line in tail:
            for pattern in CRON_BROKEN_PATTERNS:
                if pattern in line:
                    broken_logs.append((path, line.strip()[:200]))
                    break
            if broken_logs and broken_logs[-1][0] == path:
                break  # one hit per log is enough

    if broken_logs:
        detail = '; '.join(f'{p}: {msg!r}' for p, msg in broken_logs)
        fail('cron.health',
             f'{len(broken_logs)} cron(s) silently failing — {detail}')
        failures.append('cron.health')
    else:
        note = f'scanned {len(existing_logs) - stale_count} active cron logs'
        if stale_count:
            note += f' (skipped {stale_count} stale > {CRON_STALE_HOURS}h)'
        passed(f'cron.health ({note})')
    return failures


def check_web_bundle() -> list[str]:
    """Assert the compiled web route has no placeholder API key.

    Requires Docker access to the deek-web container. Skips cleanly if
    not on the Hetzner host.
    """
    if not shutil.which('docker'):
        log('[SKIP] SKIP [bundle.placeholder] docker not available on this host')
        return []
    try:
        ls = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as exc:
        log(f'[SKIP] [bundle.placeholder] docker ps failed: {exc}')
        return []

    containers = [n for n in (ls.stdout or '').splitlines()
                  if 'deek-web' in n]
    if not containers:
        log('[SKIP] [bundle.placeholder] deek-web container not running here')
        return []
    container = containers[0]

    try:
        grep = subprocess.run(
            ['docker', 'exec', container, 'sh', '-c',
             # -r recurses, -l lists files only, --include limits to
             # compiled route handlers. All 25 API routes go through
             # this tree so one scan covers the entire blast radius.
             f'grep -rl --include="route.js" "{PLACEHOLDER_KEY}" '
             f'{BUNDLE_DIR} 2>/dev/null || true'],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        log(f'[SKIP] [bundle.placeholder] docker exec failed: {exc}')
        return []

    hits = [ln for ln in (grep.stdout or '').splitlines() if ln.strip()]
    if hits:
        # Trim /app/.next/server/app/api/ prefix for readable output.
        short = [h.replace(BUNDLE_DIR + '/', '') for h in hits]
        fail('bundle.placeholder',
             f'placeholder key baked into {len(hits)} route(s): '
             f'{short} — was DEEK_API_KEY build-arg passed? '
             'see deploy/build-deek-web.sh and audit R3.')
        return ['bundle.placeholder']
    passed(f'bundle.placeholder (scanned all routes in {BUNDLE_DIR})')
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url', required=True,
                    help='Base URL, e.g. https://deek.nbnesigns.co.uk')
    ap.add_argument('--skip-bundle', action='store_true',
                    help='Skip the web bundle placeholder check')
    args = ap.parse_args()

    if not FIXTURE_PATH.exists():
        print(f'FATAL: fixture not found at {FIXTURE_PATH}', file=sys.stderr)
        return 2
    try:
        golden = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
    except Exception as exc:
        print(f'FATAL: fixture malformed: {exc}', file=sys.stderr)
        return 2

    log('== Deek post-deploy smoke test ==')
    log(f'url:      {args.url}')
    log(f'fixture:  {FIXTURE_PATH}')
    log(f'captured: {golden.get("fixture_captured_at")}')
    log('')

    failures: list[str] = []
    failures += check_identity(args.url, golden)
    if not args.skip_bundle:
        failures += check_web_bundle()
    failures += check_cron_health()

    log('')
    if failures:
        log(f'SMOKE FAILED: {len(failures)} check(s) failed — {failures}')
        return 1
    log('SMOKE PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
