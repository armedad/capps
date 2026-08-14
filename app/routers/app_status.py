"""Unified REST status query for managed apps."""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(tags=["apps"])


@router.get("/apps/status")
async def query_app_status(
    app_id: str | None = Query(
        default=None,
        description="App id from apps.json; omit to refresh all dashboard apps",
    ),
) -> dict:
    """Health/status for one app or all dashboard apps (same probes as Refresh)."""
    from app.main import dispatch_app_status

    return await dispatch_app_status(app_id)
