"""Telegram long-polling driver.

Companion to the webhook handler at ``api/routes/telegram.py`` —
routes Telegram updates through the SAME ``_route_update``
dispatcher, just sourced via long-polling rather than HTTPS push.

Used by deployments that have no public ingress (Jo's Pip on
nbne1 — Tailscale-only, no port forward, no Cloudflare Tunnel).
The trade vs webhook: ~5s additional latency on inbound messages
in exchange for outbound-only network model.

The bot must NOT have a webhook registered when polling is in
use — Telegram returns 409 Conflict on getUpdates if a webhook
is set. The polling driver clears any registered webhook on
startup as a precaution.

Run via ``scripts/run_telegram_poller.py``.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx


logger = logging.getLogger(__name__)


TELEGRAM_API_BASE = 'https://api.telegram.org'
DEFAULT_LONG_POLL_TIMEOUT = 25  # seconds; Telegram supports up to 50
HTTP_CLIENT_TIMEOUT = 35.0      # must exceed long-poll timeout
ALLOWED_UPDATES = ['message', 'edited_message']


def _bot_token() -> str:
    return (os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()


async def clear_webhook(client: httpx.AsyncClient, token: str) -> bool:
    """Telegram won't return updates while a webhook is set. Clear
    any existing webhook on startup. Idempotent."""
    try:
        r = await client.post(
            f'{TELEGRAM_API_BASE}/bot{token}/deleteWebhook',
            json={'drop_pending_updates': False},
        )
        if r.status_code == 200:
            data = r.json() or {}
            return bool(data.get('ok'))
    except Exception as exc:
        logger.warning('[telegram-poll] clear webhook failed: %s', exc)
    return False


async def fetch_updates(
    client: httpx.AsyncClient, token: str,
    offset: int, timeout: int,
) -> list[dict]:
    """Long-poll getUpdates. Returns the list (possibly empty)."""
    try:
        r = await client.post(
            f'{TELEGRAM_API_BASE}/bot{token}/getUpdates',
            json={
                'offset': offset,
                'timeout': timeout,
                'allowed_updates': ALLOWED_UPDATES,
            },
            timeout=HTTP_CLIENT_TIMEOUT,
        )
    except httpx.ReadTimeout:
        return []
    except Exception as exc:
        logger.warning('[telegram-poll] fetch failed: %s', exc)
        await asyncio.sleep(5)
        return []

    if r.status_code != 200:
        logger.warning(
            '[telegram-poll] getUpdates HTTP %d: %s',
            r.status_code, r.text[:200],
        )
        # 409 = webhook still set despite our clear attempt; back off
        if r.status_code == 409:
            await asyncio.sleep(10)
        return []
    try:
        data = r.json() or {}
    except Exception:
        return []
    if not data.get('ok'):
        logger.warning(
            '[telegram-poll] api not ok: %s', data.get('description', '?'),
        )
        return []
    return data.get('result') or []


async def run_poll_loop(
    *,
    long_poll_timeout: int = DEFAULT_LONG_POLL_TIMEOUT,
) -> None:
    """Main loop. Runs forever until cancelled. Routes each update
    through the same dispatch path the webhook uses, so all
    slash-command + brief-reply + chat-routing behaviour is
    identical between webhook and polling deployments."""
    token = _bot_token()
    if not token:
        logger.error(
            '[telegram-poll] TELEGRAM_BOT_TOKEN not set — exiting',
        )
        return

    # Lazy import — _dispatch_update_async lives in api.routes.telegram
    # and brings the FastAPI machinery with it; we want this module
    # importable from a thin script without paying that cost on import.
    from api.routes.telegram import _dispatch_update_async

    offset = 0  # Telegram's update_id offset; bumped after each batch

    async with httpx.AsyncClient(timeout=HTTP_CLIENT_TIMEOUT) as client:
        cleared = await clear_webhook(client, token)
        if cleared:
            logger.info('[telegram-poll] cleared any existing webhook')

        logger.info(
            '[telegram-poll] entering poll loop (long_poll_timeout=%ds)',
            long_poll_timeout,
        )

        while True:
            updates = await fetch_updates(
                client, token, offset, long_poll_timeout,
            )
            for update in updates:
                update_id = int(update.get('update_id') or 0)
                if update_id >= offset:
                    offset = update_id + 1
                # Schedule each update on its own task so a slow
                # chat agent call doesn't block the next poll cycle
                asyncio.create_task(_dispatch_update_async(update))
            # No sleep needed — long-poll already provides backoff


__all__ = [
    'run_poll_loop',
    'clear_webhook',
    'fetch_updates',
]
