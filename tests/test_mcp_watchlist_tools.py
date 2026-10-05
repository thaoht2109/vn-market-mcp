import pytest

from db.connection import get_conn
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
        conn.execute("DELETE FROM tickers WHERE ticker = 'WATCHTEST'")


def test_watch_registers_new_ticker_queues_a_run_and_joins_the_schedule():
    result = watch_ticker_tool("watchtest", declared_by="alice")

    assert result["data"]["status"] == "ok"
    assert result["data"]["exchange"] == "UPCOM"
    assert result["data"]["universe_tier"] == "B"
    assert result["data"]["job_id"].startswith("on_demand:WATCHTEST:")
    with get_conn() as conn:
        assert "WATCHTEST" in get_watchlist(conn)


def test_watchlists_are_per_user():
    watch_ticker_tool("WATCHTEST", declared_by="alice")

    assert [i["ticker"] for i in list_watchlist_tool("alice")["data"]["items"]] == ["WATCHTEST"]
    assert list_watchlist_tool("bob")["data"]["items"] == []
    assert unwatch_ticker_tool("WATCHTEST", declared_by="bob")["data"]["status"] == "not_found"

    assert unwatch_ticker_tool("WATCHTEST", declared_by="alice")["data"]["status"] == "ok"
    assert list_watchlist_tool("alice")["data"]["items"] == []
    with get_conn() as conn:
        assert "WATCHTEST" not in get_watchlist(conn)


def test_watch_rejects_unlisted_ticker():
    result = watch_ticker_tool("NOPE", declared_by="alice")

    assert result["data"]["status"] == "not_found"
    assert result["warnings"]


def test_pinned_user_overrides_declared_by(monkeypatch):
    monkeypatch.setenv("VNMCP_USER_ID", "alice")
    watch_ticker_tool("WATCHTEST", declared_by="bob")  # the model claims to be bob: ignored

    assert [i["ticker"] for i in list_watchlist_tool()["data"]["items"]] == ["WATCHTEST"]
    monkeypatch.delenv("VNMCP_USER_ID")
    assert list_watchlist_tool("bob")["data"]["items"] == []
    assert [i["ticker"] for i in list_watchlist_tool("alice")["data"]["items"]] == ["WATCHTEST"]


def test_list_watchlist_shows_the_holders_label(tmp_path):
    import json

    from mcp_server.tools.positions import set_position_tool

    snap = tmp_path / "s.json"
    snap.write_text(json.dumps({"action_label": "watch", "composite_score": 60, "confidence": 0.7}))

    watch_ticker_tool("WATCHTEST", declared_by="alice")
    watch_ticker_tool("WATCHTEST", declared_by="bob")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)"
            " VALUES ('watch-run', 'on_demand', ARRAY['WATCHTEST'], 'long', 'quick', now(), %s, '[]')", (str(snap),),
        )
        conn.execute(
            "INSERT INTO predictions (run_id, source, ticker, trigger, action_label, signal_type, confidence)"
            " VALUES ('watch-run', 'on_demand', 'WATCHTEST', 'first', 'watch', 'mixed', 0.7)"
        )
    set_position_tool("WATCHTEST", avg_cost=10.0, declared_by="alice")
    try:
        alice = list_watchlist_tool("alice")["data"]["items"][0]
        bob = list_watchlist_tool("bob")["data"]["items"][0]
        assert (alice["action_label"], alice["holding_state"]) == ("hold", "holding")
        assert (bob["action_label"], bob["holding_state"]) == ("watch", "unknown")
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM predictions WHERE ticker = 'WATCHTEST'")
            conn.execute("DELETE FROM positions WHERE ticker = 'WATCHTEST'")
            conn.execute("DELETE FROM runs WHERE run_id = 'watch-run'")
