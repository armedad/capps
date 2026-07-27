"""Loopback-only process exit for local dev / start.bat launcher."""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

router = APIRouter(tags=["local"])


def _require_loopback(request: Request) -> None:
    client = request.client
    host = (client.host if client else "") or ""
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Local shutdown accepts loopback only.")


def _exit_process() -> None:
    """Exit this process only; start.bat continues and returns to its caller."""
    time.sleep(0.2)
    os._exit(0)


@router.post("/local/shutdown")
async def post_local_shutdown(request: Request, background_tasks: BackgroundTasks) -> dict[str, bool]:
    """End the capps server process. Loopback only; response is sent before exit."""
    _require_loopback(request)
    background_tasks.add_task(_exit_process)
    return {"ok": True, "shutting_down": True}
