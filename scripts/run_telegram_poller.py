#!/usr/bin/env python3
"""Run the Telegram long-polling driver.

Used by deployments with no public ingress for Telegram webhook
(e.g. Jo's Pip on nbne1, Tailscale-only). Routes all updates
through the same dispatcher the webhook handler uses.

Usage:
    python scripts/run_telegram_poller.py
    python scripts/run_telegram_poller.py --timeout 30 --verbose

Run as a long-running container service alongside the API:

    services:
      jo-pip-poller:
        image: jo-pip-deek:latest
        command: python scripts/run_telegram_poller.py
        depends_on:
          jo-pip-api:
            condition: service_healthy
        environment: ...
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        '--timeout', type=int, default=25,
        help='Long-poll timeout in seconds (default 25, max 50)',
    )
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    log = logging.getLogger('run-telegram-poller')

    if not (os.getenv('TELEGRAM_BOT_TOKEN') or '').strip():
        log.error('TELEGRAM_BOT_TOKEN not set; exiting cleanly')
        return 0

    from core.channels.telegram_polling import run_poll_loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Clean shutdown on SIGTERM (docker stop) — the poll loop is
    # an infinite generator; cancellation is the expected exit path
    def _cancel(signum, frame):
        log.info('signal %s — shutting down', signum)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGTERM, _cancel)
    signal.signal(signal.SIGINT, _cancel)

    try:
        loop.run_until_complete(
            run_poll_loop(long_poll_timeout=args.timeout),
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info('poller exited cleanly')
    except Exception as exc:
        log.exception('poller crashed: %s', exc)
        return 1
    finally:
        try:
            loop.close()
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
