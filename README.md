# capps — c-apps dashboard

Local launcher page for sibling apps under the shared apps root (typically `X:\` when mapped from a UNC share).

```bash
git clone https://github.com/armedad/capps.git
```

On Windows, prefer `X:\capps` for git (UNC paths can trigger “dubious ownership”). If needed:

```bash
git config --global --add safe.directory X:/capps
```

## What it does

- Serves **http://127.0.0.1:8000/** with links to gauth, notetaker, voice-dictation, and status for **Ollama**
- **Ollama** is the standard local LLM service (port 11434), not a c-app — monitored only; notetaker and others may depend on it
- **Refresh all** or per-app **Refresh** checks health; actions poll up to **60 seconds** and report success or failure
- **Start** launches when stopped; **Stop** / **Restart** call each app’s shutdown API where supported
- **gauth**: Stop/Restart via `POST /api/local/shutdown` (loopback; respects `GAUTH_ALLOW_SHUTDOWN`)

## App control API

Single endpoint to start, stop, or restart any app in `apps.json` (same port **8000**, same behavior as the per-app routes):

```bash
curl -X POST http://127.0.0.1:8000/api/apps/control \
  -H "Content-Type: application/json" \
  -d "{\"app_id\": \"gauth\", \"action\": \"restart\"}"
```

`action` must be `start`, `stop`, or `restart`. Failover apps (e.g. `cursor-agent-telegram`) use the failover start/stop/restart logic. Per-app routes (`POST /api/apps/{app_id}/start`, etc.) still work.

**Status** (same health probes as dashboard Refresh):

```bash
# All dashboard apps
curl http://127.0.0.1:8000/api/apps/status

# One app
curl "http://127.0.0.1:8000/api/apps/status?app_id=gauth"
```

Returns `{"apps": [...], "results": [...]}`. Omit `app_id` for all apps; include it for a single app (failover-aware where configured).

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

**Stop / restart** (loopback `POST` only):

| App | Endpoint | Notes |
|-----|----------|--------|
| gauth | `/api/local/shutdown` | Loopback only; disable with `GAUTH_ALLOW_SHUTDOWN=0` |
| notetaker | `/api/local/shutdown` | Requires `NOTETAKER_LOCAL_SHUTDOWN=1` (set in `notetaker.ps1`) |
| voice-dictation | `/api/local/shutdown` | Requires combined launcher (`start.bat` / `run_combined_app.py`) |

Restart = shutdown, brief wait, then the app’s launch script again via capps (notetaker: `notetaker.bat`).

## Failover watchdog

When `apps.json` defines `failover_groups`, capps polls the **primary** app on an interval (default **1 hour**). After consecutive failed health checks (default 2), it normally restarts the primary (then backup if that fails).

Set `"auto_restart": false` on a group to keep health checks but **never** start/stop/restart. Instead capps notifies via Telegram (or HA persistent_notification) that it would have acted. Uses the primary app's `.env` (`TELEGRAM_HA_BOT_TOKEN` or `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USER_IDS`; optional `HA_URL`/`HA_TOKEN`).

- **cursor-agent Telegram**: primary `../cursor-agent`, backup `../cursor-agent-backup` (stable tag; see `BACKUP.md` there). Currently `auto_restart: false`.
- Backup has `"autostart": false` — not started on dashboard **Start all** or `CAPPS_STARTUP`.
- Only one instance should run (same port 8947 / Telegram token).
- Disable polling with `CAPPS_WATCHDOG=0`.

**Temporary faster checks** (loopback only):

```bash
curl -X POST http://127.0.0.1:8000/api/failover/schedule \
  -H "Content-Type: application/json" \
  -d "{\"every_minutes\": 5, \"for_hours\": 2}"
```

Also accepts `interval_sec` + `duration_sec`, optional `group_id`. Reverts automatically when the duration expires. `GET /api/failover/schedule` shows active overrides; `DELETE /api/failover/schedule` clears them early.

Set `CHEEAPPS_ROOT` if apps live somewhere other than the parent of `capps` (default `X:\`).
Set `OLLAMA_URL` if Ollama is not on `http://127.0.0.1:11434`.

Optional **`apps.local.json`** (gitignored) can hide apps from this checkout without changing `apps.json`:

```json
{
  "disabled_app_ids": ["notetaker", "voice-dictation"]
}
```
