VN-MARKET-MCP — Vietnamese Stock Trading Research Pipeline (Phase 0 + Phase 1)
================================================================================

TUYEN BO / DISCLAIMER
----------------------
Cong cu ho tro nghien cuu va ra quyet dinh, KHONG PHAI tu van dau tu va
KHONG tu dat lenh. Rui ro giao dich do nguoi dung tu chiu.

This is a research/decision-support tool. It is NOT investment advice and
does NOT place orders. Trading risk is entirely the user's own.


1. PROJECT OVERVIEW
--------------------
vn-market-mcp is a deterministic, code-first data pipeline for researching
Vietnamese-market stocks (initially the VN30 index). It fetches market
data from vnstock, runs data-quality checks, computes technical and
fundamental indicators, scores a ticker with a percentile-based composite
score, gates on data coverage (VN30 vs. non-VN30 tickers), and produces a
code-generated action label (buy_accumulate / watch / hold / reduce_exit /
stay_out — never chosen by an LLM), an ATR-based risk plan, and a
snapshot written to PostgreSQL.

Design principle: Python does the arithmetic, any future LLM only
interprets it. No LLM role, chat interface, or cron scheduler exists yet
in this phase — this is the on-demand CLI pipeline only.

Full design spec:  ../vn-trading-agent-plan_final.md
Implementation plan: ../docs/superpowers/plans/2026-09-30-vn-trading-agent-phase-0-1.md


2. WHAT'S INCLUDED (Phase 0 + Phase 1)
----------------------------------------
  providers/vnstock_provider.py   vnstock wrapper, VND unit normalization
  pipeline/calendar.py            trading-calendar lookups (fail-closed)
  pipeline/ingest.py              idempotent OHLCV/fundamentals/flow ingestion
  quality/checks.py               6 data-quality checks (schema, price-unit,
                                   abnormal-move, volume, freshness, completeness)
  pipeline/indicators.py          MA/EMA/RSI/MACD/Bollinger/ATR (pandas)
  pipeline/fundamentals.py        industry-group-specific fundamental ratios
  pipeline/scoring.py             percentile scoring, composite score,
                                   confidence, regime gate
  pipeline/action_label.py        code-only action-label decision function
  pipeline/risk_plan.py           ATR stop-loss, R:R, board-band-aware sizing
  pipeline/coverage.py            ticker resolution + non-VN30 data-coverage gate
  pipeline/theses.py              versioned investment theses (not yet wired
                                   into run_analysis — no LLM role exists yet)
  pipeline/positions.py           self-declared "I am holding this" state
  pipeline/snapshot.py            writes the run's snapshot.json
  pipeline/run_analysis.py        orchestrates all of the above end-to-end
                                   + CLI entrypoint
  db/                             Postgres schema, migrations, role setup
  ops/backup.sh, restore_test.sh  pg_dump backup + restore drill
  ops/alerting.py                 structured JSON-line logging + Telegram
                                   alert to the ops group on job failure
  mcp_server/                     MCP protocol server (stdio) exposing 8
                                   tools to Hermes/any MCP client — see
                                   section 13
  evals/compare_models.py         golden-sample harness for comparing Claude
                                   models on news-classification quality

NOT in this phase (by design — see the plan's roadmap for later phases):
  - Telegram / chat interface
  - Cron scheduler / job queue / worker
  - Any LLM-backed role (news, macro, synthesis, Bull/Bear)
  - Prediction grading / forward-test scoring
  - Data retention / archival jobs


3. REQUIREMENTS
-----------------
  - Python 3.11+
  - Docker + Docker Compose (for PostgreSQL)
  - A vnstock account/API key (Community tier) if you want real market data
  - An Anthropic API key only if you run evals/compare_models.py for real


4. SETUP
---------
  4.1  Start PostgreSQL:

         docker compose up -d postgres

       This starts Postgres 16 on 127.0.0.1:55432 (NOT the default 5432 —
       chosen to avoid colliding with any other local Postgres instance).

  4.2  Create your .env file:

         cp .env.example .env
         chmod 600 .env

       Edit .env and set real values for MCP_RO_PASSWORD, PIPELINE_RW_PASSWORD,
       RETENTION_JOB_PASSWORD (used to create restricted DB roles), and
       VNSTOCK_API_KEY / ANTHROPIC_API_KEY / TELEGRAM_BOT_TOKEN if/when you
       need them. DATABASE_URL already points at the docker-compose Postgres
       on port 55432 by default.

  4.3  Create a virtualenv and install dependencies:

         python3 -m venv .venv
         .venv/bin/pip install -r requirements.txt

  4.4  Export your .env into the shell (needed for every command below):

         export $(cat .env | xargs)

  4.5  Apply database migrations:

         .venv/bin/python -c "
         from db.connection import get_conn
         from db.migrate import apply_migrations
         from pathlib import Path
         with get_conn() as c:
             print(apply_migrations(c, Path('db/migrations')))
         "

  4.6  Create the restricted database roles (mcp_ro, pipeline_rw, retention_job):

         .venv/bin/python -m db.setup_roles

  4.7  Seed the trading calendar (required before running analysis — the
       pipeline never guesses trading days from the weekday alone):

         .venv/bin/python -c "
         from db.connection import get_conn
         from pipeline.calendar import seed_calendar_from_weekdays
         from datetime import date
         with get_conn() as c:
             seed_calendar_from_weekdays(c, date(2020, 1, 1), date(2027, 12, 31), holidays=set())
             c.commit()
         "

       This is a plain weekday seed with no Vietnamese public holidays
       excluded — good enough for local testing. Re-run periodically (at
       least once a year) so the calendar keeps covering "today"; a stale
       calendar is detected by the freshness check and flags stay_out.


5. RUNNING TESTS
------------------
  With Postgres up and .env exported (steps 4.1 and 4.4 above):

    .venv/bin/pytest -v

  All 89 tests should pass. Tests run against the real Postgres instance
  (no mocking of the database layer) but never call the real vnstock or
  Claude APIs.


6. RUNNING THE PIPELINE
--------------------------
  Analyze one ticker on demand:

    .venv/bin/python -m pipeline.run_analysis VNM

  Optional flags:

    --style long|swing     (default: long)
    --depth quick|full      (default: quick — no effect yet, no LLM roles exist)

  This will:
    1. Resolve the ticker (rejects unknown tickers with suggestions)
    2. Ingest today's OHLCV bar + fundamentals + foreign flow from vnstock
    3. Run 6 data-quality checks, logging results to data_quality_log
    4. Gate on data coverage (VN30 tickers pass automatically once seeded;
       non-VN30 tickers need >=500 days price history, liquidity, and
       >=4 quarters of fundamentals)
    5. Compute technical + fundamental snapshots and a composite score
    6. Compute an ATR-based risk plan (stop-loss, target, position size)
    7. Generate a code-only action label
    8. Write a snapshot.json under ./snapshots/, plus rows to the `runs`
       and `predictions` tables

  Re-running for the same ticker on the same day will NOT create a
  duplicate open prediction if the action label hasn't changed.

  Note: a brand-new ticker generally will NOT have 500 days of price
  history in the database on a first run (nothing backfills historical
  data yet in this phase) — expect "insufficient coverage" until a
  proper historical backfill exists. This is correct, fail-closed
  behavior, not a bug.

  Every CLI run emits JSON-line progress logs to stdout (run_started,
  run_finished, run_failed, etc. — see ops/alerting.py). If the run
  raises an exception, or finishes with a non-"ok" status (unknown
  ticker, insufficient coverage, data quality error), an alert is sent
  to the ops Telegram group (see section 12 below) so an operator
  notices without watching the terminal.


7. BACKUP / RESTORE DRILL
----------------------------
    ./ops/backup.sh           # pg_dump into ./backups/
    ./ops/restore_test.sh     # restores latest backup into a throwaway DB,
                               # compares row counts, then drops it

  Requires a pg_dump/pg_restore client matching the server's major version
  (Postgres 16). If your host has a different client version, run these
  inside the Postgres container instead:

    docker compose exec postgres bash
    # then pg_dump / pg_restore from inside the container


8. MODEL COMPARISON HARNESS (optional, costs money / or free via local Ollama)
-----------------------------------------------------------------------------
  evals/golden_news.jsonl holds 5 hand-labeled Vietnamese financial news
  examples. To compare models on classifying them:

    .venv/bin/python -m evals.compare_models

  Supported providers (edit the `targets` list in evals/compare_models.py
  to add/remove models):
    - anthropic  — requires ANTHROPIC_API_KEY (Claude models)
    - deepseek   — requires DEEPSEEK_API_KEY; DEEPSEEK_BASE_URL defaults to
                   https://api.deepseek.com (OpenAI-compatible API)
    - ollama     — local, no API key needed; OLLAMA_BASE_URL defaults to
                   http://localhost:11434/v1 (run `ollama serve` first and
                   `ollama pull <model>` for whichever model you list)

  NOT supported yet: any provider without an OpenAI- or Anthropic-compatible
  chat API (e.g. Google Gemini, xAI Grok native SDKs) — calling one raises
  UnsupportedProviderError. Add it by extending _make_client/_call_model.

  This is a manual, occasional exercise — not run by the automated test
  suite (only the pure-function helpers in evals/compare_models.py are
  unit-tested, no real API calls happen in tests).


9. CONFIGURATION
-------------------
  config/vn-rules.yaml holds every tunable threshold: coverage minimums,
  action-label score/confidence/R:R cutoffs, board bands (HOSE/HNX/UPCoM),
  lot size, and extra-ticker limits. Change these only through this file
  (never hardcode a threshold in code), and never edit it through a chat
  interface once one exists — that is reserved for Git-reviewed changes.


10. KNOWN LIMITATIONS (Phase 0 + 1 scope)
---------------------------------------------
  - No historical backfill job yet — a ticker only accumulates price
    history one day at a time as run_analysis is invoked for it.
  - pipeline/theses.py exists and is tested but is not wired into
    run_analysis yet (no LLM role produces real thesis content in this
    phase) — thesis_invalidated is always False for now.
  - action_label()'s branch precedence (e.g. "coverage check wins over
    the buy check") is correct by inspection but not yet locked in by a
    dedicated test with competing inputs — see the plan's final review
    notes before extending this function.
  - Real-vnstock end-to-end behavior (response shapes, ingest timing for
    a full 500-day backfill) has not been exercised against the live
    vnstock API in this environment — verify before production use.


12. OPS ALERTING (job failures / errors)
--------------------------------------------
  ops/alerting.py provides two things, used from pipeline/run_analysis.py's
  CLI entrypoint:
    - log_event(...)     structured JSON-line log to stdout for every run
                          (run_started / run_finished / run_failed / etc.)
    - send_ops_alert(...) posts a plain-text message via the Telegram Bot
                          API to a dedicated ops group — NOT the same
                          channel/bot Hermes uses for user-facing chat.

  Setup: create a separate Telegram group for operators, add your bot to
  it, and get that group's chat_id (e.g. via the bot's getUpdates API or
  @userinfobot). Set in .env:

    TELEGRAM_BOT_TOKEN=<your bot token>
    TELEGRAM_ALERT_CHAT_ID=<the ops group's chat_id, e.g. -1001234567890>

  If either variable is missing, send_ops_alert() logs and no-ops instead
  of raising — an alerting misconfiguration must never crash a run.

  This does NOT send successful analysis results anywhere (Hermes/the
  future worker owns that, per the spec) — it only fires on unknown
  ticker, insufficient coverage, data quality errors, and uncaught
  exceptions.


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


14. PROJECT HISTORY
-----------------------
  Built task-by-task via subagent-driven development against the plan at
  ../docs/superpowers/plans/2026-09-30-vn-trading-agent-phase-0-1.md.
  Every task went through an independent implementer + reviewer pass;
  a final whole-branch review found and fixed one cross-task issue
  (a dead fail-closed staleness check) before merge. See that plan file
  and its linked spec for full design rationale, open questions, and the
  roadmap for later phases (chat, cron, LLM roles, grading, retention).
