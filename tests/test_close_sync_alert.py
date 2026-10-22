"""20-21/10/2026: close_sync and its 18:00 retry both ended "done" with 36/36 tickers failed, and no one was told."""
import ops.worker as worker
from pipeline.ingest import IngestOutcome


def _run(monkeypatch, job_key, outcomes):
    sent = []
    monkeypatch.setattr(worker, "sync_recent_prices", lambda *a, **k: outcomes)
    monkeypatch.setattr(worker, "check_close", lambda *a, **k: 0)
    monkeypatch.setattr(worker, "send_ops_alert", sent.append)
    monkeypatch.setattr(worker, "VNStockProvider", lambda **k: None)
    worker._run_close_sync(None, job_key)
    return sent


def test_retry_alerts_on_tickers_still_missing(monkeypatch):
    failed = [IngestOutcome("VCB", "error", "name resolution", 0), IngestOutcome("FPT", "ok", None, 3)]
    sent = _run(monkeypatch, "close_sync_retry:MARKET:2026-10-21", failed)
    assert len(sent) == 1 and "VCB" in sent[0] and "FPT" not in sent[0]


def test_first_attempt_and_clean_retry_stay_quiet(monkeypatch):
    failed = [IngestOutcome("VCB", "error", "name resolution", 0)]
    assert _run(monkeypatch, "close_sync:MARKET:2026-10-21", failed) == []  # the 18:00 retry may still fix it
    assert _run(monkeypatch, "close_sync_retry:MARKET:2026-10-21", [IngestOutcome("VCB", "ok", None, 1)]) == []
