#!/usr/bin/env bash
# Run the test suite against the dedicated vnmcp_test database — never the live vnmcp.
# Usage: ./run_tests.sh [pytest args]     e.g. ./run_tests.sh tests/test_run_analysis.py -q
set -euo pipefail
cd "$(dirname "$0")"

set -a; . ./.env; set +a
for var in DATABASE_URL MCP_RO_DATABASE_URL PIPELINE_RW_DATABASE_URL RETENTION_JOB_DATABASE_URL; do
  export "$var=${!var%/vnmcp}/vnmcp_test"
done

PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
"$PY" -m db.create_test_db
exec "$PY" -m pytest -p no:cacheprovider "$@"
