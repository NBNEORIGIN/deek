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

    log('')
    if failures:
        log(f'SMOKE FAILED: {len(failures)} check(s) failed — {failures}')
        return 1
    log('SMOKE PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
