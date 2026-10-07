# vn-market-mcp MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing Phase 0+1 pipeline as a standard MCP server (stdio transport, official `mcp` Python SDK) so Hermes Agent (Nous Research) — or any MCP-speaking client — can call `run_analysis` and read back stored results, without re-implementing any pipeline logic.

**Architecture:** A single new package `mcp_server/` wraps eight thin tool handlers around code that already exists in `pipeline/`, `quality/`, and `db/`. Every handler opens its own short-lived `psycopg` connection scoped to the least-privileged role that can do the job (`mcp_ro` for the five read-only tools, `pipeline_rw` for `run_analysis`/`set_position`/`clear_position`). No handler calls the Anthropic API. The server registers tools with `mcp.server.fastmcp.FastMCP`, validates inputs itself (the SDK does not enforce Python type hints at runtime), and returns JSON matching the spec's "khung dữ liệu chung" (`as_of`, `sources`, `warnings` always present).

**Tech Stack:** Python 3.11, `mcp==2.2.0` (official Anthropic MCP SDK, stdio transport), `psycopg[binary]==3.2.3` (already a dependency), `pytest` + `pytest-asyncio` for testing the server via the SDK's in-memory client session.

**Spec:** `/home/anm/0_Projects/thaoht/99.CK/vn-trading-agent-plan_final.md` — §3 ("Ai điều phối", line 69), §4.1 (line 90-100), §5.1 (line 331-348), §6 (line 673-688, khung dữ liệu chung), §7.1 (line 703-709, role-based grants).

## Global Constraints

- No tool handler may call the Anthropic/Claude API (spec §3 phân vai: LLM roles are explicitly out of scope for this plan — deferred to a later phase).
- Every tool handler is a thin wrapper: it converts MCP tool input to the existing Python function's arguments, calls that function, converts the return value to the spec's data envelope. No business logic is re-implemented in `mcp_server/`.
- Every JSON response includes `as_of` (ISO 8601 string, UTC) and `sources` (list of strings) per spec §6 (line 688: "as_of và sources là bắt buộc"). Missing/unavailable data must never be silently omitted — degrade to `null` with a `warnings` entry, never guess (spec Nguyên tắc 3, fail-closed).
- Read-only tools (`get_snapshot`, `query_history`, `explain_run`, `list_predictions`, `get_stats`) connect using the `mcp_ro` Postgres role (SELECT-only, already provisioned by `db/setup_roles.py`). Write tools (`run_analysis`, `set_position`, `clear_position`) connect using `pipeline_rw`.
- `run_analysis` in this plan calls the existing **synchronous** `pipeline.run_analysis.run_analysis()` function directly and blocks until it returns — there is no job queue or background worker yet (spec §3 line 69 describes a `job_id`-returning async worker, which is out of scope for Phase 0+1 and not built here). This is a known, documented gap — Task 2's docstring and the README must say so explicitly, so nobody assumes fire-and-forget semantics exist.
- `config/vn-rules.yaml` is read-only from every tool — no tool may write to it (spec SOUL.md §5.3 rule 7).
- Ponytail: use the SDK's built-in decorator-based tool registration (`@mcp.tool()`) — do not hand-roll JSON-RPC framing, argument schemas, or a custom protocol layer.

## Review Focus

- **Unknown ticker passed to any read tool** (`get_snapshot`, `query_history`, `list_predictions` with a ticker that was never ingested) — spec's fail-closed principle implies this must return an empty/`not_found` result with a clear `warnings` entry, not a raw SQL error or an empty 200 that looks like "no data exists for this well-known ticker." Covered in Task 4 and Task 5.
- **`run_analysis` tool called while the underlying pipeline raises** (e.g. `UnknownTickerError`, `IngestBatchError`, `InsufficientHistoryError`) — the MCP tool must translate this into a structured error result (not let an unhandled Python exception cross the MCP boundary as an opaque JSON-RPC internal error), and must still emit `ops/alerting.log_event`/`send_ops_alert` the same way the CLI entrypoint does, since this is now a second entrypoint into the same pipeline. Covered in Task 2.
- **`get_stats` with zero runs in the database** (a brand-new install before anyone has run analysis) — must return zeroed counts and empty breakdowns, not divide-by-zero or an empty list masquerading as "the feature is broken." Covered in Task 6.
- **`explain_run` with a `run_id` that does not exist** — must return a `not_found` status distinguishable from "found, but the run produced no predictions," matching the same not-found contract used elsewhere. Covered in Task 5.
- **Read tools receiving a `mcp_ro` connection that lacks permission to run a write** — a bug that accidentally tries to write through a read tool must fail loudly at the database permission layer (proving the role separation is real), not silently no-op. Covered in Task 1 (a permission-boundary test using the real `mcp_ro` role against a table `mcp_ro` cannot write to).

---

## File Structure

```
vn-market-mcp/
  mcp_server/
    __init__.py
    connection.py      # get_ro_conn() / get_rw_conn() context managers scoped to mcp_ro / pipeline_rw
    envelope.py         # build_envelope() helper implementing spec §6's common data frame
    server.py           # FastMCP instance + tool registration (imports handlers from tools/)
    tools/
      __init__.py
      run_analysis.py   # run_analysis tool handler
      snapshot.py        # get_snapshot tool handler
      history.py         # query_history tool handler
      explain.py          # explain_run tool handler
      predictions.py       # list_predictions tool handler
      stats.py              # get_stats tool handler
      positions.py           # set_position / clear_position tool handlers
  tests/
    test_mcp_connection.py
    test_mcp_envelope.py
    test_mcp_run_analysis_tool.py
    test_mcp_snapshot_tool.py
    test_mcp_history_tool.py
    test_mcp_explain_tool.py
    test_mcp_predictions_tool.py
    test_mcp_stats_tool.py
    test_mcp_positions_tools.py
    test_mcp_server_integration.py   # boots the real FastMCP server, calls tools via SDK's in-memory client
  requirements.txt      # + mcp==2.2.0, pytest-asyncio==0.24.0
  README.txt            # + section 13: running the MCP server, connecting Hermes
  .env.example           # + MCP_RO_DATABASE_URL, PIPELINE_RW_DATABASE_URL (see Task 1)
```

**Why split by tool, one file each:** each tool file is independently reviewable and the failure surface of one tool (e.g. a bad SQL query in `history.py`) cannot hide inside a 400-line `server.py`. `server.py` stays a thin registration list — if it grows unwieldy from having eight `@mcp.tool()` decorators, that's expected and fine at this size (8 tools, ~10 lines of registration each).

---

## Task 1: Role-scoped DB connections + data envelope helper

**Files:**
- Create: `vn-market-mcp/mcp_server/__init__.py` (empty)
- Create: `vn-market-mcp/mcp_server/connection.py`
- Create: `vn-market-mcp/mcp_server/envelope.py`
- Modify: `vn-market-mcp/.env.example`
- Test: `vn-market-mcp/tests/test_mcp_connection.py`
- Test: `vn-market-mcp/tests/test_mcp_envelope.py`

**Interfaces:**
- Consumes: nothing (foundation task)
- Produces:
  - `mcp_server.connection.get_ro_conn() -> ContextManager[psycopg.Connection]` — connects using `MCP_RO_DATABASE_URL`, no auto-commit needed (read-only role can't write), rolls back and closes on exit like `db.connection.get_conn`.
  - `mcp_server.connection.get_rw_conn() -> ContextManager[psycopg.Connection]` — connects using `PIPELINE_RW_DATABASE_URL`, commits on clean exit, rolls back on exception (same shape as `db.connection.get_conn`).
  - `mcp_server.envelope.build_envelope(data: dict, sources: list[str], as_of: datetime, warnings: list[str] | None = None) -> dict` — returns `{"as_of": <iso8601 str>, "sources": sources, "data": data, "warnings": warnings or []}`.

The existing `db/connection.py`'s `get_conn()` reads a single `DATABASE_URL` (the admin user, `vnmcp_admin`) — that's correct for migrations/setup scripts but wrong for the MCP server, which must connect as the restricted `mcp_ro`/`pipeline_rw` roles so a bug in a "read-only" tool can't write. `db/setup_roles.py` already creates these two Postgres roles with `MCP_RO_PASSWORD`/`PIPELINE_RW_PASSWORD` (read from env — see `db/setup_roles.py:5-9`); this task adds the two full connection-string env vars the MCP server needs to actually connect as them.

- [ ] **Step 1: Write the failing connection test**

```python
# vn-market-mcp/tests/test_mcp_connection.py
import os
import pytest
import psycopg

from mcp_server.connection import get_ro_conn, get_rw_conn


def test_get_ro_conn_can_select():
    with get_ro_conn() as conn:
        row = conn.execute("SELECT 1").fetchone()
        assert row == (1,)


def test_get_ro_conn_cannot_insert():
    with get_ro_conn() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO tickers (ticker, exchange) VALUES ('ZZZTEST', 'HOSE')"
            )
            conn.execute("SELECT 1")  # force flush of the failed statement


def test_get_rw_conn_can_insert_and_rolls_back_on_error():
    with pytest.raises(RuntimeError):
        with get_rw_conn() as conn:
            conn.execute(
                "INSERT INTO tickers (ticker, exchange) VALUES ('ZZZTEST2', 'HOSE')"
            )
            raise RuntimeError("boom")

    with get_ro_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tickers WHERE ticker = 'ZZZTEST2'"
        ).fetchone()
        assert row is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_connection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server'`

- [ ] **Step 3: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/__init__.py
```//empty file

```python
# vn-market-mcp/mcp_server/connection.py
import os
from contextlib import contextmanager

import psycopg


@contextmanager
def get_ro_conn():
    conn = psycopg.connect(os.environ["MCP_RO_DATABASE_URL"])
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_rw_conn():
    conn = psycopg.connect(os.environ["PIPELINE_RW_DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: Add the two connection-string env vars**

In `vn-market-mcp/.env.example`, add after the existing `RETENTION_JOB_PASSWORD` line:

```
MCP_RO_DATABASE_URL=postgresql://mcp_ro:changeme@localhost:55432/vnmcp
PIPELINE_RW_DATABASE_URL=postgresql://pipeline_rw:changeme@localhost:55432/vnmcp
```

Note in a comment above them (in the same file) that the passwords here must match `MCP_RO_PASSWORD`/`PIPELINE_RW_PASSWORD` used by `db/setup_roles.py`, since that script is what actually sets the role's Postgres password.

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_connection.py -v`
Expected: PASS (all 3 tests) — requires Postgres running (`docker compose up -d postgres`), roles created (`.venv/bin/python -m db.setup_roles`), and `MCP_RO_DATABASE_URL`/`PIPELINE_RW_DATABASE_URL` exported matching the passwords used when `setup_roles` ran.

- [ ] **Step 6: Write the failing envelope test**

```python
# vn-market-mcp/tests/test_mcp_envelope.py
from datetime import datetime, timezone

from mcp_server.envelope import build_envelope


def test_build_envelope_includes_required_fields():
    as_of = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    result = build_envelope({"ticker": "VNM"}, sources=["postgres"], as_of=as_of)
    assert result["as_of"] == "2026-09-30T08:00:00+00:00"
    assert result["sources"] == ["postgres"]
    assert result["data"] == {"ticker": "VNM"}
    assert result["warnings"] == []


def test_build_envelope_defaults_warnings_to_empty_list_not_shared_mutable():
    as_of = datetime(2026, 9, 30, tzinfo=timezone.utc)
    r1 = build_envelope({}, sources=[], as_of=as_of)
    r1["warnings"].append("x")
    r2 = build_envelope({}, sources=[], as_of=as_of)
    assert r2["warnings"] == []


def test_build_envelope_passes_through_explicit_warnings():
    as_of = datetime(2026, 9, 30, tzinfo=timezone.utc)
    result = build_envelope({}, sources=["vnstock"], as_of=as_of, warnings=["dữ liệu cũ"])
    assert result["warnings"] == ["dữ liệu cũ"]
```

- [ ] **Step 7: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_envelope.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.envelope'`

- [ ] **Step 8: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/envelope.py
from __future__ import annotations

from datetime import datetime


def build_envelope(
    data: dict, sources: list[str], as_of: datetime, warnings: list[str] | None = None
) -> dict:
    return {
        "as_of": as_of.isoformat(),
        "sources": sources,
        "data": data,
        "warnings": list(warnings) if warnings else [],
    }
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_connection.py tests/test_mcp_envelope.py -v`
Expected: PASS (5 tests total)

- [ ] **Step 10: Commit**

```bash
git add mcp_server/__init__.py mcp_server/connection.py mcp_server/envelope.py \
        tests/test_mcp_connection.py tests/test_mcp_envelope.py .env.example
git commit -m "feat: add role-scoped DB connections and data envelope for MCP server"
```

---

## Task 2: `run_analysis` tool

**Files:**
- Create: `vn-market-mcp/mcp_server/tools/__init__.py` (empty)
- Create: `vn-market-mcp/mcp_server/tools/run_analysis.py`
- Test: `vn-market-mcp/tests/test_mcp_run_analysis_tool.py`

**Interfaces:**
- Consumes: `mcp_server.connection.get_rw_conn` (Task 1), `pipeline.run_analysis.run_analysis(conn, provider, ticker_raw, snapshot_dir, mode, style, depth) -> RunResult` (existing, `pipeline/run_analysis.py:114-121`), `RunResult` dataclass fields `run_id, status, ticker, action_label, message` (existing, `pipeline/run_analysis.py:44-49`), `providers.vnstock_provider.VNStockProvider` (existing), `ops.alerting.log_event`/`send_ops_alert` (existing, from the earlier alerting work).
- Produces: `mcp_server.tools.run_analysis.run_analysis_tool(ticker: str, style: str = "long", depth: str = "quick") -> dict` — a plain dict (not yet registered with MCP; Task 8 wires it into the server), used directly by its own unit test and later imported by `server.py`.

`run_analysis` is a write operation (it ingests data and writes to `runs`/`predictions`), so it uses `get_rw_conn`. This is the second entrypoint into `pipeline.run_analysis.run_analysis` (the first is the existing CLI in `pipeline/run_analysis.py`'s `__main__` block) — it must apply the identical logging/alerting behavior the CLI already has, so ops visibility doesn't regress for calls made through Hermes.

- [ ] **Step 1: Write the failing test**

```python
# vn-market-mcp/tests/test_mcp_run_analysis_tool.py
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_server.tools.run_analysis import run_analysis_tool
from pipeline.run_analysis import RunResult


def test_run_analysis_tool_returns_envelope_on_success(tmp_path):
    fake_result = RunResult(
        run_id="on_demand:VNM:123", status="ok", ticker="VNM",
        action_label="watch", message="trigger=first",
    )
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.run_analysis", return_value=fake_result) as mock_run, \
         patch("mcp_server.tools.run_analysis.log_event") as mock_log:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = run_analysis_tool("VNM", style="long", depth="quick")

    assert result["data"]["status"] == "ok"
    assert result["data"]["action_label"] == "watch"
    assert result["data"]["run_id"] == "on_demand:VNM:123"
    assert result["sources"] == ["vnstock", "postgres"]
    assert result["warnings"] == []
    mock_run.assert_called_once()
    mock_log.assert_any_call("mcp_run_analysis_started", ticker="VNM", style="long", depth="quick")
    mock_log.assert_any_call(
        "mcp_run_analysis_finished", ticker="VNM", status="ok", action_label="watch"
    )


def test_run_analysis_tool_returns_warning_envelope_on_non_ok_status():
    fake_result = RunResult(
        run_id=None, status="unknown_ticker", ticker="ZZZ", action_label=None,
        message="mã ZZZ không tồn tại",
    )
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.run_analysis", return_value=fake_result), \
         patch("mcp_server.tools.run_analysis.log_event"), \
         patch("mcp_server.tools.run_analysis.send_ops_alert") as mock_alert:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = run_analysis_tool("ZZZ")

    assert result["data"]["status"] == "unknown_ticker"
    assert result["warnings"] == ["mã ZZZ không tồn tại"]
    mock_alert.assert_called_once()


def test_run_analysis_tool_catches_exception_and_alerts():
    with patch("mcp_server.tools.run_analysis.get_rw_conn") as mock_conn, \
         patch("mcp_server.tools.run_analysis.run_analysis", side_effect=RuntimeError("db down")), \
         patch("mcp_server.tools.run_analysis.log_event") as mock_log, \
         patch("mcp_server.tools.run_analysis.send_ops_alert") as mock_alert:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        result = run_analysis_tool("VNM")

    assert result["data"]["status"] == "error"
    assert "db down" in result["warnings"][0]
    mock_alert.assert_called_once()
    mock_log.assert_any_call(
        "mcp_run_analysis_failed", ticker="VNM", error="db down", error_type="RuntimeError"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_run_analysis_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.run_analysis'`

- [ ] **Step 3: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/tools/__init__.py
```//empty file

```python
# vn-market-mcp/mcp_server/tools/run_analysis.py
"""MCP tool wrapping pipeline.run_analysis.run_analysis.

Note: this calls the existing SYNCHRONOUS run_analysis() and blocks until
it returns. The spec (§3, §4.1) describes an async job_id + background
worker + Telegram push; that worker does not exist yet in this codebase
(Phase 0+1 only built the on-demand CLI). Callers of this tool should
expect it to block for the duration of one full pipeline run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from ops.alerting import log_event, send_ops_alert
from pipeline.run_analysis import run_analysis
from providers.vnstock_provider import VNStockProvider

SNAPSHOT_DIR = Path("snapshots")


def run_analysis_tool(ticker: str, style: str = "long", depth: str = "quick") -> dict:
    now = datetime.now(timezone.utc)
    log_event("mcp_run_analysis_started", ticker=ticker, style=style, depth=depth)

    try:
        with get_rw_conn() as conn:
            result = run_analysis(
                conn, VNStockProvider(source="VCI"), ticker, SNAPSHOT_DIR,
                mode="on_demand", style=style, depth=depth,
            )
    except Exception as exc:
        log_event(
            "mcp_run_analysis_failed", ticker=ticker, error=str(exc), error_type=type(exc).__name__,
        )
        send_ops_alert(f"[vn-market-mcp/mcp] LỖI khi chạy {ticker}: {type(exc).__name__}: {exc}")
        return build_envelope(
            {"status": "error", "ticker": ticker, "run_id": None, "action_label": None},
            sources=["vnstock", "postgres"], as_of=now, warnings=[str(exc)],
        )

    if result.status == "ok":
        log_event(
            "mcp_run_analysis_finished", ticker=ticker, status=result.status, action_label=result.action_label,
        )
        warnings: list[str] = []
    else:
        log_event("mcp_run_analysis_finished_with_issue", ticker=ticker, status=result.status, message=result.message)
        send_ops_alert(f"[vn-market-mcp/mcp] {ticker}: {result.status} — {result.message}")
        warnings = [result.message]

    return build_envelope(
        {
            "status": result.status, "ticker": result.ticker, "run_id": result.run_id,
            "action_label": result.action_label, "message": result.message,
        },
        sources=["vnstock", "postgres"], as_of=now, warnings=warnings,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_run_analysis_tool.py -v`
Expected: PASS (3 tests) — this test mocks `get_rw_conn`/`run_analysis`/`log_event`/`send_ops_alert`, so it does not need a live Postgres connection.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/tools/__init__.py mcp_server/tools/run_analysis.py \
        tests/test_mcp_run_analysis_tool.py
git commit -m "feat: add run_analysis MCP tool handler"
```

---

## Task 3: `get_snapshot` tool

**Files:**
- Create: `vn-market-mcp/mcp_server/tools/snapshot.py`
- Test: `vn-market-mcp/tests/test_mcp_snapshot_tool.py`

**Interfaces:**
- Consumes: `mcp_server.connection.get_ro_conn` (Task 1), `mcp_server.envelope.build_envelope` (Task 1). Reads directly from the `runs` table (`run_id, snapshot_ref, as_of` columns — schema at `db/migrations/003_runs_theses_predictions.sql`) and the JSON file at `snapshot_ref` (written by `pipeline.snapshot.write_snapshot`, existing).
- Produces: `mcp_server.tools.snapshot.get_snapshot_tool(ticker: str | None = None, run_id: str | None = None) -> dict`, used by Task 8's server registration. Exactly one of `ticker`/`run_id` must be given — `ticker` looks up the most recent run for that ticker (`tickers` array column contains it); `run_id` looks up that exact run.

- [ ] **Step 1: Write the failing test**

```python
# vn-market-mcp/tests/test_mcp_snapshot_tool.py
import json
from pathlib import Path

import pytest

from db.connection import get_conn
from mcp_server.tools.snapshot import get_snapshot_tool


@pytest.fixture
def seeded_run(tmp_path):
    snapshot_path = tmp_path / "test_run.json"
    snapshot_path.write_text(json.dumps({"ticker": "VNMTEST", "action_label": "watch"}))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('test:VNMTEST:1', 'on_demand', ARRAY['VNMTEST'], 'long', 'quick', now(), %s, '[]')
            """,
            (str(snapshot_path),),
        )
    yield "test:VNMTEST:1", str(snapshot_path)
    with get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE run_id = 'test:VNMTEST:1'")


def test_get_snapshot_by_run_id(seeded_run):
    run_id, _ = seeded_run
    result = get_snapshot_tool(run_id=run_id)
    assert result["data"]["run_id"] == run_id
    assert result["data"]["snapshot"]["ticker"] == "VNMTEST"
    assert result["warnings"] == []


def test_get_snapshot_by_ticker_returns_most_recent(seeded_run):
    result = get_snapshot_tool(ticker="VNMTEST")
    assert result["data"]["snapshot"]["ticker"] == "VNMTEST"


def test_get_snapshot_unknown_run_id_returns_not_found():
    result = get_snapshot_tool(run_id="does:not:exist")
    assert result["data"]["status"] == "not_found"
    assert result["warnings"] != []


def test_get_snapshot_requires_exactly_one_arg():
    with pytest.raises(ValueError):
        get_snapshot_tool()
    with pytest.raises(ValueError):
        get_snapshot_tool(ticker="VNM", run_id="x:y:1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_snapshot_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.snapshot'`

- [ ] **Step 3: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/tools/snapshot.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope


def get_snapshot_tool(ticker: str | None = None, run_id: str | None = None) -> dict:
    if (ticker is None) == (run_id is None):
        raise ValueError("provide exactly one of ticker or run_id")

    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        if run_id is not None:
            row = conn.execute(
                "SELECT run_id, snapshot_ref, as_of FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT run_id, snapshot_ref, as_of FROM runs WHERE %s = ANY(tickers)"
                " ORDER BY as_of DESC LIMIT 1",
                (ticker,),
            ).fetchone()

    if row is None:
        return build_envelope(
            {"status": "not_found", "run_id": run_id, "ticker": ticker},
            sources=["postgres"], as_of=now, warnings=["không tìm thấy run"],
        )

    found_run_id, snapshot_ref, run_as_of = row
    snapshot_path = Path(snapshot_ref)
    if not snapshot_path.exists():
        return build_envelope(
            {"status": "snapshot_file_missing", "run_id": found_run_id, "ticker": ticker},
            sources=["postgres"], as_of=now, warnings=[f"file snapshot không tồn tại: {snapshot_ref}"],
        )

    snapshot = json.loads(snapshot_path.read_text())
    return build_envelope(
        {"status": "ok", "run_id": found_run_id, "snapshot": snapshot},
        sources=["postgres", "snapshot_file"], as_of=run_as_of,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_snapshot_tool.py -v`
Expected: PASS (4 tests) — requires live Postgres (uses `db.connection.get_conn` for fixture setup and `mcp_server.connection.get_ro_conn` for the tool itself; both need `DATABASE_URL` and `MCP_RO_DATABASE_URL` exported).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/tools/snapshot.py tests/test_mcp_snapshot_tool.py
git commit -m "feat: add get_snapshot MCP tool handler"
```

---

## Task 4: `query_history` tool

**Files:**
- Create: `vn-market-mcp/mcp_server/tools/history.py`
- Test: `vn-market-mcp/tests/test_mcp_history_tool.py`

**Interfaces:**
- Consumes: `mcp_server.connection.get_ro_conn`, `mcp_server.envelope.build_envelope` (Task 1). Reads `prices_daily` (columns: `ticker, trade_date, open, high, low, close, volume` — schema at `db/migrations/002_market_data.sql`), `fundamentals_quarterly` (`ticker, period, metrics` JSONB), `foreign_flow_daily` (`ticker, trade_date, net_value`) — all existing tables, already used by `pipeline/run_analysis.py` (see e.g. `pipeline/run_analysis.py:154-158` for the `prices_daily` query shape).
- Produces: `mcp_server.tools.history.query_history_tool(ticker: str, series: str, start_date: str | None = None, end_date: str | None = None) -> dict`, `series` is one of `"prices"`, `"fundamentals"`, `"foreign_flow"`. Dates are `YYYY-MM-DD` strings; `None` means unbounded on that side.

- [ ] **Step 1: Write the failing test**

```python
# vn-market-mcp/tests/test_mcp_history_tool.py
import pytest

from mcp_server.tools.history import query_history_tool


def test_query_history_rejects_unknown_series():
    with pytest.raises(ValueError):
        query_history_tool("VNM", series="not_a_series")


def test_query_history_prices_empty_ticker_returns_empty_list_not_error():
    result = query_history_tool("ZZZNOPE", series="prices")
    assert result["data"]["rows"] == []
    assert result["warnings"] == ["không có dữ liệu cho ZZZNOPE"]


def test_query_history_prices_returns_rows_for_known_ticker():
    # VN30 seed data from db/migrations includes at least the tickers table;
    # this test seeds one price row directly to avoid depending on live vnstock.
    from db.connection import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tickers (ticker, exchange) VALUES ('HISTTEST', 'HOSE') ON CONFLICT DO NOTHING"
        )
        conn.execute(
            """
            INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)
            VALUES ('HISTTEST', '2026-09-28', 10, 11, 9, 10.5, 1000, 'test', now())
            ON CONFLICT (ticker, trade_date) DO NOTHING
            """
        )
    try:
        result = query_history_tool("HISTTEST", series="prices")
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["close"] == 10.5
        assert result["warnings"] == []
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM prices_daily WHERE ticker = 'HISTTEST'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'HISTTEST'")


def test_query_history_respects_date_range():
    from db.connection import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tickers (ticker, exchange) VALUES ('HISTTEST2', 'HOSE') ON CONFLICT DO NOTHING"
        )
        for d, c in [("2026-09-01", 10), ("2026-09-15", 11), ("2026-09-28", 12)]:
            conn.execute(
                """
                INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source, fetched_at)
                VALUES ('HISTTEST2', %s, %s, %s, %s, %s, 1000, 'test', now())
                ON CONFLICT (ticker, trade_date) DO NOTHING
                """,
                (d, c, c, c, c),
            )
    try:
        result = query_history_tool(
            "HISTTEST2", series="prices", start_date="2026-09-10", end_date="2026-09-20"
        )
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0]["close"] == 11
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM prices_daily WHERE ticker = 'HISTTEST2'")
            conn.execute("DELETE FROM tickers WHERE ticker = 'HISTTEST2'")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_history_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.history'`

- [ ] **Step 3: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/tools/history.py
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope

_SERIES_QUERIES = {
    "prices": (
        "SELECT trade_date, open, high, low, close, volume FROM prices_daily"
        " WHERE ticker = %s AND trade_date >= COALESCE(%s, trade_date)"
        " AND trade_date <= COALESCE(%s, trade_date) ORDER BY trade_date",
        ["trade_date", "open", "high", "low", "close", "volume"],
    ),
    "fundamentals": (
        "SELECT period, metrics FROM fundamentals_quarterly"
        " WHERE ticker = %s AND period >= COALESCE(%s, period)"
        " AND period <= COALESCE(%s, period) ORDER BY period",
        ["period", "metrics"],
    ),
    "foreign_flow": (
        "SELECT trade_date, net_value FROM foreign_flow_daily"
        " WHERE ticker = %s AND trade_date >= COALESCE(%s, trade_date)"
        " AND trade_date <= COALESCE(%s, trade_date) ORDER BY trade_date",
        ["trade_date", "net_value"],
    ),
}


def query_history_tool(
    ticker: str, series: str, start_date: str | None = None, end_date: str | None = None
) -> dict:
    if series not in _SERIES_QUERIES:
        raise ValueError(f"series phải là một trong {sorted(_SERIES_QUERIES)}, nhận '{series}'")

    sql, columns = _SERIES_QUERIES[series]
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        rows = conn.execute(sql, (ticker, start_date, end_date)).fetchall()

    dict_rows = [dict(zip(columns, row)) for row in rows]
    warnings = [] if dict_rows else [f"không có dữ liệu cho {ticker}"]
    return build_envelope(
        {"ticker": ticker, "series": series, "rows": dict_rows},
        sources=["postgres"], as_of=now, warnings=warnings,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_history_tool.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/tools/history.py tests/test_mcp_history_tool.py
git commit -m "feat: add query_history MCP tool handler"
```

---

## Task 5: `explain_run` and `list_predictions` tools

**Files:**
- Create: `vn-market-mcp/mcp_server/tools/explain.py`
- Create: `vn-market-mcp/mcp_server/tools/predictions.py`
- Test: `vn-market-mcp/tests/test_mcp_explain_tool.py`
- Test: `vn-market-mcp/tests/test_mcp_predictions_tool.py`

**Interfaces:**
- Consumes: `mcp_server.connection.get_ro_conn`, `mcp_server.envelope.build_envelope` (Task 1). Reads `runs`, `predictions`, `data_quality_log` tables (schema: `db/migrations/003_runs_theses_predictions.sql`, `db/migrations/004_positions_quality_watchlist.sql`).
- Produces:
  - `mcp_server.tools.explain.explain_run_tool(run_id: str) -> dict`
  - `mcp_server.tools.predictions.list_predictions_tool(ticker: str | None = None, status: str | None = None, limit: int = 20) -> dict`

- [ ] **Step 1: Write the failing explain_run test**

```python
# vn-market-mcp/tests/test_mcp_explain_tool.py
import pytest

from db.connection import get_conn
from mcp_server.tools.explain import explain_run_tool


@pytest.fixture
def seeded_run_with_checks():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('explain:TEST:1', 'on_demand', ARRAY['EXPTEST'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            "INSERT INTO data_quality_log (run_id, ticker, check_name, result, detail)"
            " VALUES ('explain:TEST:1', 'EXPTEST', 'freshness', 'pass', '{}')"
        )
    yield "explain:TEST:1"
    with get_conn() as conn:
        conn.execute("DELETE FROM data_quality_log WHERE run_id = 'explain:TEST:1'")
        conn.execute("DELETE FROM predictions WHERE run_id = 'explain:TEST:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'explain:TEST:1'")


def test_explain_run_returns_run_and_checks(seeded_run_with_checks):
    result = explain_run_tool(seeded_run_with_checks)
    assert result["data"]["status"] == "ok"
    assert result["data"]["run"]["run_id"] == "explain:TEST:1"
    assert len(result["data"]["quality_checks"]) == 1
    assert result["data"]["quality_checks"][0]["check_name"] == "freshness"
    assert result["data"]["predictions"] == []


def test_explain_run_unknown_run_id_returns_not_found():
    result = explain_run_tool("does:not:exist")
    assert result["data"]["status"] == "not_found"
    assert result["warnings"] != []
```

- [ ] **Step 2: Run explain_run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_explain_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.explain'`

- [ ] **Step 3: Write minimal explain_run implementation**

```python
# vn-market-mcp/mcp_server/tools/explain.py
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope


def explain_run_tool(run_id: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        run_row = conn.execute(
            "SELECT run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings"
            " FROM runs WHERE run_id = %s",
            (run_id,),
        ).fetchone()

        if run_row is None:
            return build_envelope(
                {"status": "not_found", "run_id": run_id},
                sources=["postgres"], as_of=now, warnings=["không tìm thấy run_id"],
            )

        checks = conn.execute(
            "SELECT ticker, check_name, result, detail, created_at FROM data_quality_log"
            " WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()
        preds = conn.execute(
            "SELECT id, ticker, action_label, status, confidence, created_at FROM predictions"
            " WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()

    run_cols = ["run_id", "mode", "tickers", "style", "depth", "as_of", "snapshot_ref", "warnings"]
    check_cols = ["ticker", "check_name", "result", "detail", "created_at"]
    pred_cols = ["id", "ticker", "action_label", "status", "confidence", "created_at"]

    return build_envelope(
        {
            "status": "ok",
            "run": dict(zip(run_cols, run_row)),
            "quality_checks": [dict(zip(check_cols, row)) for row in checks],
            "predictions": [dict(zip(pred_cols, row)) for row in preds],
        },
        sources=["postgres"], as_of=now,
    )
```

- [ ] **Step 4: Run explain_run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_explain_tool.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing list_predictions test**

```python
# vn-market-mcp/tests/test_mcp_predictions_tool.py
import pytest

from db.connection import get_conn
from mcp_server.tools.predictions import list_predictions_tool


@pytest.fixture
def seeded_predictions():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('pred:TEST:1', 'on_demand', ARRAY['PREDTEST'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
              run_id, source, ticker, trigger, action_label, universe_tier, holding_state,
              signal_type, horizon_days, confidence, status
            )
            VALUES ('pred:TEST:1', 'on_demand', 'PREDTEST', 'first', 'watch', 'A', 'unknown',
                    'mixed', 120, 0.5, 'open')
            """
        )
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions WHERE run_id = 'pred:TEST:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'pred:TEST:1'")


def test_list_predictions_filters_by_ticker(seeded_predictions):
    result = list_predictions_tool(ticker="PREDTEST")
    assert len(result["data"]["predictions"]) == 1
    assert result["data"]["predictions"][0]["action_label"] == "watch"


def test_list_predictions_filters_by_status(seeded_predictions):
    result = list_predictions_tool(ticker="PREDTEST", status="closed")
    assert result["data"]["predictions"] == []
    assert result["warnings"] == []  # empty result set is not itself a warning


def test_list_predictions_empty_ticker_history_is_not_an_error():
    result = list_predictions_tool(ticker="ZZZNOPRED")
    assert result["data"]["predictions"] == []
```

- [ ] **Step 6: Run list_predictions test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_predictions_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.predictions'`

- [ ] **Step 7: Write minimal list_predictions implementation**

```python
# vn-market-mcp/mcp_server/tools/predictions.py
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope

_COLUMNS = [
    "id", "run_id", "ticker", "trigger", "action_label", "universe_tier",
    "holding_state", "signal_type", "entry_zone", "stop_loss", "target",
    "horizon_days", "confidence", "status", "created_at",
]


def list_predictions_tool(
    ticker: str | None = None, status: str | None = None, limit: int = 20
) -> dict:
    now = datetime.now(timezone.utc)
    query = (
        f"SELECT {', '.join(_COLUMNS)} FROM predictions"
        " WHERE ticker = COALESCE(%s, ticker) AND status = COALESCE(%s, status)"
        " ORDER BY created_at DESC LIMIT %s"
    )
    with get_ro_conn() as conn:
        rows = conn.execute(query, (ticker, status, limit)).fetchall()

    predictions = [dict(zip(_COLUMNS, row)) for row in rows]
    return build_envelope(
        {"predictions": predictions}, sources=["postgres"], as_of=now,
    )
```

- [ ] **Step 8: Run list_predictions test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_predictions_tool.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Commit**

```bash
git add mcp_server/tools/explain.py mcp_server/tools/predictions.py \
        tests/test_mcp_explain_tool.py tests/test_mcp_predictions_tool.py
git commit -m "feat: add explain_run and list_predictions MCP tool handlers"
```

---

## Task 6: `get_stats` tool

**Files:**
- Create: `vn-market-mcp/mcp_server/tools/stats.py`
- Test: `vn-market-mcp/tests/test_mcp_stats_tool.py`

**Interfaces:**
- Consumes: `mcp_server.connection.get_ro_conn`, `mcp_server.envelope.build_envelope` (Task 1). Reads `runs` and `predictions` tables.
- Produces: `mcp_server.tools.stats.get_stats_tool() -> dict`. Not present in the spec's tool table (line 343 just says "get_stats" with no field list) — this plan defines its exact contract here since none existed: total run count, breakdown of predictions by `action_label`, breakdown of predictions by `status` (open/closed). Kept intentionally small (no time-windowing, no per-ticker breakdown) — this is the minimum that answers "is the system working and what has it been saying," matching spec §5.1's one-line description; extend later if Hermes's actual chat usage shows a need for more slices.

- [ ] **Step 1: Write the failing test**

```python
# vn-market-mcp/tests/test_mcp_stats_tool.py
import pytest

from db.connection import get_conn
from mcp_server.tools.stats import get_stats_tool


def test_get_stats_on_empty_database_returns_zeroed_counts():
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions")
        conn.execute("DELETE FROM runs")

    result = get_stats_tool()
    assert result["data"]["total_runs"] == 0
    assert result["data"]["predictions_by_action_label"] == {}
    assert result["data"]["predictions_by_status"] == {}
    assert result["warnings"] == []


@pytest.fixture
def seeded_stats_data():
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, mode, tickers, style, depth, as_of, snapshot_ref, warnings)
            VALUES ('stats:TEST:1', 'on_demand', ARRAY['STATSTEST'], 'long', 'quick', now(), '/tmp/x.json', '[]')
            """
        )
        conn.execute(
            """
            INSERT INTO predictions (
              run_id, source, ticker, trigger, action_label, universe_tier, holding_state,
              signal_type, horizon_days, confidence, status
            )
            VALUES
              ('stats:TEST:1', 'on_demand', 'STATSTEST', 'first', 'watch', 'A', 'unknown', 'mixed', 120, 0.5, 'open'),
              ('stats:TEST:1', 'on_demand', 'STATSTEST', 'first', 'watch', 'A', 'unknown', 'mixed', 120, 0.5, 'closed')
            """
        )
    yield
    with get_conn() as conn:
        conn.execute("DELETE FROM predictions WHERE run_id = 'stats:TEST:1'")
        conn.execute("DELETE FROM runs WHERE run_id = 'stats:TEST:1'")


def test_get_stats_counts_runs_and_breaks_down_predictions(seeded_stats_data):
    result = get_stats_tool()
    assert result["data"]["total_runs"] >= 1
    assert result["data"]["predictions_by_action_label"]["watch"] >= 2
    assert result["data"]["predictions_by_status"]["open"] >= 1
    assert result["data"]["predictions_by_status"]["closed"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_stats_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.stats'`

- [ ] **Step 3: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/tools/stats.py
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_ro_conn
from mcp_server.envelope import build_envelope


def get_stats_tool() -> dict:
    now = datetime.now(timezone.utc)
    with get_ro_conn() as conn:
        total_runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
        by_label = conn.execute(
            "SELECT action_label, count(*) FROM predictions GROUP BY action_label"
        ).fetchall()
        by_status = conn.execute(
            "SELECT status, count(*) FROM predictions GROUP BY status"
        ).fetchall()

    return build_envelope(
        {
            "total_runs": total_runs,
            "predictions_by_action_label": {label: count for label, count in by_label},
            "predictions_by_status": {status: count for status, count in by_status},
        },
        sources=["postgres"], as_of=now,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_stats_tool.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/tools/stats.py tests/test_mcp_stats_tool.py
git commit -m "feat: add get_stats MCP tool handler"
```

---

## Task 7: `set_position` and `clear_position` tools

**Files:**
- Create: `vn-market-mcp/mcp_server/tools/positions.py`
- Test: `vn-market-mcp/tests/test_mcp_positions_tools.py`

**Interfaces:**
- Consumes: `mcp_server.connection.get_rw_conn`, `mcp_server.envelope.build_envelope` (Task 1). `pipeline.positions.set_position(conn, ticker, avg_cost, declared_by) -> None`, `pipeline.positions.clear_position(conn, ticker, declared_by) -> None`, `pipeline.positions.get_holding_state(conn, ticker) -> str` (all existing, `pipeline/positions.py`).
- Produces: `mcp_server.tools.positions.set_position_tool(ticker: str, avg_cost: float | None, declared_by: str) -> dict`, `mcp_server.tools.positions.clear_position_tool(ticker: str, declared_by: str) -> dict`.

These are the only two write tools besides `run_analysis` — spec §5.8 frames positions as strictly self-declared (never inferred), so `declared_by` is a required argument here (identifies who declared it, e.g. the Hermes/Telegram user id), matching spec's "tự khai" (self-declared) language literally.

- [ ] **Step 1: Write the failing test**

```python
# vn-market-mcp/tests/test_mcp_positions_tools.py
from db.connection import get_conn
from mcp_server.tools.positions import clear_position_tool, set_position_tool


def _cleanup(ticker):
    with get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE ticker = %s", (ticker,))


def test_set_position_tool_marks_holding():
    try:
        result = set_position_tool("POSTEST", avg_cost=25.5, declared_by="user123")
        assert result["data"]["status"] == "ok"
        assert result["data"]["holding_state"] == "holding"
        assert result["warnings"] == []
    finally:
        _cleanup("POSTEST")


def test_clear_position_tool_marks_none():
    try:
        set_position_tool("POSTEST2", avg_cost=10.0, declared_by="user123")
        result = clear_position_tool("POSTEST2", declared_by="user123")
        assert result["data"]["status"] == "ok"
        assert result["data"]["holding_state"] == "none"
    finally:
        _cleanup("POSTEST2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_positions_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.tools.positions'`

- [ ] **Step 3: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/tools/positions.py
from __future__ import annotations

from datetime import datetime, timezone

from mcp_server.connection import get_rw_conn
from mcp_server.envelope import build_envelope
from pipeline.positions import clear_position, get_holding_state, set_position


def set_position_tool(ticker: str, avg_cost: float | None, declared_by: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_rw_conn() as conn:
        set_position(conn, ticker, avg_cost, declared_by)
        holding_state = get_holding_state(conn, ticker)
    return build_envelope(
        {"status": "ok", "ticker": ticker, "holding_state": holding_state},
        sources=["postgres"], as_of=now,
    )


def clear_position_tool(ticker: str, declared_by: str) -> dict:
    now = datetime.now(timezone.utc)
    with get_rw_conn() as conn:
        clear_position(conn, ticker, declared_by)
        holding_state = get_holding_state(conn, ticker)
    return build_envelope(
        {"status": "ok", "ticker": ticker, "holding_state": holding_state},
        sources=["postgres"], as_of=now,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_positions_tools.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/tools/positions.py tests/test_mcp_positions_tools.py
git commit -m "feat: add set_position and clear_position MCP tool handlers"
```

---

## Task 8: FastMCP server registration + stdio entrypoint + integration test

**Files:**
- Create: `vn-market-mcp/mcp_server/server.py`
- Modify: `vn-market-mcp/requirements.txt`
- Test: `vn-market-mcp/tests/test_mcp_server_integration.py`

**Interfaces:**
- Consumes: all eight tool functions from Tasks 2-7 (`run_analysis_tool`, `get_snapshot_tool`, `query_history_tool`, `explain_run_tool`, `list_predictions_tool`, `get_stats_tool`, `set_position_tool`, `clear_position_tool`).
- Produces: `mcp_server.server.mcp` — a `FastMCP` instance with all eight tools registered, runnable via `python -m mcp_server.server` (stdio transport) or imported directly by the integration test using the SDK's in-memory client session.

`mcp==2.2.0`'s `FastMCP` decorator (`@mcp.tool()`) introspects the wrapped function's type hints to build the tool's JSON schema automatically — so each `@mcp.tool()`-decorated function in this file needs its own type-hinted signature (it cannot just be `mcp.tool()(run_analysis_tool)` reassignment, because docstrings/param descriptions matter for how Hermes sees the tool). Each registration function is a 3-5 line pass-through to the Task 2-7 implementation.

- [ ] **Step 1: Add the `mcp` and `pytest-asyncio` dependencies**

In `vn-market-mcp/requirements.txt`, add:

```
mcp==2.2.0
pytest-asyncio==0.24.0
```

Run: `.venv/bin/pip install -q mcp==2.2.0 pytest-asyncio==0.24.0`

- [ ] **Step 2: Write the failing integration test**

```python
# vn-market-mcp/tests/test_mcp_server_integration.py
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_server.server import mcp


@pytest.mark.asyncio
async def test_server_lists_all_eight_tools():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "run_analysis", "get_snapshot", "query_history", "explain_run",
            "list_predictions", "get_stats", "set_position", "clear_position",
        }


@pytest.mark.asyncio
async def test_server_get_stats_tool_call_returns_structured_content():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        result = await client.call_tool("get_stats", {})
        assert result.isError is False
        assert result.structuredContent is not None
        assert "total_runs" in result.structuredContent
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_server_integration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.server'`

- [ ] **Step 4: Write minimal implementation**

```python
# vn-market-mcp/mcp_server/server.py
"""vn-market-mcp MCP server: stdio transport, read-only + run_analysis tools.

Run directly: python -m mcp_server.server
Connects to Postgres as mcp_ro (read tools) / pipeline_rw (write tools) —
see .env.example for MCP_RO_DATABASE_URL / PIPELINE_RW_DATABASE_URL.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server.tools.explain import explain_run_tool
from mcp_server.tools.history import query_history_tool
from mcp_server.tools.positions import clear_position_tool, set_position_tool
from mcp_server.tools.predictions import list_predictions_tool
from mcp_server.tools.run_analysis import run_analysis_tool
from mcp_server.tools.snapshot import get_snapshot_tool
from mcp_server.tools.stats import get_stats_tool

mcp = FastMCP("vn-market-mcp")


@mcp.tool()
def run_analysis(ticker: str, style: str = "long", depth: str = "quick") -> dict:
    """Chạy pipeline phân tích cho một mã (đồng bộ, chặn tới khi xong)."""
    return run_analysis_tool(ticker, style=style, depth=depth)


@mcp.tool()
def get_snapshot(ticker: str | None = None, run_id: str | None = None) -> dict:
    """Đọc lại snapshot đã lưu, theo ticker (mới nhất) hoặc theo run_id."""
    return get_snapshot_tool(ticker=ticker, run_id=run_id)


@mcp.tool()
def query_history(
    ticker: str, series: str, start_date: str | None = None, end_date: str | None = None
) -> dict:
    """Tra cứu chuỗi lịch sử: series là 'prices' | 'fundamentals' | 'foreign_flow'."""
    return query_history_tool(ticker, series, start_date=start_date, end_date=end_date)


@mcp.tool()
def explain_run(run_id: str) -> dict:
    """Chi tiết một run: thông tin run, các data-quality check, các prediction đã ghi."""
    return explain_run_tool(run_id)


@mcp.tool()
def list_predictions(
    ticker: str | None = None, status: str | None = None, limit: int = 20
) -> dict:
    """Liệt kê predictions, lọc theo ticker/status."""
    return list_predictions_tool(ticker=ticker, status=status, limit=limit)


@mcp.tool()
def get_stats() -> dict:
    """Thống kê tổng hợp: tổng số run, phân bố action_label, phân bố status."""
    return get_stats_tool()


@mcp.tool()
def set_position(ticker: str, avg_cost: float | None, declared_by: str) -> dict:
    """Tự khai đang nắm giữ một mã (không suy luận từ dữ liệu khác)."""
    return set_position_tool(ticker, avg_cost, declared_by)


@mcp.tool()
def clear_position(ticker: str, declared_by: str) -> dict:
    """Tự khai đã thoát vị thế một mã."""
    return clear_position_tool(ticker, declared_by)


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_mcp_server_integration.py -v`
Expected: PASS (2 tests) — requires live Postgres for the `get_stats` call.

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `PYTHONPATH=. .venv/bin/python -m pytest -v`
Expected: PASS (all prior Phase 0+1 tests + all new `mcp_server` tests)

- [ ] **Step 7: Commit**

```bash
git add mcp_server/server.py requirements.txt tests/test_mcp_server_integration.py
git commit -m "feat: register vn-market-mcp FastMCP server with all 8 tools"
```

---

## Task 9: README documentation for running the server and connecting Hermes

**Files:**
- Modify: `vn-market-mcp/README.txt`

**Interfaces:**
- Consumes: nothing new — this task only documents Tasks 1-8's output.
- Produces: nothing consumed by later tasks (final task in this plan).

**Verification note on Hermes's MCP client configuration:** Hermes Agent (Nous Research)'s exact config file format/location for registering an external stdio MCP server was **not verified** during this plan's research — the project spec (line 112, line 647) itself flags Hermes's compatibility with recent model/tooling changes as unverified and something to check "ở giai đoạn 0." Do not invent a specific Hermes config file path or key name. Write the README section generically (what command to run, what env vars it needs) and explicitly mark the Hermes-side registration step as needing verification against Hermes's actual current documentation.

- [ ] **Step 1: Add a new README section documenting the MCP server**

In `vn-market-mcp/README.txt`, insert a new section after the current section 12 (OPS ALERTING) and before "11. PROJECT HISTORY" (matching this file's existing — already slightly out-of-order — numbering; add this as section 13 and renumber "11. PROJECT HISTORY" to "14. PROJECT HISTORY" if you want strict ordering, or simply append as the new highest-numbered section, whichever requires the smaller diff against the current file):

```
13. MCP SERVER (for Hermes Agent / any MCP client)
-------------------------------------------------------
  mcp_server/ exposes 8 tools over the standard MCP protocol (stdio
  transport): run_analysis, get_snapshot, query_history, explain_run,
  list_predictions, get_stats, set_position, clear_position.

  These are thin wrappers around the same pipeline/ code the CLI uses —
  no new business logic lives here. Read-only tools connect to Postgres
  as the mcp_ro role (SELECT-only); run_analysis/set_position/
  clear_position connect as pipeline_rw. No tool in this server calls
  the Anthropic/Claude API — the LLM-role tools from the spec
  (analyze_news, synthesize, bull_case, bear_case, ...) are not built
  yet (see section 10, Known Limitations).

  IMPORTANT: run_analysis here calls the existing pipeline SYNCHRONOUSLY
  and blocks until the run finishes. The full spec describes an async
  job_id + background worker that pushes results via Telegram — that
  worker does not exist in this codebase yet. A client calling this
  tool should expect it to block for the duration of one full pipeline
  run (typically a few seconds to a couple minutes, not fast if
  vnstock is slow).

  Setup:
    1. Add to .env (see .env.example):
         MCP_RO_DATABASE_URL=postgresql://mcp_ro:<MCP_RO_PASSWORD>@localhost:55432/vnmcp
         PIPELINE_RW_DATABASE_URL=postgresql://pipeline_rw:<PIPELINE_RW_PASSWORD>@localhost:55432/vnmcp
       (passwords must match what db/setup_roles.py set for those roles —
       i.e. the same MCP_RO_PASSWORD / PIPELINE_RW_PASSWORD values.)
    2. .venv/bin/pip install -r requirements.txt   (installs the mcp SDK)
    3. Run the server directly to sanity-check it starts:
         export $(cat .env | xargs)
         .venv/bin/python -m mcp_server.server
       (it will sit waiting on stdio — Ctrl+C to stop; this is normal,
       it's meant to be launched BY an MCP client, not run standalone
       for interactive use)

  Connecting Hermes Agent (Nous Research):
    Hermes's exact configuration format for registering an external
    stdio MCP server was NOT verified while building this — the project
    spec itself (see docs/superpowers/plans/..., and
    ../vn-trading-agent-plan_final.md) flags Hermes version/tooling
    compatibility as something to verify hands-on. In general, an MCP
    stdio server is registered with a client by giving it the command
    to launch the server process (here: the venv's python, `-m
    mcp_server.server`, working directory vn-market-mcp/, with the .env
    variables above present in the process environment) — check
    Hermes's current documentation for its specific config file/key for
    this before wiring it up.

  Testing without Hermes:
    Any MCP client works for manual testing, e.g. the official MCP
    Inspector (`npx @modelcontextprotocol/inspector .venv/bin/python -m
    mcp_server.server`), or the SDK's Python client directly.
```

- [ ] **Step 2: Add `ops/alerting.py` cross-reference note**

In the same README, in section 2 ("WHAT'S INCLUDED"), add one line after the existing `ops/alerting.py` entry:

```
  mcp_server/                     MCP protocol server (stdio) exposing 8
                                   tools to Hermes/any MCP client — see
                                   section 13
```

- [ ] **Step 3: Update "NOT in this phase" list**

In the same README section 2, remove "MCP server tool registration (this project is a CLI for now)" from the "NOT in this phase" bullet list, since this plan now delivers it. Leave the other four bullets (Telegram/chat interface, cron scheduler, LLM-backed roles, prediction grading, retention jobs) — those remain genuinely out of scope.

- [ ] **Step 4: Commit**

```bash
git add README.txt
git commit -m "docs: document MCP server setup and Hermes connection caveats"
```

---

## Self-Review

**1. Spec coverage:** §5.1's tool table lists 15 tools total; this plan explicitly builds the 8 the user scoped in (run_analysis, get_snapshot, query_history, explain_run, list_predictions, get_stats, set_position, clear_position) and explicitly excludes `technical_snapshot`, `fundamental_snapshot`, `foreign_flow`, `corporate_events`, `market_breadth`, `risk_plan` (these are internal pipeline functions already exercised through `run_analysis`, not separately spec'd as standalone MCP tools the user asked for) and the 6 LLM-role tools (`analyze_news` etc. — explicitly out of scope per the user's locked-in decision). §6's data envelope (`as_of`, `sources`) is implemented once in Task 1 and reused everywhere. §7.1's role-based DB access is implemented in Task 1 and used by every tool task. Covered.

**2. Placeholder scan:** No TBD/TODO strings. Every step has real, complete code. The one deliberately-unresolved item (Hermes's exact client config format) is flagged as an explicit, named unknown in Task 9 rather than glossed over with a fake instruction — that's an honest scope boundary, not a placeholder.

**3. Type consistency:** `build_envelope(data, sources, as_of, warnings=None)` signature from Task 1 is used identically by every tool in Tasks 2-7. `get_ro_conn`/`get_rw_conn` context managers from Task 1 are imported identically everywhere. `RunResult` fields (`run_id, status, ticker, action_label, message`) match the existing dataclass in `pipeline/run_analysis.py:44-49` — verified against the actual file, not assumed.

**4. Review Focus:** all five items listed have an owning task and a concrete test (unknown ticker → Task 4/Task 3 not_found paths; run_analysis exception → Task 2's `test_run_analysis_tool_catches_exception_and_alerts`; get_stats on empty DB → Task 6's `test_get_stats_on_empty_database_returns_zeroed_counts`; explain_run unknown run_id → Task 5's `test_explain_run_unknown_run_id_returns_not_found`; mcp_ro permission boundary → Task 1's `test_get_ro_conn_cannot_insert`).
