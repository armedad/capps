from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CAPPS_DIR = Path(__file__).resolve().parent.parent

_REQUIRED_FIELDS = (
    "id",
    "name",
    "description",
    "port",
    "health_path",
    "app_dir",
    "launch_script",
    "control",
)


def _map_unc_apps_path(path: Path) -> Path:
    """CMD cannot use UNC as cwd; map \\\\cc\\apps\\... to X:\\... when available."""
    raw = os.path.normpath(str(path))
    if len(raw) >= 2 and raw[1] == ":":
        return Path(raw)
    lower = raw.lower()
    if lower.startswith("\\\\cc\\apps"):
        rest = raw[9:].lstrip("\\/")
        x_base = Path("X:/")
        if x_base.is_dir():
            return x_base / rest if rest else x_base
    return path


def _config_path() -> Path:
    env = os.environ.get("CAPPS_APPS_CONFIG", "").strip()
    if env:
        return Path(env)
    return CAPPS_DIR / "apps.json"


def _resolve_under_capps(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (CAPPS_DIR / p).resolve()
    return _map_unc_apps_path(resolved)


ControlKind = Literal["remote", "stub"]


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


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")


def _ollama_health_url() -> str:
    return f"{_ollama_base_url()}/api/tags"


def _parse_app_entry(raw: dict[str, Any], *, source: str) -> AppDef:
    missing = [f for f in _REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ValueError(f"{source}: missing required fields: {', '.join(missing)}")

    app_id = str(raw["id"])
    control = raw["control"]
    if control not in ("remote", "stub"):
        raise ValueError(f"{source} id={app_id!r}: control must be 'remote' or 'stub'")

    external = bool(raw.get("external", False))
    launch_script = str(raw["launch_script"])
    app_dir = _resolve_under_capps(str(raw["app_dir"]))

    health_url = raw.get("health_url")
    if health_url is not None:
        health_url = str(health_url).strip() or None
    elif external and app_id == "ollama":
        health_url = _ollama_health_url()

    shutdown_path = raw.get("shutdown_path")
    if shutdown_path is not None:
        shutdown_path = str(shutdown_path).strip() or None

    return AppDef(
        id=app_id,
        name=str(raw["name"]),
        description=str(raw["description"]),
        port=int(raw["port"]),
        health_path=str(raw["health_path"]),
        app_dir=app_dir,
        launch_script=launch_script,
        control=control,
        shutdown_path=shutdown_path,
        health_url=health_url,
        external=external,
    )


def _load_apps_from_json(path: Path) -> tuple[AppDef, ...]:
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

    return tuple(apps)


APPS: tuple[AppDef, ...] = _load_apps_from_json(_config_path())

APPS_BY_ID = {a.id: a for a in APPS}

NOT_IMPLEMENTED_MSG = "not yet implemented"
EXTERNAL_MSG = "Not managed from this dashboard (external service)"
