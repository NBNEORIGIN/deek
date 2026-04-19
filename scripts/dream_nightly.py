#!/usr/bin/env python3
"""Dream state nightly entry point — Brief 4 Phase A.

Intended to be invoked by Hetzner cron at 02:30 UTC (Phase B wires
the cron). Runnable manually for testing.

Usage:
    python scripts/dream_nightly.py                  # full run, writes
    python scripts/dream_nightly.py --dry-run        # no writes
    python scripts/dream_nightly.py --seed-limit 5   # smaller seed set
    python scripts/dream_nightly.py --max-attempts 10 # cap LLM calls

Exit codes:
    0 — ran to completion (including runs that produce zero candidates
        — that's expected at low memory volume)
    1 — fatal setup error (DB down, OLLAMA unreachable, etc.)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--window', type=int, default=30)
    ap.add_argument('--seed-limit', type=int, default=20)
    ap.add_argument('--max-attempts', type=int, default=100)
    ap.add_argument('--max-surface', type=int, default=5)
    ap.add_argument('--budget-seconds', type=int, default=1800)
    ap.add_argument('--model', type=str, default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )
    log = logging.getLogger('dream')

    from core.dream.nocturnal import run_nocturnal_loop
    try:
        stats = run_nocturnal_loop(
            window_days=args.window,
            seed_limit=args.seed_limit,
            max_attempts=args.max_attempts,
            max_surface=args.max_surface,
            runtime_budget_seconds=float(args.budget_seconds),
            model=args.model,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        log.exception('nocturnal loop crashed: %s', exc)
        return 1

    log.info(
        'dream run: seeds=%d bundles=%d llm_calls=%d null=%d parse_fail=%d '
        'raw=%d runtime=%.1fs errors=%d dry_run=%s',
        stats.seeds_sampled, stats.bundles_built, stats.llm_calls,
        stats.null_responses, stats.parse_failures, stats.raw_candidates,
        stats.runtime_seconds, len(stats.errors), args.dry_run,
    )
    if stats.errors:
        for err in stats.errors[:5]:
            log.warning('error: %s', err)
    return 0


if __name__ == '__main__':
    sys.exit(main())
