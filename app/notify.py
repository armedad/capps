"""Outbound alerts for failover watchdog (Telegram + optional HA)."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import AppDef

logger = logging.getLogger(__name__)


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _env_for_app(app: AppDef) -> dict[str, str]:
    return _parse_dotenv(app.app_dir / ".env")


def _chat_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            ids.append(int(part))
    return ids


async def _send_telegram(token: str, chat_ids: list[int], text: str) -> bool:
    if not token or not chat_ids:
        return False
    ok = False
    async with httpx.AsyncClient(timeout=15.0) as client:
        for chat_id in chat_ids:
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                if resp.status_code == 200:
                    ok = True
                else:
                    logger.warning(
                        "Telegram notify failed chat_id=%s status=%s body=%s",
                        chat_id,
                        resp.status_code,
                        resp.text[:200],
                    )
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Telegram notify error chat_id=%s: %s", chat_id, exc)
    return ok


async def _send_ha_persistent(env: dict[str, str], title: str, message: str) -> bool:
    base = (env.get("HA_URL") or "").strip().rstrip("/")
    token = (env.get("HA_TOKEN") or "").strip()
    if not base or not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{base}/api/services/persistent_notification/create",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "notification_id": "capps_failover_watchdog",
                    "title": title,
                    "message": message,
                },
            )
            if resp.status_code in (200, 201):
                return True
            logger.warning(
                "HA notify failed status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("HA notify error: %s", exc)
    return False


async def notify_failover_would_act(primary: AppDef, message: str) -> bool:
    """Alert Chee that watchdog would have restarted/stopped something.

    Uses primary app .env: prefer TELEGRAM_HA_BOT_TOKEN, else TELEGRAM_BOT_TOKEN,
    plus TELEGRAM_ALLOWED_USER_IDS. Falls back to HA persistent_notification.
    """
    env = _env_for_app(primary)
    token = (
        (env.get("TELEGRAM_HA_BOT_TOKEN") or "").strip()
        or (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    )
    chat_ids = _chat_ids(env.get("TELEGRAM_ALLOWED_USER_IDS") or "")
    text = f"[capps] {message}"
    sent = await _send_telegram(token, chat_ids, text)
    if not sent:
        sent = await _send_ha_persistent(
            env,
            title="capps failover watchdog",
            message=message,
        )
    if sent:
        logger.warning("Failover notify sent: %s", message)
    else:
        logger.error(
            "Failover notify failed (no Telegram/HA credentials worked): %s",
            message,
        )
    return sent
