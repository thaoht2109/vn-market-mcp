"""session-freshness: a fresh chat session every day.

Hermes never ends a routed conversation on time (session_reset in config.yaml is inert since v0.21); only a
suspended session is replaced on its next message. So before each inbound message, if the session was last
active before the most recent 04:00 Vietnam time, suspend it: Hermes then starts a new one for this very
message (with its usual continuity hint), the same path /stop uses. Commands (/new, /stop...) pass untouched.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ponytail: one fixed cut for every chat; make it per-profile config if a user ever needs another hour.
RESET_HOUR = 4
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def should_reset(updated_at: datetime, now: datetime) -> bool:
    """Was the session last active before the latest RESET_HOUR cut? Naive times are this machine's local time."""
    updated, now = (t if t.tzinfo else t.astimezone() for t in (updated_at, now))
    now = now.astimezone(TZ)
    cut = datetime.combine(now.date(), time(RESET_HOUR), TZ)
    if now < cut:
        cut -= timedelta(days=1)
    return updated < cut


def _on_dispatch(event=None, gateway=None, session_store=None, **_):
    try:
        if not (event and gateway and session_store and event.source) or (event.text or "").startswith("/"):
            return None
        key = gateway._session_key_for_source(event.source)
        entry = session_store.lookup_by_session_key(key)
        if entry and entry.updated_at and should_reset(entry.updated_at, datetime.now()):
            session_store.suspend_session(key)
            logger.info("session-freshness: new day, suspended %s (last active %s)", key, entry.updated_at)
    except Exception:  # never block a message over this
        logger.exception("session-freshness: check failed, message dispatched as usual")
    return None


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _on_dispatch)


if __name__ == "__main__":
    vn = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=TZ)  # noqa: E731
    assert should_reset(vn(17, 23), vn(18, 5))        # yesterday evening -> this morning
    assert not should_reset(vn(18, 5), vn(18, 10))    # same day
    assert not should_reset(vn(18, 3), vn(18, 3, 30)) # same night, before the cut
    assert should_reset(vn(18, 3), vn(18, 4, 1))      # crossed the cut
    assert should_reset(vn(15, 12), vn(17, 3))        # days ago, now before the cut
    naive = vn(18, 5).astimezone().replace(tzinfo=None)  # how Hermes stores updated_at: naive local time
    assert not should_reset(naive, vn(18, 6)) and should_reset(naive, vn(19, 4, 1))
    print("session-freshness: ok")
