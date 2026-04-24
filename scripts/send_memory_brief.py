#!/usr/bin/env python3
"""Memory Brief Phase A — daily send.

Invoked by Hetzner cron at 07:30 UTC. Generates the day's question
set from live memory state, composes the email, records the run in
`memory_brief_runs`, and sends via SMTP (or logs to stdout under
--dry-run).

Idempotency: a non-dry-run send for (user_email, today) that already
completed successfully is a no-op. Failed sends can be retried
without `--force`.

Usage:
    python scripts/send_memory_brief.py --user toby@nbnesigns.com
    python scripts/send_memory_brief.py --user toby@... --dry-run
    python scripts/send_memory_brief.py --user toby@... --force

Exit codes:
    0 — sent, dry-run printed, or already-sent-today (idempotent skip)
    1 — fatal error (DB unreachable, SMTP misconfigured when not dry)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


log = logging.getLogger('memory-brief')


# ── DB helpers ────────────────────────────────────────────────────────

def _connect():
    import psycopg2
    db_url = os.getenv('DATABASE_URL', '')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set')
    return psycopg2.connect(db_url, connect_timeout=5)


def _already_sent_today(conn, user_email: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM memory_brief_runs
                WHERE user_email = %s
                  AND generated_at::date = CURRENT_DATE
                  AND dry_run = FALSE
                  AND delivery_status = 'sent'
                LIMIT 1""",
            (user_email,),
        )
        return cur.fetchone() is not None


def _insert_run(
    conn, user_email: str, questions: list, subject: str, body: str,
    dry_run: bool, delivery_status: str, error: str | None,
    *, outgoing_message_id: str | None = None,
    delivered_via: str = 'email',
) -> str:
    run_id = str(uuid.uuid4())
    questions_json = json.dumps([
        {
            'category': q.category,
            'prompt': q.prompt,
            'reply_format': q.reply_format,
            'provenance': q.provenance,
        }
        for q in questions
    ])
    now = datetime.now(timezone.utc)
    delivered_at = now if delivery_status == 'sent' else None
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO memory_brief_runs
                (id, user_email, generated_at, questions, subject,
                 body_text, delivery_status, delivered_at, error,
                 dry_run, outgoing_message_id, delivered_via)
               VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                run_id, user_email, now, questions_json, subject,
                body, delivery_status, delivered_at, error, dry_run,
                outgoing_message_id, delivered_via,
            ),
        )
    conn.commit()
    return run_id


# ── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--user', required=True, help='User email to send to')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print to stdout, do not send, still record the run')
    ap.add_argument('--force', action='store_true',
                    help='Send even if already sent today')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )

    # Open DB first so the idempotency guard + insert path is ready
    # before we spend time generating questions.
    try:
        conn = _connect()
    except Exception as exc:
        log.error('db connect failed: %s', exc)
        return 1

    try:
        if not args.dry_run and not args.force:
            if _already_sent_today(conn, args.user):
                log.info('already sent today to %s — skipping (use --force to override)',
                         args.user)
                return 0

        from core.brief.questions import generate_questions
        from core.brief.composer import (
            compose_email, send_via_smtp, SMTPNotConfigured,
        )
        from core.brief.user_profile import get_profile

        profile = get_profile(args.user)
        channel = profile.channel

        question_set = generate_questions(args.user)
        log.info('generated %d question(s) for %s (channel=%s)',
                 len(question_set.questions), args.user, channel)
        for n in question_set.notes:
            log.info('  note: %s', n)

        # Always compose the email body — it's the canonical
        # persistence format stored on memory_brief_runs. Telegram
        # path uses a re-rendered version but the row captures the
        # email body too for audit + fallback.
        email = compose_email(
            user_email=args.user,
            generated_at=question_set.generated_at,
            questions=question_set.questions,
            notes=question_set.notes,
        )

        if args.dry_run:
            print(f'--- DRY RUN — channel={channel}, not sending ---')
            print(f'To:      {args.user}')
            if channel == 'telegram':
                from core.brief.telegram_delivery import (
                    render_brief_for_telegram,
                )
                tg_body = render_brief_for_telegram(
                    display_name=profile.display_name,
                    generated_at=question_set.generated_at,
                    questions=question_set.questions,
                )
                print(tg_body)
            else:
                print(f'Subject: {email.subject}')
                print()
                print(email.body)
            print('--- end ---')
            _insert_run(
                conn, args.user, question_set.questions,
                email.subject, email.body,
                dry_run=True, delivery_status='dry_run', error=None,
                delivered_via=channel,
            )
            return 0

        # Real send — branch on channel
        if channel == 'telegram':
            from core.brief.telegram_delivery import (
                render_brief_for_telegram, send_brief_via_telegram,
            )
            tg_body = render_brief_for_telegram(
                display_name=profile.display_name,
                generated_at=question_set.generated_at,
                questions=question_set.questions,
            )
            result = send_brief_via_telegram(
                conn, user_email=args.user, text=tg_body,
            )
            if not result.ok:
                log.error('telegram send failed: %s', result.error)
                _insert_run(
                    conn, args.user, question_set.questions,
                    email.subject, email.body,
                    dry_run=False, delivery_status='failed',
                    error=str(result.error), delivered_via='telegram',
                )
                return 1
            outgoing = (
                f'telegram:{result.chat_id}:{result.message_ids[0]}'
                if result.message_ids else None
            )
            run_id = _insert_run(
                conn, args.user, question_set.questions,
                email.subject, email.body,
                dry_run=False, delivery_status='sent', error=None,
                outgoing_message_id=outgoing,
                delivered_via='telegram',
            )
            log.info('telegram sent to %s (run_id=%s, %d questions, '
                     'chat_id=%s)',
                     args.user, run_id, len(question_set.questions),
                     result.chat_id)
            return 0

        # Email path (existing behaviour)
        outgoing_message_id: str | None = None
        try:
            outgoing_message_id = send_via_smtp(email, args.user)
        except SMTPNotConfigured as exc:
            log.error('SMTP not configured — recording run as failed: %s', exc)
            _insert_run(
                conn, args.user, question_set.questions,
                email.subject, email.body,
                dry_run=False, delivery_status='failed', error=str(exc),
                delivered_via='email',
            )
            return 1
        except Exception as exc:
            log.error('send failed: %s', exc)
            _insert_run(
                conn, args.user, question_set.questions,
                email.subject, email.body,
                dry_run=False, delivery_status='failed', error=str(exc),
                delivered_via='email',
            )
            return 1

        run_id = _insert_run(
            conn, args.user, question_set.questions,
            email.subject, email.body,
            dry_run=False, delivery_status='sent', error=None,
            outgoing_message_id=outgoing_message_id,
            delivered_via='email',
        )
        log.info('sent to %s (run_id=%s, %d questions)',
                 args.user, run_id, len(question_set.questions))
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
