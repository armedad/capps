from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CAPPS_DIR = Path(__file__).resolve().parent.parent

_BASE_REQUIRED_FIELDS = (
    "id",
    "name",
    "description",
    "app_dir",
    "launch_script",
    "control",
)

_HTTP_REQUIRED_FIELDS = ("port", "health_path")


def _map_unc_apps_path(path: Path) -> Path:
    """CMD cannot use UNC as cwd; map \\\\host\\apps\\... to X:\\... when available."""
    raw = os.path.normpath(str(path))
    if len(raw) >= 2 and raw[1] == ":":
        return Path(raw)
    if raw.startswith("\\\\"):
        parts = [p for p in raw.split("\\") if p]
        if len(parts) >= 2 and parts[1].lower() == "apps":
            rest = "\\".join(parts[2:]) if len(parts) > 2 else ""
            x_base = Path("X:/")
            if x_base.is_dir():
                return x_base / rest if rest else x_base
    return path


def _config_path() -> Path:
    env = os.environ.get("CAPPS_APPS_CONFIG", "").strip()
    if env:
        return Path(env)
    return CAPPS_DIR / "apps.json"


def _local_config_path() -> Path:
    return CAPPS_DIR / "apps.local.json"


def _disabled_app_ids() -> frozenset[str]:
    """Optional gitignored overlay: {"disabled_app_ids": ["notetaker", ...]}."""
    path = _local_config_path()
    if not path.is_file():
        return frozenset()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    raw = data.get("disabled_app_ids", [])
    if not isinstance(raw, list):
        raise ValueError(f"{path}: 'disabled_app_ids' must be an array")
    return frozenset(str(item).strip() for item in raw if str(item).strip())


def _apply_local_disabled(
    apps: tuple[AppDef, ...], groups: tuple[FailoverGroup, ...]
) -> tuple[tuple[AppDef, ...], tuple[FailoverGroup, ...]]:
    disabled = _disabled_app_ids()
    if not disabled:
        return apps, groups
    apps = tuple(app for app in apps if app.id not in disabled)
    present = {app.id for app in apps}
    groups = tuple(
        group
        for group in groups
        if group.primary_id in present and group.backup_id in present
    )
    return apps, groups


def _resolve_under_capps(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (CAPPS_DIR / p).resolve()
    return _map_unc_apps_path(resolved)


ControlKind = Literal["remote", "stub", "script"]
HealthProbeKind = Literal["http", "process"]


@dataclass(frozen=True)
class StartDebugConfig:
    """Foreground / verbose launch (console window). Optional per-app overrides."""

    launch_script: str | None = None
    launch_args: str = ""


@dataclass(frozen=True)
class AppDef:
    id: str
    name: str
    description: str
    port: int
    health_path: str
    app_dir: Path
    launch_script: str
    control: ControlKind = "stub"
    shutdown_path: str | None = None
    health_url: str | None = None
    external: bool = False  # not a c-app; monitor only
    health_probe: HealthProbeKind = "http"
    process_match: str | None = None
    stop_script: str | None = None
    launch_args: str = ""
    start_debug: StartDebugConfig | None = None
    autostart: bool = True


@dataclass(frozen=True)
class FailoverGroup:
    """Primary/backup pair: watchdog starts backup when primary health fails."""

    id: str
    primary_id: str
    backup_id: str
    interval_sec: float = 3600.0
    fail_threshold: int = 2
    failback_threshold: int = 2
    # When False, health checks continue but start/stop/restart are skipped;
    # the operator is notified that an action would have been taken instead.
    auto_restart: bool = True


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")


def _ollama_health_url() -> str:
    return f"{_ollama_base_url()}/api/tags"


def health_check_label(app: AppDef) -> str:
    if app.health_url:
        return app.health_url
    if app.health_probe == "process" and app.process_match:
        return f"process:{app.process_match}"
    return f"http://127.0.0.1:{app.port}{app.health_path}"


def app_service_url(app: AppDef) -> str | None:
    if app.health_probe != "http" or app.port <= 0:
        return None
    return f"http://127.0.0.1:{app.port}/"


def _parse_app_entry(raw: dict[str, Any], *, source: str) -> AppDef:
    missing = [f for f in _BASE_REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ValueError(f"{source}: missing required fields: {', '.join(missing)}")

    app_id = str(raw["id"])
    control = raw["control"]
    if control not in ("remote", "stub", "script"):
        raise ValueError(
            f"{source} id={app_id!r}: control must be 'remote', 'stub', or 'script'"
        )

    health_probe = str(raw.get("health_probe", "http")).strip().lower()
    if health_probe not in ("http", "process"):
        raise ValueError(
            f"{source} id={app_id!r}: health_probe must be 'http' or 'process'"
        )

    if health_probe == "http":
        missing_http = [f for f in _HTTP_REQUIRED_FIELDS if f not in raw]
        if missing_http:
            raise ValueError(
                f"{source} id={app_id!r}: missing required fields for http health: "
                f"{', '.join(missing_http)}"
            )

    process_match = raw.get("process_match")
    if process_match is not None:
        process_match = str(process_match).strip() or None
    if health_probe == "process":
        if not process_match:
            raise ValueError(
                f"{source} id={app_id!r}: process_match is required when health_probe is 'process'"
            )

    stop_script = raw.get("stop_script")
    if stop_script is not None:
        stop_script = str(stop_script).strip() or None
    if control == "script" and not stop_script:
        raise ValueError(
            f"{source} id={app_id!r}: stop_script is required when control is 'script'"
        )

    external = bool(raw.get("external", False))
    launch_script = str(raw["launch_script"])
    app_dir = _resolve_under_capps(str(raw["app_dir"]))

    port = int(raw.get("port", 0))
    health_path = str(raw.get("health_path", ""))

    health_url = raw.get("health_url")
    if health_url is not None:
        health_url = str(health_url).strip() or None
    elif external and app_id == "ollama":
        health_url = _ollama_health_url()

    shutdown_path = raw.get("shutdown_path")
    if shutdown_path is not None:
        shutdown_path = str(shutdown_path).strip() or None

    if control == "remote" and not shutdown_path:
        raise ValueError(
            f"{source} id={app_id!r}: shutdown_path is required when control is 'remote'"
        )

    launch_args = str(raw.get("launch_args", "")).strip()

    start_debug: StartDebugConfig | None = None
    start_debug_raw = raw.get("start_debug")
    if start_debug_raw is not None:
        if not isinstance(start_debug_raw, dict):
            raise ValueError(f"{source} id={app_id!r}: start_debug must be an object")
        if start_debug_raw:
            sd_script = start_debug_raw.get("launch_script")
            if sd_script is not None:
                sd_script = str(sd_script).strip() or None
            start_debug = StartDebugConfig(
                launch_script=sd_script,
                launch_args=str(start_debug_raw.get("launch_args", "")).strip(),
            )

    autostart = bool(raw.get("autostart", True))

    return AppDef(
        id=app_id,
        name=str(raw["name"]),
        description=str(raw["description"]),
        port=port,
        health_path=health_path,
        app_dir=app_dir,
        launch_script=launch_script,
        control=control,
        shutdown_path=shutdown_path,
        health_url=health_url,
        external=external,
        health_probe=health_probe,  # type: ignore[arg-type]
        process_match=process_match,
        stop_script=stop_script,
        launch_args=launch_args,
        start_debug=start_debug,
        autostart=autostart,
    )


def _parse_failover_group(raw: dict[str, Any], *, source: str) -> FailoverGroup:
    for field in ("id", "primary_id", "backup_id"):
        if field not in raw:
            raise ValueError(f"{source}: missing required field {field!r}")

    interval_sec = float(raw.get("interval_sec", 3600))
    if interval_sec <= 0:
        raise ValueError(f"{source}: interval_sec must be positive")

    fail_threshold = int(raw.get("fail_threshold", 2))
    failback_threshold = int(raw.get("failback_threshold", 2))
    if fail_threshold < 1 or failback_threshold < 1:
        raise ValueError(f"{source}: fail_threshold and failback_threshold must be >= 1")

    auto_restart = bool(raw.get("auto_restart", True))

    return FailoverGroup(
        id=str(raw["id"]),
        primary_id=str(raw["primary_id"]),
        backup_id=str(raw["backup_id"]),
        interval_sec=interval_sec,
        fail_threshold=fail_threshold,
        failback_threshold=failback_threshold,
        auto_restart=auto_restart,
    )


def _load_config_from_json(path: Path) -> tuple[tuple[AppDef, ...], tuple[FailoverGroup, ...]]:
    if not path.is_file():
        raise FileNotFoundError(f"Apps config not found: {path}")

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    apps_raw = data.get("apps")
    if not isinstance(apps_raw, list):
        raise ValueError(f"{path}: 'apps' must be an array")

    seen: set[str] = set()
    apps: list[AppDef] = []
    for i, entry in enumerate(apps_raw):
        source = f"{path} apps[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{source}: must be an object")
        app = _parse_app_entry(entry, source=source)
        if app.id in seen:
            raise ValueError(f"{path}: duplicate app id {app.id!r}")
        seen.add(app.id)
        apps.append(app)

    failover_raw = data.get("failover_groups", [])
    if failover_raw is None:
        failover_raw = []
    if not isinstance(failover_raw, list):
        raise ValueError(f"{path}: 'failover_groups' must be an array")

    groups: list[FailoverGroup] = []
    seen_group_ids: set[str] = set()
    for i, entry in enumerate(failover_raw):
        source = f"{path} failover_groups[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{source}: must be an object")
        group = _parse_failover_group(entry, source=source)
        if group.id in seen_group_ids:
            raise ValueError(f"{path}: duplicate failover group id {group.id!r}")
        seen_group_ids.add(group.id)
        if group.primary_id not in seen:
            raise ValueError(
                f"{path}: failover group {group.id!r} references unknown primary {group.primary_id!r}"
            )
        if group.backup_id not in seen:
            raise ValueError(
                f"{path}: failover group {group.id!r} references unknown backup {group.backup_id!r}"
            )
        if group.primary_id == group.backup_id:
            raise ValueError(f"{path}: failover group {group.id!r} primary and backup must differ")
        groups.append(group)

    return tuple(apps), tuple(groups)


APPS, FAILOVER_GROUPS = _apply_local_disabled(*_load_config_from_json(_config_path()))

APPS_BY_ID = {a.id: a for a in APPS}
FAILOVER_BY_PRIMARY_ID = {g.primary_id: g for g in FAILOVER_GROUPS}
FAILOVER_BY_BACKUP_ID = {g.backup_id: g for g in FAILOVER_GROUPS}
BACKUP_APP_IDS = frozenset(FAILOVER_BY_BACKUP_ID.keys())
DASHBOARD_APPS = tuple(a for a in APPS if a.id not in BACKUP_APP_IDS)

NOT_IMPLEMENTED_MSG = "not yet implemented"
EXTERNAL_MSG = "Not managed from this dashboard (external service)"
