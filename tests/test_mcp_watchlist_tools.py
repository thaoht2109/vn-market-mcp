import json

import pytest

from db.connection import get_conn
from mcp_server.tools.positions import clear_position_tool, set_position_tool
from mcp_server.tools.watchlist import list_watchlist_tool, unwatch_ticker_tool, watch_ticker_tool
from ops.scheduler import get_watchlist


class _FakeProvider:
    def __init__(self, source="VCI"):
        pass

    def lookup_listing(self, ticker):
        return ("CTCP WATCHTEST", "UPCOM") if ticker == "WATCHTEST" else None


@pytest.fixture(autouse=True)
def _fake_vnstock(monkeypatch):
    monkeypatch.setattr("mcp_server.tools.watchlist.VNStockProvider", _FakeProvider)
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist_extra WHERE ticker = 'WATCHTEST'")
        conn.execute("DELETE FROM jobs WHERE ticker = 'WATCHTEST'")
        conn.execute("DELETE FROM predictions WHERE ticker = 'WATCHTEST'")
        conn.execute("DELETE FROM positions WHERE ticker = 'WATCHTEST'")
        conn.execute("DELETE FROM runs WHERE run_id = 'watch-run'")
        conn.execute("DELETE FROM tickers WHERE ticker = 'WATCHTEST'")


@pytest.fixture
def as_user(monkeypatch):
    """The user's own chat: Hermes routed it to a profile whose MCP server pins VNMCP_USER_ID."""
    def _as(user):
        monkeypatch.setenv("VNMCP_USER_ID", user)
    return _as


def test_watch_registers_new_ticker_queues_a_run_and_joins_the_schedule(as_user):
    as_user("alice")
    result = watch_ticker_tool("watchtest")

    assert result["data"]["status"] == "ok"
    assert result["data"]["exchange"] == "UPCOM"
    assert result["data"]["universe_tier"] == "B"
    assert result["data"]["job_id"].startswith("on_demand:WATCHTEST:")
    with get_conn() as conn:
        assert "WATCHTEST" in get_watchlist(conn)
        assert conn.execute("SELECT requested_by FROM jobs WHERE ticker = 'WATCHTEST'").fetchone()[0] == "watch:alice"


def test_watchlists_are_per_user(as_user):
    as_user("alice")
    watch_ticker_tool("WATCHTEST")

    assert [i["ticker"] for i in list_watchlist_tool()["data"]["items"]] == ["WATCHTEST"]
    as_user("bob")
    assert list_watchlist_tool()["data"]["items"] == []
    assert unwatch_ticker_tool("WATCHTEST")["data"]["status"] == "not_found"

    as_user("alice")
    assert unwatch_ticker_tool("WATCHTEST")["data"]["status"] == "ok"
    assert list_watchlist_tool()["data"]["items"] == []
    with get_conn() as conn:
        assert "WATCHTEST" not in get_watchlist(conn)


def test_watch_rejects_unlisted_ticker(as_user):
    as_user("alice")
    result = watch_ticker_tool("NOPE")

    assert result["data"]["status"] == "not_found"
    assert result["warnings"]


def test_shared_chat_has_no_personal_scope(monkeypatch):
    monkeypatch.delenv("VNMCP_USER_ID", raising=False)

    for result in (watch_ticker_tool("WATCHTEST"), unwatch_ticker_tool("WATCHTEST"), list_watchlist_tool(),
                   set_position_tool("WATCHTEST", 10.0), clear_position_tool("WATCHTEST")):
        assert result["data"]["status"] == "no_personal_scope"
        assert result["warnings"]
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM watchlist_extra WHERE ticker = 'WATCHTEST'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs WHERE ticker = 'WATCHTEST'").fetchone()[0] == 0


def test_list_watchlist_shows_the_holders_label(as_user, tmp_path):
    snap = tmp_path / "s.json"
    snap.write_text(json.dumps({"action_label": "watch", "composite_score": 60, "confidence": 0.7}))

    as_user("bob")
    watch_ticker_tool("WATCHTEST")
    as_user("alice")
    watch_ticker_tool("WATCHTEST")
    set_position_tool("WATCHTEST", avg_cost=10.0)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)"
            " VALUES ('watch-run', 'on_demand', ARRAY['WATCHTEST'], 'long', 'quick', now(), %s, '[]')", (str(snap),),
        )
        conn.execute(
            "INSERT INTO predictions (run_id, source, ticker, trigger, action_label, signal_type, confidence)"
            " VALUES ('watch-run', 'on_demand', 'WATCHTEST', 'first', 'watch', 'mixed', 0.7)"
        )

    alice = list_watchlist_tool()["data"]["items"][0]
    as_user("bob")
    bob = list_watchlist_tool()["data"]["items"][0]
    assert (alice["action_label"], alice["holding_state"]) == ("hold", "holding")
    assert (bob["action_label"], bob["holding_state"]) == ("watch", "unknown")
