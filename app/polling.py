from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from app.config import AppDef

POLL_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 1.0
HEALTH_TIMEOUT = 2.0


@dataclass
class PollOutcome:
    success: bool
    running: bool
    message: str
    elapsed_seconds: float
    status: dict


def _health_probe_url(app_def: AppDef) -> str:
    if app_def.health_url:
        return app_def.health_url
    return f"http://127.0.0.1:{app_def.port}{app_def.health_path}"


async def _probe_raw(app_def: AppDef) -> tuple[bool | None, dict]:
    """Return (running, fields). running=None means could not reach the health endpoint."""
    url = _health_probe_url(app_def)
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            resp = await client.get(url)
            running = resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return None, {"reachable": False, "running": False}

    return running, {"reachable": True, "running": running}


async def probe_app(app_def: AppDef, status_fields: dict) -> dict:
    running, _extra = await _probe_raw(app_def)
    if running is None:
        running = False
    return {**status_fields, "running": running}


async def wait_for_condition(
    app_def: AppDef,
    status_fields: dict,
    *,
    goal_running: bool | None,
    action: str,
    timeout_sec: float | None = None,
    probe_fn: Callable[[AppDef], Awaitable[dict]] | None = None,
) -> PollOutcome:
    """
    Poll health until goal is met or timeout.

    goal_running: True = wait until running, False = wait until stopped, None = refresh (report state).
    """
    probe = probe_fn or (lambda a: probe_app(a, status_fields))
    limit = timeout_sec if timeout_sec is not None else POLL_TIMEOUT_SEC
    start = time.monotonic()
    last_status: dict | None = None
    last_running: bool | None = None

    while True:
        elapsed = time.monotonic() - start
        last_status = await probe(app_def)
        last_running = last_status["running"]

        if goal_running is None:
            label = "Running" if last_running else "Stopped"
            return PollOutcome(
                success=True,
                running=last_running,
                message=f"{label} (checked in {elapsed:.1f}s)",
                elapsed_seconds=elapsed,
                status=last_status,
            )

        if last_running == goal_running:
            verb = "running" if goal_running else "stopped"
            return PollOutcome(
                success=True,
                running=last_running,
                message=f"App is {verb} (confirmed in {elapsed:.1f}s)",
                elapsed_seconds=elapsed,
                status=last_status,
            )

        if elapsed >= limit:
            break

        await asyncio.sleep(POLL_INTERVAL_SEC)

    assert last_status is not None and last_running is not None
    if goal_running:
        msg = f"Still not running after {limit:.0f}s"
    else:
        msg = f"Still running after {limit:.0f}s"
    return PollOutcome(
        success=False,
        running=last_running,
        message=msg,
        elapsed_seconds=elapsed,
        status=last_status,
    )


def outcome_response(app_id: str, action: str, outcome: PollOutcome, **extra) -> dict:
    return {
        "id": app_id,
        "action": action,
        "success": outcome.success,
        "running": outcome.running,
        "message": outcome.message,
        "elapsed_seconds": round(outcome.elapsed_seconds, 1),
        "app": outcome.status,
        **extra,
    }
