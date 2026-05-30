from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    APPS,
    APPS_BY_ID,
    CAPPS_DIR,
    EXTERNAL_MSG,
    NOT_IMPLEMENTED_MSG,
    app_service_url,
    health_check_label,
)
from app.launcher import launch_app, run_script
from app.polling import (
    POLL_TIMEOUT_SEC,
    outcome_response,
    probe_app,
    wait_for_condition,
)

STATIC_DIR = CAPPS_DIR / "static"
HEALTH_TIMEOUT = 2.0

app = FastAPI(title="c-apps", description="Local apps dashboard")
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
    return {
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
    outcome = await wait_for_condition(
        app_def, fields, goal_running=None, action="refresh"
    )
    return outcome_response(app_def.id, "refresh", outcome)


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
    return {"apps": [_app_status_fields(a) for a in APPS]}


@app.get("/api/apps")
async def list_apps():
    results = await asyncio.gather(*(_refresh_app(a) for a in APPS))
    return {"apps": [r["app"] for r in results], "results": results}


@app.get("/api/apps/{app_id}")
async def get_app(app_id: str):
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")
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
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")
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
    results: list[dict] = []
    for app_def in APPS:
        try:
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


@app.post("/api/apps/{app_id}/stop")
async def stop_app(app_id: str):
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")
    return await _perform_stop(app_def)


@app.post("/api/apps/stop-all")
async def stop_all_apps():
    """Stop every manageable app that is currently running."""
    results: list[dict] = []
    for app_def in APPS:
        try:
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
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")

    if app_def.external or app_def.control == "stub":
        return {
            "id": app_id,
            "action": "restart",
            "success": False,
            "not_implemented": True,
            "message": EXTERNAL_MSG if app_def.external else NOT_IMPLEMENTED_MSG,
        }

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
