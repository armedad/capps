from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    APPS,
    APPS_BY_ID,
    CAPPS_DIR,
    DASHBOARD_APPS,
    EXTERNAL_MSG,
    FAILOVER_BY_PRIMARY_ID,
    NOT_IMPLEMENTED_MSG,
    app_service_url,
    health_check_label,
)
from app.failover_actions import (
    failover_group_for_app,
    perform_failover_restart,
    perform_failover_start,
    perform_failover_stop,
    primary_app_id,
    refresh_failover_app,
)
from app.launcher import launch_app, run_script
from app.polling import (
    POLL_TIMEOUT_SEC,
    outcome_response,
    probe_app,
    wait_for_condition,
)
from app.watchdog import watchdog
from app.routers.local_shutdown import router as local_shutdown_router
from app.routers.failover import router as failover_router

STATIC_DIR = CAPPS_DIR / "static"
HEALTH_TIMEOUT = 2.0
logger = logging.getLogger(__name__)


def _startup_mode_enabled() -> bool:
    return os.environ.get("CAPPS_STARTUP", "").lower() in ("1", "true", "yes")


async def ensure_all_apps_running() -> dict:
    """Start every manageable app that is not already running (no restarts)."""
    results: list[dict] = []
    for app_def in DASHBOARD_APPS:
        if not app_def.autostart:
            continue
        try:
            if failover_group_for_app(app_def.id):
                results.append(await perform_failover_start(app_def))
            else:
                results.append(await _perform_start(app_def))
        except HTTPException as exc:
            fields = _app_status_fields(app_def)
            results.append(
                {
                    "id": app_def.id,
                    "action": "start",
                    "success": False,
                    "message": str(exc.detail),
                    "app": await probe_app(app_def, fields),
                }
            )
    return {
        "action": "start-all",
        "apps": [r["app"] for r in results],
        "results": results,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if _startup_mode_enabled():
        logger.info("Startup mode: ensuring managed apps are running")
        outcome = await ensure_all_apps_running()
        for result in outcome["results"]:
            app_id = result["id"]
            if result.get("skipped") and result.get("success"):
                logger.info("%s: %s", app_id, result.get("message", "skipped"))
            elif result.get("success"):
                logger.info("%s: started", app_id)
            else:
                logger.warning(
                    "%s: failed — %s",
                    app_id,
                    result.get("message", "unknown error"),
                )
    await watchdog.start()
    yield
    await watchdog.stop()


app = FastAPI(title="c-apps", description="Local apps dashboard", lifespan=lifespan)
app.include_router(local_shutdown_router, prefix="/api")
app.include_router(failover_router, prefix="/api")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _app_status_fields(app_def) -> dict:
    launch_exists = (
        not app_def.external
        and app_def.launch_script
        and (app_def.app_dir / app_def.launch_script).is_file()
    )
    start_debug_exists = False
    if app_def.start_debug and not app_def.external:
        sd_script = app_def.start_debug.launch_script or app_def.launch_script
        start_debug_exists = bool(sd_script) and (app_def.app_dir / sd_script).is_file()
    remote_stop = app_def.control == "remote" and bool(app_def.shutdown_path)
    script_stop = app_def.control == "script" and bool(app_def.stop_script)
    stop_available = remote_stop or script_stop
    stub_controls = app_def.control == "stub" or app_def.external
    fields = {
        "id": app_def.id,
        "name": app_def.name,
        "description": app_def.description,
        "port": app_def.port,
        "health_probe": app_def.health_probe,
        "url": app_service_url(app_def),
        "health_check_url": health_check_label(app_def),
        "external": app_def.external,
        "launch_available": launch_exists,
        "start_debug_available": start_debug_exists,
        "stop_available": stop_available,
        "restart_available": stop_available,
        "stop_stub": stub_controls,
    }
    if app_def.id in FAILOVER_BY_PRIMARY_ID:
        fields["failover_managed"] = True
    return fields


async def _remote_shutdown(app_def) -> None:
    if not app_def.shutdown_path:
        raise HTTPException(status_code=500, detail="No shutdown path configured")
    url = f"http://127.0.0.1:{app_def.port}{app_def.shutdown_path}"
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT) as client:
            resp = await client.post(url)
            if resp.status_code >= 400:
                detail = resp.text[:200] or resp.reason_phrase
                raise HTTPException(status_code=502, detail=f"Shutdown failed: {detail}")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="App is not running") from None
    except httpx.TimeoutException:
        pass  # process may exit before response completes


async def _script_shutdown(app_def) -> None:
    if not app_def.stop_script:
        raise HTTPException(status_code=500, detail="No stop_script configured")
    try:
        code = await asyncio.to_thread(run_script, app_def, app_def.stop_script)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if code != 0:
        raise HTTPException(
            status_code=502,
            detail=f"Stop script exited with code {code}",
        )


async def _shutdown_app(app_def) -> None:
    if app_def.control == "script":
        await _script_shutdown(app_def)
        return
    await _remote_shutdown(app_def)


async def _refresh_app(app_def) -> dict:
    fields = _app_status_fields(app_def)
    if failover_group_for_app(app_def.id):
        return await refresh_failover_app(app_def, fields)
    outcome = await wait_for_condition(
        app_def, fields, goal_running=None, action="refresh"
    )
    return outcome_response(app_def.id, "refresh", outcome)


def _resolve_api_app(app_id: str):
    app_id = primary_app_id(app_id)
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")
    return app_def


def _can_manage_start(app_def, fields: dict) -> bool:
    return not app_def.external and bool(fields["launch_available"])


def _can_manage_stop(app_def, fields: dict) -> bool:
    return bool(fields["stop_available"])


async def _launch_app_process(app_def, *, debug: bool = False) -> None:
    if debug:
        launch_app(app_def, debug=True)
        return
    if app_def.control == "script":
        code = await asyncio.to_thread(
            run_script, app_def, app_def.launch_script, app_def.launch_args
        )
        if code != 0:
            raise HTTPException(
                status_code=502,
                detail=f"Launch script exited with code {code}",
            )
    else:
        launch_app(app_def)


async def _perform_start(app_def, fields: dict | None = None) -> dict:
    fields = fields or _app_status_fields(app_def)
    initial = await probe_app(app_def, fields)
    if initial["running"]:
        return {
            "id": app_def.id,
            "action": "start",
            "success": True,
            "skipped": True,
            "running": True,
            "message": "Already running",
            "elapsed_seconds": 0,
            "app": initial,
        }

    if not _can_manage_start(app_def, fields):
        return {
            "id": app_def.id,
            "action": "start",
            "success": False,
            "skipped": True,
            "not_implemented": app_def.external,
            "message": EXTERNAL_MSG if app_def.external else NOT_IMPLEMENTED_MSG,
            "running": initial["running"],
            "app": initial,
        }

    if not initial["launch_available"]:
        raise HTTPException(
            status_code=500,
            detail=f"Launch script missing: {app_def.app_dir / app_def.launch_script}",
        )

    try:
        await _launch_app_process(app_def)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    outcome = await wait_for_condition(app_def, fields, goal_running=True, action="start")
    result = outcome_response(app_def.id, "start", outcome)
    result["skipped"] = False
    return result


async def _perform_start_debug(app_def, fields: dict | None = None) -> dict:
    fields = fields or _app_status_fields(app_def)
    initial = await probe_app(app_def, fields)
    if initial["running"]:
        return {
            "id": app_def.id,
            "action": "start-debug",
            "success": True,
            "skipped": True,
            "running": True,
            "message": "Already running",
            "elapsed_seconds": 0,
            "app": initial,
        }

    if app_def.external or not app_def.start_debug:
        return {
            "id": app_def.id,
            "action": "start-debug",
            "success": False,
            "skipped": True,
            "not_implemented": True,
            "message": EXTERNAL_MSG if app_def.external else NOT_IMPLEMENTED_MSG,
            "running": initial["running"],
            "app": initial,
        }

    if not initial.get("start_debug_available"):
        sd_script = app_def.start_debug.launch_script or app_def.launch_script
        raise HTTPException(
            status_code=500,
            detail=f"Start debug script missing: {app_def.app_dir / sd_script}",
        )

    try:
        await _launch_app_process(app_def, debug=True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    outcome = await wait_for_condition(
        app_def, fields, goal_running=True, action="start-debug"
    )
    result = outcome_response(app_def.id, "start-debug", outcome)
    result["skipped"] = False
    return result


async def _perform_stop(app_def, fields: dict | None = None) -> dict:
    fields = fields or _app_status_fields(app_def)
    initial = await probe_app(app_def, fields)
    if not initial["running"]:
        return {
            "id": app_def.id,
            "action": "stop",
            "success": True,
            "skipped": True,
            "running": False,
            "message": "Already stopped",
            "elapsed_seconds": 0,
            "app": initial,
        }

    if not _can_manage_stop(app_def, fields):
        return {
            "id": app_def.id,
            "action": "stop",
            "success": False,
            "skipped": True,
            "not_implemented": True,
            "message": EXTERNAL_MSG if app_def.external else NOT_IMPLEMENTED_MSG,
            "running": initial["running"],
            "app": initial,
        }

    await _shutdown_app(app_def)
    outcome = await wait_for_condition(app_def, fields, goal_running=False, action="stop")
    result = outcome_response(app_def.id, "stop", outcome)
    result["skipped"] = False
    return result


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/apps/catalog")
async def catalog_apps():
    """App list without health probes (for initial dashboard layout)."""
    return {"apps": [_app_status_fields(a) for a in DASHBOARD_APPS]}


@app.get("/api/apps")
async def list_apps():
    results = await asyncio.gather(*(_refresh_app(a) for a in DASHBOARD_APPS))
    return {"apps": [r["app"] for r in results], "results": results}


@app.get("/api/apps/{app_id}")
async def get_app(app_id: str):
    app_def = _resolve_api_app(app_id)
    return await _refresh_app(app_def)


def _external_response(app_id: str, action: str) -> dict:
    return {
        "id": app_id,
        "action": action,
        "success": False,
        "not_implemented": True,
        "message": EXTERNAL_MSG,
    }


@app.post("/api/apps/{app_id}/start")
async def start_app(app_id: str):
    app_def = _resolve_api_app(app_id)
    if failover_group_for_app(app_def.id):
        return await perform_failover_start(app_def)
    return await _perform_start(app_def)


@app.post("/api/apps/{app_id}/start-debug")
async def start_debug_app(app_id: str):
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")
    return await _perform_start_debug(app_def)


@app.post("/api/apps/start-all")
async def start_all_apps():
    """Start every manageable app that is not already running (no restarts)."""
    return await ensure_all_apps_running()


@app.post("/api/apps/{app_id}/stop")
async def stop_app(app_id: str):
    app_def = _resolve_api_app(app_id)
    if failover_group_for_app(app_def.id):
        return await perform_failover_stop(app_def)
    return await _perform_stop(app_def)


@app.post("/api/apps/stop-all")
async def stop_all_apps():
    """Stop every manageable app that is currently running."""
    results: list[dict] = []
    for app_def in DASHBOARD_APPS:
        try:
            if failover_group_for_app(app_def.id):
                results.append(await perform_failover_stop(app_def))
            else:
                results.append(await _perform_stop(app_def))
        except HTTPException as exc:
            fields = _app_status_fields(app_def)
            results.append(
                {
                    "id": app_def.id,
                    "action": "stop",
                    "success": False,
                    "message": str(exc.detail),
                    "app": await probe_app(app_def, fields),
                }
            )
    return {
        "action": "stop-all",
        "apps": [r["app"] for r in results],
        "results": results,
    }


@app.post("/api/apps/{app_id}/restart")
async def restart_app(app_id: str):
    app_def = _resolve_api_app(app_id)

    if app_def.external or app_def.control == "stub":
        return {
            "id": app_id,
            "action": "restart",
            "success": False,
            "not_implemented": True,
            "message": EXTERNAL_MSG if app_def.external else NOT_IMPLEMENTED_MSG,
        }

    if failover_group_for_app(app_def.id):
        return await perform_failover_restart(app_def)

    fields = _app_status_fields(app_def)
    initial = await probe_app(app_def, fields)
    deadline = time.monotonic() + POLL_TIMEOUT_SEC

    if initial["running"]:
        await _shutdown_app(app_def)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return outcome_response(
                app_id,
                "restart",
                await wait_for_condition(
                    app_def, fields, goal_running=False, action="restart", timeout_sec=0.1
                ),
                phase="stop",
            )
        stop_outcome = await wait_for_condition(
            app_def,
            fields,
            goal_running=False,
            action="restart",
            timeout_sec=remaining,
        )
        if not stop_outcome.success:
            return outcome_response(app_id, "restart", stop_outcome, phase="stop")

    if not (app_def.app_dir / app_def.launch_script).is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Launch script missing: {app_def.app_dir / app_def.launch_script}",
        )

    await _launch_app_process(app_def)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return outcome_response(
            app_id,
            "restart",
            await wait_for_condition(
                app_def, fields, goal_running=True, action="restart", timeout_sec=0.1
            ),
            phase="start",
        )
    start_outcome = await wait_for_condition(
        app_def,
        fields,
        goal_running=True,
        action="restart",
        timeout_sec=remaining,
    )
    return outcome_response(app_id, "restart", start_outcome, phase="start")
