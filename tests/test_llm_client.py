import pytest

from llm.client import LLMCallError, call_role
from llm.config import ModelsConfig
from llm.providers import ChatResult
from tests.conftest import insert_ticker


class FakeChatClient:
    """Fake StructuredChatClient: scripted per-call results/exceptions, in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _result(tool_input, input_tokens=100, output_tokens=50, cached_tokens=0):
    return ChatResult(tool_input=tool_input, input_tokens=input_tokens, output_tokens=output_tokens, cached_tokens=cached_tokens)


@pytest.fixture
def run_row(db_conn):
    insert_ticker(db_conn, "LLMTEST")
    db_conn.execute(
        """
        INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)
        VALUES ('llmtest-run', 'on_demand', ARRAY['LLMTEST'], 'long', 'quick', now(), 'snapshots/x.json')
        """
    )
    db_conn.commit()
    yield "llmtest-run"
    db_conn.execute("DELETE FROM llm_calls WHERE run_id = 'llmtest-run'")
    db_conn.execute("DELETE FROM runs WHERE run_id = 'llmtest-run'")
    db_conn.execute("DELETE FROM tickers WHERE ticker = 'LLMTEST'")
    db_conn.commit()


def test_call_role_returns_tool_input_and_logs_llm_call(db_conn, run_row):
    # synthesis_daily's primary model is deepseek (cost-optimization default).
    cfg = ModelsConfig.load()
    deepseek = FakeChatClient([_result({"ok": True}, cached_tokens=20)])
    result = call_role(cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row, clients={"deepseek": deepseek})
    assert result.output == {"ok": True}
    assert result.provider == "deepseek"
    assert result.schema_valid is True
    assert result.cost_usd > 0

    row = db_conn.execute(
        "SELECT role, schema_valid, input_tokens, output_tokens, cached_tokens FROM llm_calls WHERE run_id = %s", (run_row,)
    ).fetchone()
    assert row == ("synthesis_daily", True, 100, 50, 20)


def test_call_role_falls_back_to_claude_on_primary_error(db_conn, run_row):
    cfg = ModelsConfig.load()
    deepseek = FakeChatClient([RuntimeError("503 overloaded")])
    sonnet = FakeChatClient([_result({"ok": True})])
    result = call_role(
        cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row,
        clients={"deepseek": deepseek, "anthropic": sonnet},
    )
    assert result.output == {"ok": True}
    assert result.provider == "anthropic"

    rows = db_conn.execute(
        "SELECT model_id, schema_valid, error FROM llm_calls WHERE run_id = %s ORDER BY id", (run_row,)
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] is False and rows[0][2] is not None  # failed DeepSeek attempt logged with error
    assert rows[1][1] is True and rows[1][2] is None        # successful Claude fallback logged clean


def test_call_role_skips_fallback_models_with_no_configured_provider(db_conn, run_row, monkeypatch):
    # ANTHROPIC_API_KEY/OPENAI_API_KEY/GROK_API_KEY are all unset here, so the
    # fallback chain [sonnet, gpt, grok, haiku] must skip past the three
    # Claude/GPT/Grok providers it can't reach (sonnet and haiku both need
    # ANTHROPIC_API_KEY) straight to... nothing, since haiku also needs
    # anthropic — so only deepseek itself is reachable.
    for var in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROK_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    cfg = ModelsConfig.load()
    deepseek = FakeChatClient([_result({"ok": True})])
    result = call_role(
        cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row,
        clients={"deepseek": deepseek},
    )
    assert result.output == {"ok": True}
    assert result.provider == "deepseek"
    assert len(deepseek.calls) == 1  # primary succeeded, no fallback needed

    rows = db_conn.execute("SELECT model_id, error FROM llm_calls WHERE run_id = %s ORDER BY id", (run_row,)).fetchall()
    assert len(rows) == 1


def test_call_role_skips_unconfigured_fallbacks_after_primary_fails(db_conn, run_row, monkeypatch):
    for var in ["OPENAI_API_KEY", "GROK_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    cfg = ModelsConfig.load()
    deepseek = FakeChatClient([RuntimeError("down")])
    anthropic = FakeChatClient([_result({"ok": True}), _result({"ok": True})])  # sonnet then haiku if needed
    result = call_role(
        cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row,
        clients={"deepseek": deepseek, "anthropic": anthropic},
    )
    assert result.provider == "anthropic"  # sonnet (1st anthropic fallback) succeeds immediately
    assert len(anthropic.calls) == 1

    rows = db_conn.execute("SELECT model_id, error FROM llm_calls WHERE run_id = %s ORDER BY id", (run_row,)).fetchall()
    # deepseek failure, sonnet success — gpt/grok never attempted because sonnet succeeded first
    assert len(rows) == 2


def test_call_role_raises_when_no_provider_configured_at_all(db_conn, run_row, monkeypatch):
    for var in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GROK_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    cfg = ModelsConfig.load()
    with pytest.raises(LLMCallError):
        call_role(cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row)

    rows = db_conn.execute("SELECT error FROM llm_calls WHERE run_id = %s", (run_row,)).fetchall()
    assert len(rows) >= 1
    assert all("not configured" in r[0] for r in rows)


def test_call_role_raises_when_all_models_fail(db_conn, run_row):
    cfg = ModelsConfig.load()
    deepseek = FakeChatClient([RuntimeError("down")])
    anthropic = FakeChatClient([RuntimeError("down too")])
    with pytest.raises(LLMCallError):
        call_role(
            cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row,
            clients={"deepseek": deepseek, "anthropic": anthropic},
        )


def test_call_role_raises_without_consuming_fallback_when_tool_not_called(db_conn, run_row):
    cfg = ModelsConfig.load()
    deepseek = FakeChatClient([_result(None)])
    anthropic = FakeChatClient([_result({"ok": True})])
    with pytest.raises(LLMCallError):
        call_role(
            cfg, db_conn, "synthesis_daily", "sys", "user", run_id=run_row,
            clients={"deepseek": deepseek, "anthropic": anthropic},
        )
    assert len(anthropic.calls) == 0  # prompting issue, not transient — must not burn fallbacks
