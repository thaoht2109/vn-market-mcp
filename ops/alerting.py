from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("vn-market-mcp")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_event(event: str, **fields) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    logger.info(json.dumps(record, default=str, ensure_ascii=False))


def send_ops_alert(text: str) -> bool:
    """Gui canh bao toi group van hanh qua Telegram Bot API.

    Can TELEGRAM_BOT_TOKEN va TELEGRAM_ALERT_CHAT_ID trong env; neu thieu,
    chi ghi log va bo qua (khong lam crash job vi thieu cau hinh alert).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_ALERT_CHAT_ID")
    if not token or not chat_id:
        log_event("alert_skipped", reason="missing TELEGRAM_BOT_TOKEN or TELEGRAM_ALERT_CHAT_ID", text=text)
        return False

    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10.0,
        )
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log_event("alert_send_failed", error=str(exc), text=text)
        return False
