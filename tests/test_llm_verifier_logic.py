import pytest

from llm.bull_bear import AdvocateResult
from llm.config import ModelsConfig
from llm.providers import ChatResult
from llm.verifier_logic import VerifierPrecheckError, run_verifier_logic
from tests.conftest import insert_ticker

SNAPSHOT = {"ticker": "VERTEST", "technical": {"rsi14": 61.2}, "fundamental": {"roe": 18.5}}


class FakeChatClient:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._results.pop(0)


def _result(tool_input):
    return ChatResult(tool_input=tool_input, input_tokens=100, output_tokens=50, cached_tokens=0)


def _advocate_result(evidence_ref="technical.rsi14"):
    return AdvocateResult(
        arguments=[{"text": "lập luận", "evidence_ref": evidence_ref}],
        key_risk_to_thesis="rủi ro", llm_meta=None,
    )


@pytest.fixture
def run_row(db_conn):
    insert_ticker(db_conn, "VERTEST")
    db_conn.execute(
        "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)"
        " VALUES ('ver-run', 'on_demand', ARRAY['VERTEST'], 'long', 'quick', now(), 'snapshots/x.json')"
    )
    db_conn.commit()
    yield "ver-run"
    db_conn.execute("DELETE FROM llm_calls WHERE run_id = 'ver-run'")
    db_conn.execute("DELETE FROM runs WHERE run_id = 'ver-run'")
    db_conn.execute("DELETE FROM tickers WHERE ticker = 'VERTEST'")
    db_conn.commit()


def test_run_verifier_logic_calls_llm_after_precheck_passes(db_conn, run_row):
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result({"bull_logic_valid": True, "bear_logic_valid": True, "issues": []})])
    result = run_verifier_logic(
        db_conn, cfg, SNAPSHOT, _advocate_result(), _advocate_result(), run_row, clients={"deepseek": client},
    )
    assert result.bull_logic_valid is True
    assert len(client.calls) == 1


def test_run_verifier_logic_precheck_rejects_bad_evidence_ref_without_calling_llm(db_conn, run_row):
    cfg = ModelsConfig.load()
    client = FakeChatClient([_result({"bull_logic_valid": True, "bear_logic_valid": True, "issues": []})])
    with pytest.raises(VerifierPrecheckError):
        run_verifier_logic(
            db_conn, cfg, SNAPSHOT, _advocate_result(evidence_ref="technical.made_up"), _advocate_result(),
            run_row, clients={"deepseek": client},
        )
    assert len(client.calls) == 0  # precheck fails before any LLM call is made
