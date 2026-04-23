#!/usr/bin/env python3
"""Generate a one-shot Telegram join code.

Toby runs this on the Hetzner host, gets a short code, opens
Telegram on his phone and sends the code to the @DeekNudgeBot (or
whatever the bot is called). The webhook at
``/api/deek/telegram/webhook`` consumes the code and registers
Toby's chat_id.

Usage:
    python scripts/telegram_join_code.py toby@nbnesigns.com
    python scripts/telegram_join_code.py jo@nbnesigns.com --ttl 60
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('user_email')
    ap.add_argument('--ttl', type=int, default=30,
                    help='Minutes before the code expires (default 30)')
    args = ap.parse_args()

    from core.channels.nudge import record_join_code
    conn = _connect()
    try:
        code = record_join_code(conn, args.user_email,
                                ttl_minutes=args.ttl)
    finally:
        conn.close()
    print()
    print('┌────────────────────────────────────────────────┐')
    print(f'│  Telegram join code for {args.user_email:<20}  │')
    print(f'│                                                │')
    print(f'│                   {code}                     │')
    print(f'│                                                │')
    print(f'│  Expires in {args.ttl} minutes.                        │')
    print('│  Send this code to the Deek bot on Telegram.   │')
    print('└────────────────────────────────────────────────┘')
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
