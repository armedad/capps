"""Unified REST control for managed apps (start / stop / restart)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["apps"])


class AppControlRequest(BaseModel):
    app_id: str = Field(..., min_length=1, description="App id from apps.json")
    action: Literal["start", "stop", "restart"]


@router.post("/apps/control")
async def control_app(body: AppControlRequest) -> dict:
    """Start, stop, or restart one managed app. Same behavior as the per-action routes."""
    from app.main import dispatch_app_action

    return await dispatch_app_action(body.app_id, body.action)
