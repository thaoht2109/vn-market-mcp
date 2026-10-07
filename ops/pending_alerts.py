"""Print one user's undelivered price alerts and mark them delivered.

This is the script behind that user's Hermes cron job (ops/setup_alerts_cron.sh, `--no-agent`):
Hermes sends its stdout verbatim to the user's own chat through their own bot, and sends nothing
when stdout is empty. No LLM, so the numbers in an alert are exactly the ones computed in
pipeline/price_alerts.py.

    python -m ops.pending_alerts --hermes-config /opt/data/profiles/<name>/config.yaml

The DB URL and user id are read from that profile's vn-market-mcp env — the same ones its MCP
server gets — so a profile's job can only ever read its own user's alerts. Hermes runs cron scripts
with a scrubbed environment, which is why they are not taken from os.environ.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from mcp_server.connection import get_rw_conn
from pipeline.stock_report import DISCLAIMER

# Cron down for a while (gateway restart, outage): an hours-old in-session touch is noise by the
# time it would arrive. Older rows are still marked delivered so they never pile up.
MAX_AGE = timedelta(hours=6)
FOOTER = f'{DISCLAIMER} Không muốn nhận nữa: nhắn "tắt cảnh báo <mã>" hoặc "tắt mọi cảnh báo".'


def take_pending(conn, user: str, now: datetime) -> list[str]:
    rows = conn.execute(
        "UPDATE user_alerts SET delivered_at = now() WHERE user_id = %s AND delivered_at IS NULL"
        " RETURNING created_at, id, message",
        (user,),
    ).fetchall()
    # ponytail: marked before Hermes sends; a failed send loses that batch rather than re-sending it.
    return [msg for created_at, _id, msg in sorted(rows) if now - created_at <= MAX_AGE]


def render(messages: list[str]) -> str:
    return "\n\n".join([*messages, FOOTER]) if messages else ""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-config", required=True, type=Path)
    args = parser.parse_args(argv)
    env = yaml.safe_load(args.hermes_config.read_text())["mcp_servers"]["vn-market-mcp"]["env"]
    os.environ.update({k: str(v) for k, v in env.items()})  # get_rw_conn reads PIPELINE_RW_DATABASE_URL

    with get_rw_conn() as conn:
        out = render(take_pending(conn, str(env["VNMCP_USER_ID"]), datetime.now(timezone.utc)))
    if out:
        print(out)


if __name__ == "__main__":
    main()
