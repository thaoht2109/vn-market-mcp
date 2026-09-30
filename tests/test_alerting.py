import httpx
import pytest

from ops.alerting import send_ops_alert


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


def test_send_ops_alert_returns_false_on_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_ALERT_CHAT_ID", "-100123")

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert send_ops_alert("job failed") is False
