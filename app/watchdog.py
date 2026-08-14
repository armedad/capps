from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import APPS_BY_ID, FAILOVER_GROUPS, FailoverGroup
from app.polling import probe_app_verified

logger = logging.getLogger(__name__)


def _watchdog_enabled() -> bool:
    return os.environ.get("CAPPS_WATCHDOG", "1").lower() not in ("0", "false", "no")


@dataclass
class _GroupState:
    primary_failures: int = 0
    primary_successes: int = 0
    mode: str = "primary"  # "primary" | "backup"
    # Once per down episode when auto_restart is disabled.
    notified_would_restart: bool = False


@dataclass
class _ScheduleOverride:
    interval_sec: float
    expires_at_monotonic: float
    expires_at_utc: datetime

    @property
    def active(self) -> bool:
        return time.monotonic() < self.expires_at_monotonic


@dataclass
class _GroupRuntime:
    state: _GroupState = field(default_factory=_GroupState)
    last_tick: float = 0.0
    override: _ScheduleOverride | None = None


@dataclass
class FailoverWatchdog:
    groups: tuple[FailoverGroup, ...]
    runtimes: dict[str, _GroupRuntime] = field(default_factory=dict)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _wake: asyncio.Event | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for group in self.groups:
            self.runtimes[group.id] = _GroupRuntime()

    def _group(self, group_id: str) -> FailoverGroup:
        for group in self.groups:
            if group.id == group_id:
                return group
        raise KeyError(group_id)

    def _clear_expired_overrides(self) -> None:
        now = time.monotonic()
        for runtime in self.runtimes.values():
            if runtime.override and now >= runtime.override.expires_at_monotonic:
                runtime.override = None

    def effective_interval_sec(self, group_id: str) -> float:
        group = self._group(group_id)
        runtime = self.runtimes[group_id]
        override = runtime.override
        if override and override.active:
            return override.interval_sec
        return group.interval_sec

    def schedule_override(
        self,
        *,
        interval_sec: float,
        duration_sec: float,
        group_id: str | None = None,
    ) -> list[dict]:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        if duration_sec <= 0:
            raise ValueError("duration_sec must be positive")

        target_ids = [group_id] if group_id else [g.id for g in self.groups]
        if group_id and group_id not in self.runtimes:
            raise KeyError(group_id)

        expires_mono = time.monotonic() + duration_sec
        expires_utc = datetime.now(timezone.utc) + timedelta(seconds=duration_sec)
        override = _ScheduleOverride(
            interval_sec=interval_sec,
            expires_at_monotonic=expires_mono,
            expires_at_utc=expires_utc,
        )

        results: list[dict] = []
        for gid in target_ids:
            self.runtimes[gid].override = _ScheduleOverride(
                interval_sec=override.interval_sec,
                expires_at_monotonic=override.expires_at_monotonic,
                expires_at_utc=override.expires_at_utc,
            )
            results.append(self.schedule_status(gid))

        if self._wake is not None:
            self._wake.set()
        logger.info(
            "Failover schedule override: every %ss for %ss (%s)",
            interval_sec,
            duration_sec,
            group_id or "all groups",
        )
        return results

    def clear_override(self, group_id: str | None = None) -> list[dict]:
        target_ids = [group_id] if group_id else list(self.runtimes)
        if group_id and group_id not in self.runtimes:
            raise KeyError(group_id)

        for gid in target_ids:
            self.runtimes[gid].override = None

        if self._wake is not None:
            self._wake.set()
        return [self.schedule_status(gid) for gid in target_ids]

    def schedule_status(self, group_id: str) -> dict:
        group = self._group(group_id)
        runtime = self.runtimes[group_id]
        override = runtime.override if runtime.override and runtime.override.active else None
        return {
            "group_id": group_id,
            "default_interval_sec": group.interval_sec,
            "effective_interval_sec": self.effective_interval_sec(group_id),
            "auto_restart": group.auto_restart,
            "override": None
            if override is None
            else {
                "interval_sec": override.interval_sec,
                "expires_at": override.expires_at_utc.isoformat(),
            },
            "mode": runtime.state.mode,
        }

    def all_schedule_status(self) -> list[dict]:
        self._clear_expired_overrides()
        return [self.schedule_status(g.id) for g in self.groups]

    def set_mode(self, group_id: str, mode: str) -> None:
        if mode not in ("primary", "backup"):
            raise ValueError(f"mode must be 'primary' or 'backup', got {mode!r}")
        runtime = self.runtimes[group_id]
        runtime.state.mode = mode
        runtime.state.primary_failures = 0
        runtime.state.primary_successes = 0
        runtime.state.notified_would_restart = False

    async def start(self) -> None:
        if not self.groups or not _watchdog_enabled():
            if self.groups and not _watchdog_enabled():
                logger.info("Failover watchdog disabled (CAPPS_WATCHDOG=0)")
            return
        if self._task is not None:
            return
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="capps-failover-watchdog")
        logger.info(
            "Failover watchdog started for %s",
            ", ".join(g.id for g in self.groups),
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._wake = None

    def _seconds_until_next_tick(self) -> float:
        now = time.monotonic()
        delays: list[float] = []
        for group in self.groups:
            runtime = self.runtimes[group.id]
            interval = self.effective_interval_sec(group.id)
            elapsed = now - runtime.last_tick if runtime.last_tick else interval
            delays.append(max(0.0, interval - elapsed))
        return min(delays) if delays else 3600.0

    async def _sleep_until_next_tick(self) -> None:
        delay = self._seconds_until_next_tick()
        if self._wake is None:
            await asyncio.sleep(delay)
            return
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def _run(self) -> None:
        try:
            while True:
                self._clear_expired_overrides()
                now = time.monotonic()
                for group in self.groups:
                    runtime = self.runtimes[group.id]
                    interval = self.effective_interval_sec(group.id)
                    due = runtime.last_tick == 0 or (now - runtime.last_tick) >= interval
                    if not due:
                        continue
                    try:
                        await self._tick_group(group)
                    except Exception:
                        logger.exception("Failover tick failed for %s", group.id)
                    runtime.last_tick = time.monotonic()
                await self._sleep_until_next_tick()
        except asyncio.CancelledError:
            raise

    async def _tick_group(self, group: FailoverGroup) -> None:
        from app.main import _app_status_fields, _perform_start, _perform_stop
        from app.notify import notify_failover_would_act

        primary = APPS_BY_ID[group.primary_id]
        backup = APPS_BY_ID[group.backup_id]
        state = self.runtimes[group.id].state

        primary_fields = _app_status_fields(primary)
        backup_fields = _app_status_fields(backup)
        # Verified HTTP: one miss is not enough — recheck for up to 60s before
        # counting a failover failure (avoids false negatives during busy turns).
        primary_status = await probe_app_verified(primary, primary_fields)
        backup_status = await probe_app_verified(backup, backup_fields)
        primary_up = bool(primary_status.get("running"))
        backup_up = bool(backup_status.get("running"))

        if state.mode == "primary":
            if primary_up:
                state.primary_failures = 0
                state.notified_would_restart = False
                if backup_up:
                    if not group.auto_restart:
                        logger.warning(
                            "%s: backup running while primary healthy; "
                            "auto_restart=false — would have stopped backup",
                            group.id,
                        )
                        return
                    logger.warning(
                        "%s: backup running while primary healthy; stopping backup",
                        group.id,
                    )
                    await _perform_stop(backup, backup_fields)
                return

            state.primary_failures += 1
            state.primary_successes = 0
            if state.primary_failures < group.fail_threshold:
                logger.debug(
                    "%s: primary down (%s/%s)",
                    group.id,
                    state.primary_failures,
                    group.fail_threshold,
                )
                return

            if not group.auto_restart:
                msg = (
                    f"{group.id}: primary down after {state.primary_failures} checks; "
                    "would have restarted primary (auto_restart=false)"
                )
                logger.warning("%s", msg)
                if not state.notified_would_restart:
                    await notify_failover_would_act(primary, msg)
                    state.notified_would_restart = True
                return

            logger.warning(
                "%s: primary down after %s checks; restarting primary first",
                group.id,
                state.primary_failures,
            )
            if backup_up:
                await _perform_stop(backup, backup_fields)

            primary_result = await _perform_start(primary, primary_fields)
            if primary_result.get("success") and (
                primary_result.get("running") or primary_result.get("skipped")
            ):
                state.mode = "primary"
                state.primary_failures = 0
                logger.info("%s: primary restarted", group.id)
                return

            logger.warning(
                "%s: primary restart failed (%s); activating backup",
                group.id,
                primary_result.get("message", "unknown error"),
            )
            result = await _perform_start(backup, backup_fields)
            if result.get("success") and (result.get("running") or result.get("skipped")):
                state.mode = "backup"
                state.primary_failures = 0
                logger.info("%s: backup activated", group.id)
            else:
                logger.error(
                    "%s: backup start also failed — %s",
                    group.id,
                    result.get("message", "unknown error"),
                )
            return

        if not primary_up:
            state.primary_successes = 0
            if not backup_up:
                if not group.auto_restart:
                    msg = (
                        f"{group.id}: nothing healthy in backup mode; "
                        "would have restarted primary (auto_restart=false)"
                    )
                    logger.warning("%s", msg)
                    if not state.notified_would_restart:
                        await notify_failover_would_act(primary, msg)
                        state.notified_would_restart = True
                    return
                logger.warning(
                    "%s: nothing healthy in backup mode; trying primary first",
                    group.id,
                )
                primary_result = await _perform_start(primary, primary_fields)
                if primary_result.get("success") and (
                    primary_result.get("running") or primary_result.get("skipped")
                ):
                    state.mode = "primary"
                    state.primary_failures = 0
                    logger.info("%s: primary restored", group.id)
                    return
                logger.warning(
                    "%s: primary restore failed; restarting backup",
                    group.id,
                )
                await _perform_start(backup, backup_fields)
            return

        state.primary_successes += 1
        state.notified_would_restart = False
        if state.primary_successes < group.failback_threshold:
            logger.debug(
                "%s: primary up (%s/%s), staying on backup",
                group.id,
                state.primary_successes,
                group.failback_threshold,
            )
            return

        if not group.auto_restart:
            msg = (
                f"{group.id}: primary healthy; would have failed back from backup "
                "(auto_restart=false)"
            )
            logger.info("%s", msg)
            if backup_up and not state.notified_would_restart:
                await notify_failover_would_act(primary, msg)
                state.notified_would_restart = True
            state.mode = "primary"
            state.primary_successes = 0
            state.primary_failures = 0
            return

        logger.info("%s: primary healthy; failing back from backup", group.id)
        if backup_up:
            await _perform_stop(backup, backup_fields)
        state.mode = "primary"
        state.primary_successes = 0
        state.primary_failures = 0


watchdog = FailoverWatchdog(FAILOVER_GROUPS)
