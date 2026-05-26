from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import APPS, APPS_BY_ID, CAPPS_DIR, EXTERNAL_MSG, NOT_IMPLEMENTED_MSG
from app.launcher import launch_app
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
    from app.polling import _health_probe_url

    launch_exists = (
        not app_def.external
        and app_def.launch_script
        and (app_def.app_dir / app_def.launch_script).is_file()
    )
    remote = app_def.control == "remote" and app_def.shutdown_path
    stub_controls = app_def.control == "stub" or app_def.external
    return {
        "id": app_def.id,
        "name": app_def.name,
        "description": app_def.description,
        "port": app_def.port,
        "url": f"http://127.0.0.1:{app_def.port}/",
        "health_check_url": _health_probe_url(app_def),
        "external": app_def.external,
        "launch_available": launch_exists,
        "stop_available": bool(remote),
        "restart_available": bool(remote),
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


async def _refresh_app(app_def) -> dict:
    fields = _app_status_fields(app_def)
    outcome = await wait_for_condition(
        app_def, fields, goal_running=None, action="refresh"
    )
    return outcome_response(app_def.id, "refresh", outcome)


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

    if app_def.external:
        return _external_response(app_id, "start")

    fields = _app_status_fields(app_def)
    initial = await probe_app(app_def, fields)
    if initial["running"]:
        return {
            "id": app_id,
            "action": "start",
            "success": True,
            "running": True,
            "message": "Already running",
            "elapsed_seconds": 0,
            "app": initial,
        }

    if not initial["launch_available"]:
        raise HTTPException(
            status_code=500,
            detail=f"Launch script missing: {app_def.app_dir / app_def.launch_script}",
        )

    try:
        launch_app(app_def)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    outcome = await wait_for_condition(app_def, fields, goal_running=True, action="start")
    return outcome_response(app_id, "start", outcome)


@app.post("/api/apps/{app_id}/stop")
async def stop_app(app_id: str):
    app_def = APPS_BY_ID.get(app_id)
    if not app_def:
        raise HTTPException(status_code=404, detail="Unknown app")

    if app_def.external or app_def.control == "stub":
        return {
            "id": app_id,
            "action": "stop",
            "success": False,
            "not_implemented": True,
            "message": EXTERNAL_MSG if app_def.external else NOT_IMPLEMENTED_MSG,
        }

    fields = _app_status_fields(app_def)
    initial = await probe_app(app_def, fields)
    if not initial["running"]:
        return {
            "id": app_id,
            "action": "stop",
            "success": True,
            "running": False,
            "message": "Already stopped",
            "elapsed_seconds": 0,
            "app": initial,
        }

    await _remote_shutdown(app_def)
    outcome = await wait_for_condition(app_def, fields, goal_running=False, action="stop")
    return outcome_response(app_id, "stop", outcome)


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
        await _remote_shutdown(app_def)
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

    launch_app(app_def)
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
