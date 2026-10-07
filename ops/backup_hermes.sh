#!/usr/bin/env bash
# Full Hermes backup (`hermes backup`): config, .env, SOUL, memories, skills, cron, sessions of the
# default profile and every user profile — everything except the Hermes codebase and the MCP venv's
# packages (ops/setup_venv.sh rebuilds them). The zip is built in the container's /tmp, so backups
# never pile up inside ~/.hermes or get swept into the next one.
# The zip holds bot tokens, API keys and users' chats: it stays 600 here; encrypt it (gpg -c)
# before it leaves the machine. Restore: README §13.
#
# Usage: ops/backup_hermes.sh     (env: HERMES_BACKUP_DIR, HERMES_BACKUP_KEEP, HERMES_CONTAINER)
set -euo pipefail

HERMES="${HERMES_CONTAINER:-hermes-gateway}"
OUT="${HERMES_BACKUP_DIR:-$HOME/backups/hermes}"
KEEP="${HERMES_BACKUP_KEEP:-7}"
ZIP="hermes-backup-$(date +%Y%m%d_%H%M%S).zip"

mkdir -p "$OUT" && chmod 700 "$OUT"
docker exec -u hermes "$HERMES" hermes backup -o "/tmp/$ZIP" -k 0 >/dev/null
docker cp -q "$HERMES:/tmp/$ZIP" "$OUT/$ZIP"
docker exec "$HERMES" rm -f "/tmp/$ZIP"
chmod 600 "$OUT/$ZIP"
ls -1t "$OUT"/hermes-backup-*.zip | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "backup written to $OUT/$ZIP ($(du -h "$OUT/$ZIP" | cut -f1))"
