"""Module reachability probe.

On startup and every 60s thereafter, probe each module's health endpoint.
Keeps an in-process cache of reachability state and exposes it to the
identity assembler.

Does not crash on unreachable modules — records the error string for
inclusion in the system prompt so Deek knows to avoid claiming data it
can't verify.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from core.identity import assembler

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 2.0
PROBE_INTERVAL_SECONDS = 60.0


@dataclass
class ProbeResult:
    reachable: bool = False
    last_checked: datetime | None = None
    last_error: str | None = None
    latency_ms: int | None = None


_state: dict[str, ProbeResult] = {}
_last_probe_at: datetime | None = None
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


async def _probe_one(client: httpx.AsyncClient, module: assembler.ModuleSpec) -> ProbeResult:
    start = time.perf_counter()
    try:
        r = await client.get(module.health_url, timeout=PROBE_TIMEOUT_SECONDS)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if r.status_code < 500:  # 2xx/3xx/4xx all count as "service reachable"
            return ProbeResult(
                reachable=True,
                last_checked=datetime.now(timezone.utc),
                last_error=None,
                latency_ms=latency_ms,
            )
        return ProbeResult(
            reachable=False,
            last_checked=datetime.now(timezone.utc),
            last_error=f'HTTP {r.status_code}',
            latency_ms=latency_ms,
        )
    except httpx.TimeoutException:
        return ProbeResult(
            reachable=False,
            last_checked=datetime.now(timezone.utc),
            last_error='timeout',
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
    except Exception as exc:
        msg = type(exc).__name__
        # Try to surface the target host so operators know what's failing.
        detail = str(exc).split('\n', 1)[0][:100]
        if detail:
            msg = f'{msg}: {detail}'
        return ProbeResult(
            reachable=False,
            last_checked=datetime.now(timezone.utc),
            last_error=msg,
            latency_ms=None,
        )


async def probe_once() -> dict[str, ProbeResult]:
    """Probe every module in parallel. Updates the in-process cache."""
    global _last_probe_at
    modules = assembler.get_modules()
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_probe_one(client, m) for m in modules),
            return_exceptions=False,
        )
    async with _lock:
        for m, r in zip(modules, results):
            _state[m.name] = r
        _last_probe_at = datetime.now(timezone.utc)
    return dict(_state)


def get_reachable_modules() -> set[str]:
    return {name for name, r in _state.items() if r.reachable}


def get_errors() -> dict[str, str]:
    return {name: (r.last_error or 'unknown') for name, r in _state.items() if not r.reachable}


def get_probe_status() -> dict:
    return {
        'last_probe': _last_probe_at.isoformat() if _last_probe_at else None,
        'modules': {
            name: {
                'reachable': r.reachable,
                'last_checked': r.last_checked.isoformat() if r.last_checked else None,
                'last_error': r.last_error,
                'latency_ms': r.latency_ms,
            }
            for name, r in _state.items()
        },
    }


async def _loop():
    """Background task — rolling probe at PROBE_INTERVAL_SECONDS."""
    while True:
        try:
            await asyncio.sleep(PROBE_INTERVAL_SECONDS)
            await probe_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[IDENTITY.probe] rolling probe failed: %s', exc)


async def start(run_initial: bool = True) -> None:
    """Run initial probe and spawn the rolling background task.

    Safe to call multiple times — no-op if already started.
    """
    global _task
    if _task is not None and not _task.done():
        return
    if run_initial:
        await probe_once()
    _task = asyncio.create_task(_loop(), name='deek-identity-probe')


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
