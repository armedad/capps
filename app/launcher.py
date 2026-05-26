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


def launch_app(app: AppDef) -> None:
    script_unc = app.app_dir / app.launch_script
    script = _map_unc_apps_path(script_unc)
    launch_dir = script.parent
    child_env = _child_environ()

    if not script.is_file():
        raise FileNotFoundError(f"Launch script not found: {script}")

    if sys.platform == "win32":
        cmd = ["cmd.exe", "/c", str(script)]
        subprocess.Popen(
            cmd,
            cwd=_WINDOWS_CWD,
            env=child_env,
            creationflags=_CREATE_NEW_CONSOLE,
        )
        return

    subprocess.Popen(
        ["/bin/sh", str(script)],
        cwd=str(launch_dir),
        env=child_env,
        start_new_session=True,
        close_fds=True,
    )
