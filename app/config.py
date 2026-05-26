from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CAPPS_DIR = Path(__file__).resolve().parent.parent


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


def _default_apps_root() -> Path:
    env = os.environ.get("CHEEAPPS_ROOT", "").strip()
    if env:
        return _map_unc_apps_path(Path(env))
    return _map_unc_apps_path(CAPPS_DIR.parent)


APPS_ROOT = _default_apps_root()

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


def _app_dir(name: str) -> Path:
    return APPS_ROOT / name


APPS: tuple[AppDef, ...] = (
    AppDef(
        id="gauth",
        name="gauth",
        description="Gmail OAuth / API / MCP",
        port=4664,
        health_path="/health",
        app_dir=_app_dir("gauth"),
        launch_script="launch-startup.bat",
        control="remote",
        shutdown_path="/api/local/shutdown",
    ),
    AppDef(
        id="notetaker",
        name="notetaker",
        description="Meeting record, transcribe, diarize",
        port=6684,
        health_path="/api/health",
        app_dir=_app_dir("notetaker"),
        launch_script="start.bat",
        control="remote",
        shutdown_path="/api/local/shutdown",
    ),
    AppDef(
        id="ollama",
        name="Ollama",
        description="Standard Ollama LLM (not a c-app; notetaker and others may depend on it)",
        port=11434,
        health_path="/api/tags",
        app_dir=CAPPS_DIR,
        launch_script="",
        control="stub",
        health_url=_ollama_health_url(),
        external=True,
    ),
    AppDef(
        id="voice-dictation",
        name="voice-dictation",
        description="Hotkey dictation → STT → type",
        port=8946,
        health_path="/health",
        app_dir=_app_dir("voice-dictation"),
        launch_script="start.bat",
        control="remote",
        shutdown_path="/api/local/shutdown",
    ),
)

APPS_BY_ID = {a.id: a for a in APPS}

NOT_IMPLEMENTED_MSG = "not yet implemented"
EXTERNAL_MSG = "Not managed from this dashboard (external service)"
