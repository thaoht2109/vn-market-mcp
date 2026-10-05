#!/bin/sh
# Build/refresh the vn-market-mcp dependency venv on the Hermes data volume
# so the MCP server (run-vn-market-mcp) has all deps, incl. vnstock.
#
# Idempotent: uv pip install skips already-satisfied packages, so re-running
# on every compose up is cheap. Runs inside the Hermes image (has uv + the
# right Python), as the `mcp-venv-init` init container.
set -e

VENV="${VN_MARKET_MCP_VENV:-/opt/data/vn-market-mcp-venv}"
REQ="${VN_MARKET_MCP_REQ:-/opt/vn-market-mcp/requirements.txt}"
# vnstock/vnai live on the vnstock private index, not public PyPI.
EXTRA_INDEX="${VN_MARKET_MCP_INDEX:-https://vnstocks.com/api/simple}"
# The Hermes-bundled uv pins a global exclude-newer cutoff that filters out
# recently-uploaded vnstock releases; override it so they resolve.
NEWER_CUTOFF="${VN_MARKET_MCP_EXCLUDE_NEWER:-2027-01-01T00:00:00Z}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "[setup_venv] creating venv at $VENV"
  uv venv "$VENV"
fi

echo "[setup_venv] installing $REQ into $VENV"
uv pip install --python "$VENV/bin/python" \
  --extra-index-url "$EXTRA_INDEX" \
  --exclude-newer "$NEWER_CUTOFF" \
  -r "$REQ"

echo "[setup_venv] done — verifying vnstock import:"
"$VENV/bin/python" -c "from vnstock import Vnstock; print('vnstock import OK')"
