from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("vn-market-mcp")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_event(event: str, **fields) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    logger.info(json.dumps(record, default=str, ensure_ascii=False))


# Telegram's sendMessage caps text at 4096 UTF-16 code units; stay under
# that in plain chars too so a synthesis report (routinely 4000-5000+ chars)
# doesn't get silently dropped by a 400 instead of delivered.
_TELEGRAM_MAX_LEN = 4000


def _split_chunks(text: str, max_len: int) -> list[str]:
    return [text[i : i + max_len] for i in range(0, len(text), max_len)] or [text]


def send_telegram(token: str | None, chat_id: str | None, text: str) -> bool:
    if not token or not chat_id:
        log_event("alert_skipped", reason="missing bot token or chat id", text=text)
        return False

    ok = True
    for chunk in _split_chunks(text, _TELEGRAM_MAX_LEN):
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log_event("alert_send_failed", error=str(exc), text=chunk)
            ok = False
    return ok


def send_ops_alert(text: str) -> bool:
    """Gui canh bao toi group van hanh qua Telegram Bot API.

    Can TELEGRAM_BOT_TOKEN va TELEGRAM_ALERT_CHAT_ID trong env; neu thieu,
    chi ghi log va bo qua (khong lam crash job vi thieu cau hinh alert).
    """
    return send_telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_ALERT_CHAT_ID"), text)


def send_user_message(conn, user_id: str, text: str) -> bool | None:
    """Send to a registered user's own chat through their own Hermes bot (users table).
    None = user not registered (caller decides on a fallback); False = send failed."""
    row = conn.execute("SELECT chat_id, bot_token_env FROM users WHERE user_id = %s", (user_id,)).fetchone()
    if row is None:
        return None
    chat_id, token_env = row
    return send_telegram(os.environ.get(token_env), chat_id, text)
