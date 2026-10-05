"""Who is calling. Each user runs their own Hermes profile, whose MCP server is launched with
VNMCP_USER_ID: that pinned id wins over the declared_by the model fills in, so a chat message
can never make the server act for another user."""
from __future__ import annotations

import os


def pinned_user() -> str | None:
    return os.environ.get("VNMCP_USER_ID") or None


def current_user(declared_by: str | None) -> str:
    user = pinned_user() or declared_by
    if not user:
        raise ValueError("declared_by is required (no VNMCP_USER_ID set for this server)")
    return user
