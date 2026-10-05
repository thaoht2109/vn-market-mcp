import httpx
import openai
import pytest

from llm.providers import ProviderNotConfiguredError, _OpenAICompatClient, available_providers, make_client, provider_available


def test_provider_available_false_when_key_unset(monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    assert provider_available("grok") is False


def test_provider_available_true_when_key_set(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "xai-test-key")
    assert provider_available("grok") is True


def test_ollama_available_without_key_since_it_requires_none(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert provider_available("ollama") is True


def test_available_providers_reflects_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    names = available_providers()
    assert "anthropic" not in names
    assert "openai" not in names
    assert "ollama" in names  # no key required

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert "anthropic" in available_providers()


def test_make_client_raises_for_unconfigured_provider(monkeypatch):
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    try:
        make_client("grok")
        assert False, "expected ProviderNotConfiguredError"
    except ProviderNotConfiguredError:
        pass


def test_make_client_raises_for_unknown_provider_name():
    try:
        make_client("not-a-real-provider")
        assert False, "expected ProviderNotConfiguredError"
    except ProviderNotConfiguredError:
        pass


class _FakeOpenAISDKClient:
    """Mimics openai.OpenAI's shape just enough to raise APIStatusError the
    way the real SDK does (it needs a fake httpx.Response for the status_code)."""

    def __init__(self, status_code: int):
        self._status_code = status_code
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        response = httpx.Response(self._status_code, request=request)
        raise openai.APIStatusError("boom", response=response, body=None)


def test_create_alerts_on_402_payment_required(monkeypatch):
    alerts = []
    monkeypatch.setattr("ops.alerting.send_ops_alert", lambda text: alerts.append(text) or True)
    client = _OpenAICompatClient(_FakeOpenAISDKClient(402), provider_name="deepseek")

    with pytest.raises(openai.APIStatusError):
        client.create(model="deepseek-chat", system="s", user_content="u", tool_name="emit_x", output_schema={}, effort="low")

    assert len(alerts) == 1
    assert "deepseek" in alerts[0] and "402" in alerts[0]


def test_create_does_not_alert_on_other_status_codes(monkeypatch):
    alerts = []
    monkeypatch.setattr("ops.alerting.send_ops_alert", lambda text: alerts.append(text) or True)
    client = _OpenAICompatClient(_FakeOpenAISDKClient(500), provider_name="deepseek")

    with pytest.raises(openai.APIStatusError):
        client.create(model="deepseek-chat", system="s", user_content="u", tool_name="emit_x", output_schema={}, effort="low")

    assert alerts == []
