#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
pg_dump "$DATABASE_URL" -F c -f "$BACKUP_DIR/vnmcp_${STAMP}.dump"
echo "backup written to $BACKUP_DIR/vnmcp_${STAMP}.dump"
