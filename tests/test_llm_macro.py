from datetime import datetime, timezone

import pytest

from llm.config import ModelsConfig
from llm.macro import MacroDigestValidationError, run_macro_daily
from llm.providers import ChatResult
from providers.vnstock_provider import NewsItem


class FakeChatClient:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._results.pop(0)


def _result(tool_input):
    return ChatResult(tool_input=tool_input, input_tokens=100, output_tokens=50, cached_tokens=0)


NEWS = [
    NewsItem(
        ticker="VNM", published_at=datetime(2026, 9, 15, tzinfo=timezone.utc), source="HOSE",
        url=None, title="VNM: Fed giữ nguyên lãi suất", summary=None, fetched_at=datetime.now(timezone.utc),
    ),
]

VALID_OUTPUT = {"noteworthy": True, "summary": "Fed giữ nguyên lãi suất, thị trường quốc tế ổn định.", "news_refs": [0]}


def test_run_macro_daily_returns_empty_without_calling_llm_when_no_news(db_conn):
    cfg = ModelsConfig.load()
    client = FakeChatClient([])
    result = run_macro_daily(db_conn, cfg, [], clients={"deepseek": client})
    assert result.noteworthy is False
    assert len(client.calls) == 0


def test_run_macro_daily_accepts_valid_output(db_conn):
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(VALID_OUTPUT)])
    result = run_macro_daily(db_conn, cfg, NEWS, clients={"deepseek": client})
    assert result.noteworthy is True
    assert result.news_refs == [0]


def test_run_macro_daily_rejects_news_ref_outside_input_batch(db_conn):
    bad = {**VALID_OUTPUT, "news_refs": [99]}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(bad)])
    with pytest.raises(MacroDigestValidationError, match="news_refs"):
        run_macro_daily(db_conn, cfg, NEWS, clients={"deepseek": client})


def test_run_macro_daily_rejects_digit_in_summary(db_conn):
    bad = {**VALID_OUTPUT, "summary": "VN-Index tăng 5 điểm hôm nay."}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(bad)])
    with pytest.raises(MacroDigestValidationError, match="digit"):
        run_macro_daily(db_conn, cfg, NEWS, clients={"deepseek": client})
