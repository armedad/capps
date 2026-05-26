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

- Serves **http://127.0.0.1:8000/** with links to gauth, notetaker, voice-dictation, and status for **Ollama**
- **Ollama** is the standard local LLM service (port 11434), not a c-app — monitored only; notetaker and others may depend on it
- **Refresh all** or per-app **Refresh** checks health; actions poll up to **60 seconds** and report success or failure
- **Start** launches when stopped; **Stop** / **Restart** call each app’s shutdown API where supported
- **gauth**: Stop/Restart show “not yet implemented” (no gauth changes)

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
| notetaker | `/api/local/shutdown` | Requires `NOTETAKER_LOCAL_SHUTDOWN=1` (set in `start.ps1`) |
| voice-dictation | `/api/local/shutdown` | Requires combined launcher (`start.bat` / `run_combined_app.py`) |

Restart = shutdown, brief wait, then `start.bat` again via capps.

Set `CHEEAPPS_ROOT` if apps live somewhere other than the parent of `capps` (default `X:\`).
Set `OLLAMA_URL` if Ollama is not on `http://127.0.0.1:11434`.
