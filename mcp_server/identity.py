"""Who is calling. Hermes routes each user's own chat (user_id + chat_id) to that user's profile,
whose MCP server is launched with VNMCP_USER_ID. Shared chats land on a profile without it: they
can analyse tickers, but have no personal scope (positions, watchlist). The id never comes from
the model, so a chat message can't make the server act for another user."""
from __future__ import annotations

import os
from datetime import datetime

from mcp_server.envelope import build_envelope

NO_PERSONAL_SCOPE_WARNING = (
    "Danh mục riêng (theo dõi mã, khai báo vị thế) chỉ dùng được trong chat riêng của bạn với bot; "
    "nhóm chung chỉ phân tích mã."
)


def pinned_user() -> str | None:
    return os.environ.get("VNMCP_USER_ID") or None


def no_personal_scope(now: datetime) -> dict:
    return build_envelope({"status": "no_personal_scope"}, sources=[], as_of=now, warnings=[NO_PERSONAL_SCOPE_WARNING])
