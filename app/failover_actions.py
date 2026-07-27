"""Failover-aware start/stop/status for primary/backup app pairs."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

from app.config import (
    APPS_BY_ID,
    AppDef,
    FailoverGroup,
    FAILOVER_BY_PRIMARY_ID,
    health_check_label,
)
from app.polling import HEALTH_TIMEOUT, probe_app
from app.watchdog import watchdog

_BOT_MATCH = r"python(?:3\d*)?\.exe\"?\s+-m\s+cursor_chat\.telegram_bot"


async def _find_telegram_bot_pid() -> int | None:
    if sys.platform == "win32":
        ps = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "($_.Name -eq 'python.exe' -or $_.Name -like 'python3*.exe') "
            f"-and $_.CommandLine -match '{_BOT_MATCH}' "
            "} | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-Command",
            ps,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-f",
            "cursor_chat.telegram_bot",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0 or not stdout.strip():
        return None
    line = stdout.decode().strip().splitlines()[0].strip()
    return int(line) if line.isdigit() else None


def _read_pid_file(app_dir: Path) -> int | None:
    pid_file = app_dir / ".telegram_bot.pid"
    if not pid_file.is_file():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


async def _service_health_up(app_def: AppDef) -> bool:
    url = health_check_label(app_def)
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return False


async def resolve_active_instance(group: FailoverGroup) -> str | None:
    """Return 'primary', 'backup', or None when the service is not running."""
    primary = APPS_BY_ID[group.primary_id]
    backup = APPS_BY_ID[group.backup_id]

    if not await _service_health_up(primary):
        return None

    bot_pid = await _find_telegram_bot_pid()
    if bot_pid is not None:
        for role, app in (("primary", primary), ("backup", backup)):
            stored = _read_pid_file(app.app_dir)
            if stored == bot_pid:
                return role

    mode = watchdog.runtimes[group.id].state.mode
    if mode in ("primary", "backup"):
        return mode
    return "primary"


def failover_group_for_app(app_id: str) -> FailoverGroup | None:
    return FAILOVER_BY_PRIMARY_ID.get(app_id)


def primary_app_id(app_id: str) -> str:
    group = FAILOVER_BY_PRIMARY_ID.get(app_id)
    if group:
        return group.primary_id
    for g in FAILOVER_BY_PRIMARY_ID.values():
        if g.backup_id == app_id:
            return g.primary_id
    return app_id


async def enrich_failover_status(app_def: AppDef, fields: dict) -> dict:
    group = failover_group_for_app(app_def.id)
    if group is None:
        return fields

    active = await resolve_active_instance(group)
    service_up = active is not None
    runtime = watchdog.runtimes[group.id]

    enriched = {
        **fields,
        "running": service_up,
        "failover": {
            "group_id": group.id,
            "active_instance": active,
            "watchdog_mode": runtime.state.mode,
        },
    }
    if service_up and active:
        enriched["failover_status"] = f"Running ({active})"
    elif service_up:
        enriched["failover_status"] = "Running"
    else:
        enriched["failover_status"] = "Stopped"
    return enriched


async def refresh_failover_app(app_def: AppDef, fields: dict) -> dict:
    from app.polling import PollOutcome, outcome_response

    enriched = await enrich_failover_status(app_def, fields)
    running = bool(enriched.get("running"))
    label = enriched.get("failover_status", "Running" if running else "Stopped")
    outcome = PollOutcome(
        success=True,
        running=running,
        message=f"{label} (checked)",
        elapsed_seconds=0,
        status=enriched,
    )
    return outcome_response(app_def.id, "refresh", outcome)


async def perform_failover_start(app_def: AppDef) -> dict:
    from app.main import _app_status_fields, _perform_start, _perform_stop

    group = failover_group_for_app(app_def.id)
    if group is None:
        return await _perform_start(app_def)

    primary = app_def
    backup = APPS_BY_ID[group.backup_id]
    primary_fields = _app_status_fields(primary)
    backup_fields = _app_status_fields(backup)

    status = await enrich_failover_status(primary, primary_fields)
    if status.get("running"):
        active = status["failover"]["active_instance"]
        return {
            "id": primary.id,
            "action": "start",
            "success": True,
            "skipped": True,
            "running": True,
            "message": f"Already running ({active})",
            "elapsed_seconds": 0,
            "app": status,
        }

    await _perform_stop(backup, backup_fields)

    primary_result = await _perform_start(primary, primary_fields)
    primary_status = await enrich_failover_status(
        primary, primary_result.get("app") or primary_fields
    )
    if primary_result.get("success") and primary_status.get("running"):
        watchdog.set_mode(group.id, "primary")
        primary_result["app"] = primary_status
        primary_result["message"] = "Started primary"
        return primary_result

    await _perform_stop(primary, primary_fields)

    backup_result = await _perform_start(backup, backup_fields)
    backup_status = await enrich_failover_status(primary, primary_fields)
    if backup_result.get("success") and backup_status.get("running"):
        watchdog.set_mode(group.id, "backup")
        backup_result["id"] = primary.id
        backup_result["app"] = backup_status
        backup_result["message"] = "Primary failed to start; started backup"
        backup_result["failover_fallback"] = True
        return backup_result

    backup_result["id"] = primary.id
    backup_result["app"] = backup_status
    backup_result["message"] = (
        f"Primary failed ({primary_result.get('message', 'unknown')}); "
        f"backup failed ({backup_result.get('message', 'unknown')})"
    )
    backup_result["success"] = False
    return backup_result


async def perform_failover_stop(app_def: AppDef) -> dict:
    from app.main import _app_status_fields, _perform_stop

    group = failover_group_for_app(app_def.id)
    if group is None:
        return await _perform_stop(app_def)

    primary = app_def
    backup = APPS_BY_ID[group.backup_id]
    primary_fields = _app_status_fields(primary)
    backup_fields = _app_status_fields(backup)

    status = await enrich_failover_status(primary, primary_fields)
    if not status.get("running"):
        watchdog.set_mode(group.id, "primary")
        return {
            "id": primary.id,
            "action": "stop",
            "success": True,
            "skipped": True,
            "running": False,
            "message": "Already stopped",
            "elapsed_seconds": 0,
            "app": status,
        }

    active = status["failover"]["active_instance"]
    if active == "backup":
        targets = [(backup, backup_fields)]
    elif active == "primary":
        targets = [(primary, primary_fields)]
    else:
        targets = [(primary, primary_fields), (backup, backup_fields)]

    result = None
    for target, target_fields in targets:
        result = await _perform_stop(target, target_fields)
        status = await enrich_failover_status(primary, primary_fields)
        if not status.get("running"):
            break

    final_status = await enrich_failover_status(primary, primary_fields)
    watchdog.set_mode(group.id, "primary")
    if result is None:
        result = {
            "id": primary.id,
            "action": "stop",
            "success": False,
            "running": final_status.get("running", False),
            "message": "Stop failed",
            "app": final_status,
        }
    else:
        result["id"] = primary.id
        result["app"] = final_status
        if result.get("success"):
            label = active or "service"
            result["message"] = f"Stopped {label}"
    return result


async def perform_failover_restart(app_def: AppDef) -> dict:
    stop_result = await perform_failover_stop(app_def)
    if not stop_result.get("success") and not stop_result.get("skipped"):
        stop_result["action"] = "restart"
        stop_result["phase"] = "stop"
        return stop_result

    start_result = await perform_failover_start(app_def)
    start_result["action"] = "restart"
    start_result["phase"] = "start"
    return start_result
