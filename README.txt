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

  PREFER RUNNING THIS INSIDE THE WORKER CONTAINER, not from the host
  venv directly — the host .env's DATABASE_URL points at
  127.0.0.1:55432 (the published port), which has been observed to be
  transiently unreachable for a moment while `docker compose up -d
  worker`/`build worker` is recreating containers, firing a spurious
  "LỖI khi chạy" ops alert for an error that had already resolved
  itself. Inside the worker container, the same DB is reached via the
  stable in-network address (postgres:5433), which isn't affected by
  the host port being briefly unavailable:

    docker compose exec worker python -m pipeline.run_analysis VNM

  (pipeline/run_analysis.py's __main__ block also retries up to 3
  times with backoff on psycopg.OperationalError before alerting, as a
  second line of defense for whichever path you run it from.)


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

  Connecting Hermes Agent (Nous Research) — VERIFIED working setup:
    Hermes here runs via the compose stack at
    ../hermes_agent/hermes-docker-compose/docker-compose.yml, which was
    extended (not part of vn-market-mcp itself) with:
      - a read-only bind mount of this project into the gateway
        container at /opt/vn-market-mcp
      - the gateway container joined onto vn-market-mcp's own compose
        network (`vn-market-mcp_default`), so it can reach Postgres by
        its service name `postgres` instead of `localhost:55432`
          (the vn-market-mcp postgres port is only published on
          127.0.0.1 on the host, so the container-to-container route via
          the shared network is required, not host.docker.internal)

    NOTE: the postgres service's INTERNAL container port is 5433, not the
    Postgres default 5432 — see docker-compose.yml's PGPORT/`-p 5433`.
    This host machine has a separate, unrelated project's Postgres also
    listening on 5432, protected by a host iptables DOCKER-USER rule
    that DROPs all TCP to port 5432 except from that other project's
    specific subnets. vn-market-mcp's own Docker network was never in
    that allowlist, so any container-to-container connection on 5432
    silently hung until timeout. Moving vn-market-mcp's postgres to
    listen on 5433 internally (the external host publish, 127.0.0.1:
    55432, is unchanged) sidesteps the rule entirely without touching
    the other project's firewall config. If you see hangs ending in
    "connection timeout expired" from a *_DATABASE_URL that has
    "postgres:5432" in it, this is almost certainly why — the working
    values use postgres:5433.

    Inside the gateway container, a dedicated venv (NOT the repo's own
    .venv, which is host-only) was created under the persistent
    /opt/data volume and the project's requirements.txt installed there:
      uv venv /opt/data/vn-market-mcp-venv
      uv pip install --python /opt/data/vn-market-mcp-venv/bin/python \
          -r /opt/vn-market-mcp/requirements.txt

    Gotcha found while wiring this up: launching the server with cwd !=
    /opt/vn-market-mcp makes Python resolve `import providers` against
    Hermes's OWN internal /opt/hermes/providers package instead of this
    project's providers/ (both are top-level packages named
    "providers" — a naming collision, not a vn-market-mcp bug). Fixed
    with a one-line wrapper script that cd's first:
      /opt/data/vn-market-mcp-venv/bin/run-vn-market-mcp:
        #!/bin/sh
        cd /opt/vn-market-mcp
        exec /opt/data/vn-market-mcp-venv/bin/python -m mcp_server.server

    Registered with Hermes's own CLI (uses postgres:5433, the in-network
    port, and the changeme dev passwords from .env — replace with real
    values for anything beyond local dev):
      docker compose exec gateway hermes mcp add vn-market-mcp \
        --command /opt/data/vn-market-mcp-venv/bin/run-vn-market-mcp \
        --env MCP_RO_DATABASE_URL=postgresql://mcp_ro:<pw>@postgres:5433/vnmcp \
              PIPELINE_RW_DATABASE_URL=postgresql://pipeline_rw:<pw>@postgres:5433/vnmcp \
        --connect-timeout 60

    Verify anytime with:
      docker compose exec gateway hermes mcp test vn-market-mcp
      docker compose exec gateway hermes mcp list

    Confirmed: all 8 tools discovered and enabled
    (`hermes mcp test vn-market-mcp` → "Tools discovered: 8").

    Separately, the gateway's own /opt/data/config.yaml had a
    pre-existing YAML indentation bug (a stray 4-space `- mcp-codegraph`
    line under platform_toolsets.cli around line 548) and a permission
    issue (SOUL.md and friends owned by host UID 1000, unreadable/
    unwritable by the container's `hermes` user, UID 10000) that blocked
    the gateway from starting cleanly — both pre-dated this integration
    and were fixed by correcting the indentation and
    `chown -R hermes:hermes /opt/data` inside the container. Unrelated
    to vn-market-mcp but worth knowing if the gateway container is ever
    recreated from a stale /opt/data.

    IMPORTANT — MCP stdio transport requires stdout to carry ONLY
    JSON-RPC frames. ops/alerting.py originally logged structured events
    to sys.stdout, which corrupted every tool call over the real stdio
    transport ("Failed to parse JSONRPC message from server" on the
    Hermes side) — invisible in the prior session's MCP tests because
    they used the SDK's in-memory test client, not a real subprocess.
    Fixed by switching the handler to sys.stderr (ops/alerting.py:13);
    stderr is still captured in the gateway's own container logs, so no
    log events are lost, they just don't collide with the protocol
    stream anymore.

  Skill `vn-stock-analyze` (chat routing):
    A Hermes skill at .hermes/skills/vn-market/vn-stock-analyze/SKILL.md
    routes natural-language chat ("phân tích HPG", "HPG hôm nay sao
    rồi?", "vì sao stop-loss đặt ở đó?") to the right MCP tool call. It
    does NOT register real slash commands — Hermes has no plugin hook
    for that (gateway/slash_commands.py is hardcoded in Hermes core) —
    so /chay, /chitiet, /trangthai are natural-language patterns the
    skill recognizes, not registered commands.

    Load it into a running gateway:
      docker compose exec gateway hermes skills trust /opt/vn-market-mcp
      docker compose exec gateway hermes chat -q 'Bạn có skill nào tên
        vn-stock-analyze đang active không?' --oneshot

    GOTCHA — Hermes only loads project-local skills (.hermes/skills/)
    when the trusted directory has a .git ancestor (anti-prompt-
    injection: an untrusted clone can't plant a skill by merely looking
    like a project — see agent/skill_utils.py::find_project_root in
    Hermes's own source). vn-market-mcp/ is a subdirectory of a larger
    monorepo and has no .git of its own; the real .git lives two levels
    up and is outside this repo's own bind mount into the gateway
    container. Fixed with a git-worktree-style gitlink file committed at
    vn-market-mcp/.git (content: `gitdir: ../../.git`) — this satisfies
    Hermes's `.exists()` check without duplicating any repo history or
    changing how git itself treats this directory (`git status`/`git
    log` from vn-market-mcp/ still transparently operate on the
    monorepo). If this file is ever accidentally deleted, the skill will
    silently stop loading (`hermes skills list` and `-s
    vn-stock-analyze` will both report it as unknown) with no error
    pointing back to this cause.

    Manually verify routing (non-interactive, -v shows tool calls).
    Confirmed via manual test on 2026-09-30:
      docker compose exec gateway hermes chat --provider deepseek \
        -m deepseek-flash -q 'phân tích HPG' --oneshot -v
      # captured reasoning: "phân tích HPG" → run_analysis(ticker="HPG",
      # style="long", depth="full") — matches the skill's routing table
      # exactly (full analysis phrasing → depth="full", not "quick").

    (`hermes chat -q ...` needs a connected model provider first — see
    `hermes auth add <provider>`; a bare `hermes config set model
    <provider>/<model>` was NOT sufficient by itself in testing, use
    `--provider <name> -m <model>` explicitly on the hermes chat command
    if `hermes chat -q` reports "not connected to any AI provider yet"
    despite a credential being added.)

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
