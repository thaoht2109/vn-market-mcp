from datetime import date, datetime, timezone

import pytest

from llm.config import ModelsConfig
from llm.news_digest import NewsDigestValidationError, run_news_digest
from llm.providers import ChatResult
from providers.vnstock_provider import NewsItem
from tests.conftest import insert_ticker


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
        ticker="NEWSTEST", published_at=datetime(2026, 9, 15, tzinfo=timezone.utc), source="HOSE",
        url=None, title="NEWSTEST: Nghị quyết HĐQT", summary=None, fetched_at=datetime.now(timezone.utc),
    ),
    NewsItem(
        ticker="NEWSTEST", published_at=datetime(2026, 9, 16, tzinfo=timezone.utc), source="HOSE",
        url=None, title="NEWSTEST: KQKD quý 3", summary=None, fetched_at=datetime.now(timezone.utc),
    ),
]

VALID_OUTPUT = {
    "items": [
        {
            "news_ref": 0, "ticker": "NEWSTEST", "event_type": "nhan_su", "source_tier": "cbtt",
            "sentiment": "neutral", "impact_horizon": {"short_term": "neutral", "medium_term": "neutral", "long_term": "no_info"},
            "thesis_relevance": "khong_lien_quan", "confidence": 0.6,
        },
    ]
}


@pytest.fixture
def run_row(db_conn):
    insert_ticker(db_conn, "NEWSTEST")
    db_conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)"
        " VALUES ('news-run', 'on_demand', ARRAY['NEWSTEST'], 'long', 'quick', now(), 'snapshots/x.json')"
    )
    db_conn.commit()
    yield "news-run"
    db_conn.execute("DELETE FROM llm_calls WHERE run_id = 'news-run'")
    db_conn.execute("DELETE FROM runs WHERE run_id = 'news-run'")
    db_conn.execute("DELETE FROM tickers WHERE ticker = 'NEWSTEST'")
    db_conn.commit()


def test_run_news_digest_returns_empty_without_calling_llm_when_no_news(db_conn, run_row):
    cfg = ModelsConfig.load()
    client = FakeChatClient([])
    result = run_news_digest(db_conn, cfg, [], run_row, clients={"deepseek": client})
    assert result.items == []
    assert len(client.calls) == 0


def test_run_news_digest_accepts_valid_output(db_conn, run_row):
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(VALID_OUTPUT)])
    result = run_news_digest(db_conn, cfg, NEWS, run_row, clients={"deepseek": client})
    assert len(result.items) == 1
    assert result.items[0].news_ref == 0
    assert result.items[0].event_type == "nhan_su"


def test_run_news_digest_rejects_news_ref_outside_input_batch(db_conn, run_row):
    bad = {"items": [{**VALID_OUTPUT["items"][0], "news_ref": 99}]}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(bad)])
    with pytest.raises(NewsDigestValidationError, match="news_ref"):
        run_news_digest(db_conn, cfg, NEWS, run_row, clients={"deepseek": client})


def test_run_news_digest_coerces_numeric_string_news_ref(db_conn, run_row):
    # Observed with real DeepSeek output: news_ref came back as "0" (string)
    # despite the schema declaring integer — must still resolve correctly.
    output = {"items": [{**VALID_OUTPUT["items"][0], "news_ref": "0"}]}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(output)])
    result = run_news_digest(db_conn, cfg, NEWS, run_row, clients={"deepseek": client})
    assert result.items[0].news_ref == 0


def test_run_news_digest_rejects_non_numeric_news_ref(db_conn, run_row):
    bad = {"items": [{**VALID_OUTPUT["items"][0], "news_ref": "not-a-number"}]}
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result(bad)])
    with pytest.raises(NewsDigestValidationError, match="news_ref"):
        run_news_digest(db_conn, cfg, NEWS, run_row, clients={"deepseek": client})
