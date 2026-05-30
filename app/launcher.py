from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.config import AppDef, _map_unc_apps_path

_CREATE_NEW_CONSOLE = 0x00000010
# CMD cannot use UNC as cwd; scripts use cd /d "%~dp0" so only the .bat path must be valid.
_WINDOWS_CWD = r"C:\Windows"


def _child_environ() -> dict[str, str]:
    return {k: str(v) for k, v in os.environ.items()}


def _resolve_script(app: AppDef, script_name: str) -> Path:
    script_unc = app.app_dir / script_name
    script = _map_unc_apps_path(script_unc)
    if not script.is_file():
        raise FileNotFoundError(f"Script not found: {script}")
    return script


def _windows_cmd(script: Path, args: str) -> list[str]:
    """Build argv for cmd /c. Use list2cmdline for the tail so paths/args are not double-quoted."""
    parts = [str(script)]
    if args.strip():
        parts.extend(args.split())
    # cmd /c expects one command string; list2cmdline quotes paths with spaces correctly.
    return ["cmd.exe", "/c", subprocess.list2cmdline(parts)]


def launch_app(app: AppDef, *, debug: bool = False) -> None:
    if debug:
        if not app.start_debug:
            raise ValueError(f"No start_debug configured for {app.id}")
        cfg = app.start_debug
        script_name = cfg.launch_script or app.launch_script
        args = cfg.launch_args
        new_console = True
    else:
        script_name = app.launch_script
        args = app.launch_args
        new_console = False

    script = _resolve_script(app, script_name)
    launch_dir = script.parent
    child_env = _child_environ()

    if sys.platform == "win32":
        flags = _CREATE_NEW_CONSOLE if new_console else 0
        subprocess.Popen(
            _windows_cmd(script, args),
            cwd=_WINDOWS_CWD,
            env=child_env,
            creationflags=flags,
        )
        return

    cmd = ["/bin/sh", str(script)]
    if args.strip():
        cmd.extend(args.split())
    subprocess.Popen(
        cmd,
        cwd=str(launch_dir),
        env=child_env,
        start_new_session=True,
        close_fds=True,
    )


def run_script(app: AppDef, script_name: str, args: str = "") -> int:
    """Run a stop/health script synchronously and return its exit code."""
    script = _resolve_script(app, script_name)
    child_env = _child_environ()

    if sys.platform == "win32":
        result = subprocess.run(
            _windows_cmd(script, args),
            cwd=_WINDOWS_CWD,
            env=child_env,
            check=False,
        )
        return int(result.returncode)

    cmd = ["/bin/sh", str(script)]
    if args.strip():
        cmd.extend(args.split())
    result = subprocess.run(
        cmd,
        cwd=str(script.parent),
        env=child_env,
        check=False,
    )
    return int(result.returncode)
