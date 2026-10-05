from types import SimpleNamespace

import pytest

from llm.batch import BatchNotSupportedError, poll_batch_job, submit_batch_role
from llm.config import ModelsConfig
from tests.conftest import insert_ticker


def _tool_block(input_dict):
    return SimpleNamespace(type="tool_use", name="emit_synthesis_daily", input=input_dict)


def _usage(input_tokens=100, output_tokens=50, cache_read_input_tokens=0):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, cache_read_input_tokens=cache_read_input_tokens)


class FakeBatches:
    def __init__(self, create_response, retrieve_responses, results=None):
        self._create_response = create_response
        self._retrieve_responses = list(retrieve_responses)
        self._results = results or []
        self.create_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._create_response

    def retrieve(self, batch_id):
        return self._retrieve_responses.pop(0)

    def results(self, batch_id):
        return self._results


class FakeAnthropicClient:
    def __init__(self, batches: FakeBatches):
        self.messages = SimpleNamespace(batches=batches)


@pytest.fixture
def run_rows(db_conn):
    insert_ticker(db_conn, "BATCHA")
    insert_ticker(db_conn, "BATCHB")
    for t in ["BATCHA", "BATCHB"]:
        db_conn.execute(
            "INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref)"
            " VALUES (%s, 'on_demand', ARRAY[%s], 'long', 'quick', now(), 'snapshots/x.json')",
            (f"run-{t}", t),
        )
    db_conn.commit()
    yield ["run-BATCHA", "run-BATCHB"]
    db_conn.execute("DELETE FROM llm_calls WHERE run_id IN ('run-BATCHA', 'run-BATCHB')")
    db_conn.execute("DELETE FROM llm_batch_items WHERE run_id IN ('run-BATCHA', 'run-BATCHB')")
    db_conn.execute("DELETE FROM llm_batch_jobs WHERE provider_batch_id LIKE 'batch_test%'")
    db_conn.execute("DELETE FROM runs WHERE run_id IN ('run-BATCHA', 'run-BATCHB')")
    db_conn.execute("DELETE FROM tickers WHERE ticker IN ('BATCHA', 'BATCHB')")
    db_conn.commit()


def test_submit_batch_role_rejects_deepseek_primary_model(db_conn):
    # synthesis_daily's primary model is deepseek (cost-optimization default,
    # see config/models.yaml) — deepseek has no batch API at all.
    cfg = ModelsConfig.load()
    with pytest.raises(BatchNotSupportedError, match="deepseek"):
        submit_batch_role(object(), db_conn, cfg, "synthesis_daily", "sys", [("c1", "content", None)])


def test_submit_batch_role_writes_job_and_items(db_conn, run_rows):
    cfg = ModelsConfig.load()
    batches = FakeBatches(
        create_response=SimpleNamespace(id="batch_test_1", processing_status="in_progress"),
        retrieve_responses=[],
    )
    client = FakeAnthropicClient(batches)
    items = [("c-a", "content a", run_rows[0]), ("c-b", "content b", run_rows[1])]
    submission = submit_batch_role(client, db_conn, cfg, "bull_advocate", "sys", items, model_key="sonnet")

    assert submission.item_count == 2
    assert submission.provider_batch_id == "batch_test_1"
    assert len(batches.create_calls) == 1
    assert len(batches.create_calls[0]["requests"]) == 2

    job = db_conn.execute(
        "SELECT provider, role, model_key, status, item_count FROM llm_batch_jobs WHERE id = %s", (submission.batch_job_id,)
    ).fetchone()
    assert job == ("anthropic", "bull_advocate", "sonnet", "in_progress", 2)

    item_rows = db_conn.execute(
        "SELECT custom_id, run_id, status FROM llm_batch_items WHERE batch_job_id = %s ORDER BY custom_id",
        (submission.batch_job_id,),
    ).fetchall()
    assert item_rows == [("c-a", run_rows[0], "pending"), ("c-b", run_rows[1], "pending")]


def test_poll_batch_job_pulls_results_when_completed(db_conn, run_rows):
    cfg = ModelsConfig.load()
    batches = FakeBatches(
        create_response=SimpleNamespace(id="batch_test_2", processing_status="in_progress"),
        retrieve_responses=[SimpleNamespace(processing_status="ended")],
        results=[
            SimpleNamespace(
                custom_id="c-a",
                result=SimpleNamespace(
                    type="succeeded",
                    message=SimpleNamespace(content=[_tool_block({"ok": True})], usage=_usage()),
                ),
            ),
            SimpleNamespace(
                custom_id="c-b",
                result=SimpleNamespace(type="errored", error="rate_limit"),
            ),
        ],
    )
    client = FakeAnthropicClient(batches)
    items = [("c-a", "content a", run_rows[0]), ("c-b", "content b", run_rows[1])]
    submission = submit_batch_role(client, db_conn, cfg, "bull_advocate", "sys", items, model_key="sonnet")

    status = poll_batch_job(client, db_conn, cfg, submission.batch_job_id)
    assert status == "ended"

    items_after = db_conn.execute(
        "SELECT custom_id, status, output, error FROM llm_batch_items WHERE batch_job_id = %s ORDER BY custom_id",
        (submission.batch_job_id,),
    ).fetchall()
    assert items_after[0][1] == "succeeded"
    assert items_after[0][2] == {"ok": True}
    assert items_after[1][1] == "errored"
    assert items_after[1][3] is not None

    llm_call = db_conn.execute(
        "SELECT run_id, role, cost_usd, schema_valid FROM llm_calls WHERE run_id = %s", (run_rows[0],)
    ).fetchone()
    assert llm_call[1] == "bull_advocate"
    assert llm_call[3] is True
    assert llm_call[2] > 0


def test_poll_batch_job_is_idempotent_once_completed(db_conn, run_rows):
    cfg = ModelsConfig.load()
    batches = FakeBatches(
        create_response=SimpleNamespace(id="batch_test_3", processing_status="in_progress"),
        retrieve_responses=[SimpleNamespace(processing_status="ended"), SimpleNamespace(processing_status="ended")],
        results=[
            SimpleNamespace(
                custom_id="c-a",
                result=SimpleNamespace(type="succeeded", message=SimpleNamespace(content=[_tool_block({"ok": True})], usage=_usage())),
            ),
        ],
    )
    client = FakeAnthropicClient(batches)
    submission = submit_batch_role(
        client, db_conn, cfg, "bull_advocate", "sys", [("c-a", "content a", run_rows[0])], model_key="sonnet",
    )

    poll_batch_job(client, db_conn, cfg, submission.batch_job_id)
    poll_batch_job(client, db_conn, cfg, submission.batch_job_id)  # second poll must not duplicate llm_calls

    count = db_conn.execute("SELECT count(*) FROM llm_calls WHERE run_id = %s", (run_rows[0],)).fetchone()[0]
    assert count == 1
