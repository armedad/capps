"""Temporary failover watchdog schedule overrides (loopback only)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.config import FAILOVER_GROUPS
from app.watchdog import watchdog

router = APIRouter(tags=["failover"])


def _require_loopback(request: Request) -> None:
    client = request.client
    host = (client.host if client else "") or ""
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Failover schedule accepts loopback only.")


class FailoverScheduleRequest(BaseModel):
    group_id: str | None = None
    interval_sec: float | None = Field(default=None, gt=0)
    duration_sec: float | None = Field(default=None, gt=0)
    every_minutes: float | None = Field(default=None, gt=0)
    for_hours: float | None = Field(default=None, gt=0)
    for_minutes: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _resolve_interval_and_duration(self) -> FailoverScheduleRequest:
        interval_sources = sum(
            x is not None for x in (self.interval_sec, self.every_minutes)
        )
        duration_sources = sum(
            x is not None for x in (self.duration_sec, self.for_hours, self.for_minutes)
        )
        if interval_sources != 1:
            raise ValueError(
                "Provide exactly one of interval_sec or every_minutes"
            )
        if duration_sources != 1:
            raise ValueError(
                "Provide exactly one of duration_sec, for_hours, or for_minutes"
            )
        return self

    def resolved_interval_sec(self) -> float:
        if self.interval_sec is not None:
            return self.interval_sec
        assert self.every_minutes is not None
        return self.every_minutes * 60

    def resolved_duration_sec(self) -> float:
        if self.duration_sec is not None:
            return self.duration_sec
        if self.for_hours is not None:
            return self.for_hours * 3600
        assert self.for_minutes is not None
        return self.for_minutes * 60


@router.get("/failover/schedule")
async def get_failover_schedule() -> dict:
    return {"groups": watchdog.all_schedule_status()}


@router.post("/failover/schedule")
async def post_failover_schedule(
    request: Request, body: FailoverScheduleRequest
) -> dict:
    _require_loopback(request)
    if not FAILOVER_GROUPS:
        raise HTTPException(status_code=404, detail="No failover groups configured")

    if body.group_id and body.group_id not in {g.id for g in FAILOVER_GROUPS}:
        raise HTTPException(status_code=404, detail=f"Unknown failover group {body.group_id!r}")

    try:
        groups = watchdog.schedule_override(
            interval_sec=body.resolved_interval_sec(),
            duration_sec=body.resolved_duration_sec(),
            group_id=body.group_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "interval_sec": body.resolved_interval_sec(),
        "duration_sec": body.resolved_duration_sec(),
        "groups": groups,
    }


@router.delete("/failover/schedule")
async def delete_failover_schedule(
    request: Request, group_id: str | None = None
) -> dict:
    _require_loopback(request)
    if group_id and group_id not in {g.id for g in FAILOVER_GROUPS}:
        raise HTTPException(status_code=404, detail=f"Unknown failover group {group_id!r}")

    try:
        groups = watchdog.clear_override(group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"ok": True, "groups": groups}
