"""Who is calling. Two front doors, one set of tools:

- Telegram (stdio): Hermes routes each user's own chat (user_id + chat_id) to that user's profile,
  whose MCP server is launched with VNMCP_USER_ID. Shared chats land on a profile without it: they
  can analyse tickers, but have no personal scope (positions, watchlist).
- Any authenticated MCP HTTP host built on these tools: the SDK's bearer check puts an AccessToken on
  the request, and its `subject` is the user. The process env is never consulted there.

The id never comes from the model, so a chat message can't make the server act for another user."""
from __future__ import annotations

import os
from datetime import datetime

from mcp.server.lowlevel.server import request_ctx

from mcp_server.envelope import build_envelope

NO_PERSONAL_SCOPE_WARNING = (
    "Danh mục riêng (theo dõi mã, khai báo vị thế) chỉ dùng được trong chat riêng của bạn với bot; "
    "nhóm chung chỉ phân tích mã."
)


def _http_request():
    """The Starlette request behind this tool call, or None on stdio (and outside any tool call)."""
    try:
        return request_ctx.get().request
    except LookupError:
        return None


def pinned_user() -> str | None:
    req = _http_request()
    if req is not None:  # scope["user"]: the SDK's AuthenticatedUser (RequireAuthMiddleware lets no one else through)
        return getattr(getattr(req.scope.get("user"), "access_token", None), "subject", None)
    return os.environ.get("VNMCP_USER_ID") or None


def writes_shared() -> bool:
    """May this caller write rows every user reads back (news judgments, report commentary)?
    Only our own Hermes profiles; an outside agent's text must not reach other users."""
    return _http_request() is None


def no_personal_scope(now: datetime) -> dict:
    return build_envelope({"status": "no_personal_scope"}, sources=[], as_of=now, warnings=[NO_PERSONAL_SCOPE_WARNING])
