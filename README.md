# capps — c-apps dashboard

Local launcher page for sibling apps under `X:\` (`\\cc\apps\`).

```bash
git clone https://github.com/armedad/capps.git
```

On Windows, prefer `X:\capps` for git (UNC paths can trigger “dubious ownership”). If needed:

```bash
git config --global --add safe.directory X:/capps
```

## What it does

- Serves **http://127.0.0.1:8000/** with links to gauth, notetaker, voice-dictation, **cursor-agent Telegram**, and status for **Ollama**
- **Ollama** is the standard local LLM service (port 11434), not a c-app — monitored only; notetaker and others may depend on it
- **Refresh all** or per-app **Refresh** checks health; actions poll up to **60 seconds** and report success or failure
- **Start all** starts every manageable app that is not already running (skips external/monitor-only apps; does not restart running ones)
- **Stop all** stops every manageable app that is currently running
- **Start** launches when stopped; **Stop** / **Restart** call each app’s shutdown API where supported
- **gauth**: Stop/Restart via `POST /api/local/shutdown` (loopback; respects `GAUTH_ALLOW_SHUTDOWN`)

## Run

```bat
cd X:\capps
start.bat
```

Or: `python -m pip install -r requirements.txt` then `python run.py`.

## Apps (defaults)

| Entry | Port | Health |
|-------|------|--------|
| gauth | 4664 | `/health` |
| notetaker | 6684 | `/api/health` |
| Ollama (external) | 11434 | `GET /api/tags` (`OLLAMA_URL`, default `http://127.0.0.1:11434`) |
| voice-dictation | 8946 | `/health` |
| cursor-agent Telegram | — | `process:cursor_chat.telegram_bot` |

**Stop / restart**:

| Kind | Apps | Mechanism |
|------|------|-----------|
| HTTP shutdown | gauth, notetaker, voice-dictation | Loopback `POST` to `shutdown_path` |
| Script | cursor-agent Telegram | `stop_script` from config (e.g. `telegram_bot-stop.bat`) |

**HTTP shutdown** (loopback `POST` only):

| App | Endpoint | Notes |
|-----|----------|--------|
| gauth | `/api/local/shutdown` | Loopback only; disable with `GAUTH_ALLOW_SHUTDOWN=0` |
| notetaker | `/api/local/shutdown` | Requires `NOTETAKER_LOCAL_SHUTDOWN=1` (set in `start.ps1`) |
| voice-dictation | `/api/local/shutdown` | Requires combined launcher (`start.bat` / `run_combined_app.py`) |

Restart = shutdown, brief wait, then `start.bat` again via capps.

## App configuration

Apps are defined in [`apps.json`](apps.json) at the capps repo root. Each entry’s `app_dir` is resolved relative to the capps directory (where `run.py` lives), e.g. `../gauth` for a sibling folder next to `capps`.

To add or change an app, edit `apps.json` and restart capps.

### Non-HTTP apps (process health + script control)

For apps without an HTTP server, set in `apps.json`:

| Field | Purpose |
|-------|---------|
| `health_probe` | `"process"` (default is `"http"`) |
| `process_match` | Substring matched in the **python.exe** command line (capps ignores PowerShell so the health probe cannot false-positive on itself) |
| `control` | `"script"` |
| `stop_script` | Script run on Stop/Restart (e.g. `telegram_bot-stop.bat`) |
| `launch_script` | Script run on Start (e.g. `telegram_bot-background.bat`) |
| `port` | `0` when not applicable |

capps does not hardcode app-specific logic; all behavior comes from the config entry.

| Env var | Purpose |
|---------|---------|
| `CAPPS_APPS_CONFIG` | Path to an alternate apps JSON file |
| `OLLAMA_URL` | Ollama base URL if not `http://127.0.0.1:11434` |
