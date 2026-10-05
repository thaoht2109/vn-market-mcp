import httpx
import pytest

from ops import alerting
from ops.alerting import send_ops_alert, send_user_message


def test_user_bot_token_added_after_start_is_used_without_restart(db_conn, monkeypatch, tmp_path):
    users_env = tmp_path / "users.env"
    monkeypatch.setattr(alerting, "USERS_ENV", users_env)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN_LAN", raising=False)
    db_conn.execute("INSERT INTO users (user_id, chat_id, bot_token_env) VALUES ('lan1', '42', 'TELEGRAM_BOT_TOKEN_LAN')")
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: sent.append(url) or httpx.Response(
        200, request=httpx.Request("POST", url)))

    assert send_user_message(db_conn, "lan1", "x") is False  # no token yet: skipped, not sent
    users_env.write_text("# comment\nTELEGRAM_BOT_TOKEN_LAN=lan-token\n")  # ops/add_user.sh, worker still running
    assert send_user_message(db_conn, "lan1", "x") is True
    assert sent == ["https://api.telegram.org/botlan-token/sendMessage"]
    assert send_user_message(db_conn, "nobody", "x") is None


def test_send_ops_alert_skips_when_config_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)
    assert send_ops_alert("test") is False


def test_send_ops_alert_posts_to_telegram_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "-100123")

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert send_ops_alert("job failed") is True
    assert captured["url"] == "https://api.telegram.org/botfake-token/sendMessage"
    assert captured["json"] == {"chat_id": "-100123", "text": "job failed"}


def test_send_ops_alert_splits_long_text_into_multiple_messages(monkeypatch):
    # Regression: a real Synthesis report routinely runs 4000-5000+ chars,
    # and Telegram's sendMessage rejects anything over 4096 with a 400 —
    # observed live sending a full VCB report (4813 chars) before this fix.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "-100123")

    sent_texts = []

    def fake_post(url, json, timeout):
        sent_texts.append(json["text"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    long_text = "a" * 9000
    assert send_ops_alert(long_text) is True
    assert len(sent_texts) == 3
    assert "".join(sent_texts) == long_text
    assert all(len(chunk) <= 4000 for chunk in sent_texts)


def test_send_ops_alert_returns_false_on_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "-100123")

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert send_ops_alert("job failed") is False
