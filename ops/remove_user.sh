#!/usr/bin/env bash
# Remove a user added by ops/add_user.sh and revoke everything tied to them, without restarting
# the gateway (the multiplexer unserves the deleted profile and stops only its bot):
#   - Hermes: the profile (own bot token, memory, sessions, config); leftovers of the old shared-bot
#     setup (profile_routes, ids in default's allowlists that no other route uses) are removed from
#     the files and dropped by the gateway at its next restart — until then they point to an unserved
#     profile, which Hermes rejects
#   - Postgres: their watchlist (watchlist_extra) and positions
# Before deleting: Hermes config + a profile archive go to ~/.hermes/backups/remove_user-<stamp>/,
# the DB rows to ./backups/remove_user-<tên>-<stamp>/*.csv. Shared data (runs, predictions,
# market data) and job history are kept.
#
# Usage: ops/remove_user.sh <tên> [--yes]
set -euo pipefail
cd "$(dirname "$0")/.."

HERMES="${HERMES_CONTAINER:-hermes-gateway}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vn-market-mcp}"  # same stack when run from a git worktree
NAME="${1:-}"; YES="${2:-}"
if [[ ! "$NAME" =~ ^[a-z0-9]+$ || "$NAME" == "default" || ( -n "$YES" && "$YES" != "--yes" ) ]]; then
  echo "usage: $0 <tên: a-z0-9, không phải default> [--yes]" >&2
  exit 2
fi
STAMP=$(date +%Y%m%d-%H%M%S)
psql_admin() { docker compose -f infrastructure/docker-compose.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -p "$PGPORT" "$POSTGRES_DB" "$@"' psql -v ON_ERROR_STOP=1 "$@" </dev/null; }  # don't eat the confirmation

# Plan (read-only) and apply share one script; MODE picks which.
hermes_py() {
  docker exec -i -e NAME="$NAME" -e MODE="$1" -w /opt/hermes "$HERMES" /opt/hermes/.venv/bin/python - <<'PY'
import os
import re
from pathlib import Path

import yaml

from gateway.profile_routing import match_profile_route, parse_profile_routes

name, mode = os.environ["NAME"], os.environ["MODE"]
home = Path("/opt/data")
cfg_path, env_path = home / "config.yaml", home / ".env"
text = cfg_path.read_text()
routes = (yaml.safe_load(text).get("gateway") or {}).get("profile_routes") or []
mine = [r for r in routes if r.get("profile") == name]
others = [r for r in routes if r.get("profile") != name]

user_id = None
prof_cfg = home / "profiles" / name / "config.yaml"
if prof_cfg.exists():
    user_id = (((yaml.safe_load(prof_cfg.read_text()).get("mcp_servers") or {}).get("vn-market-mcp") or {})
               .get("env") or {}).get("VNMCP_USER_ID")
user_id = str(user_id) if user_id else next((str(r["user_id"]) for r in mine if r.get("user_id")), None)
other_users = {str(r.get("user_id")) for r in others}
other_chats = {str(r.get("chat_id")) for r in others}
groups = sorted({str(r["chat_id"]) for r in mine if str(r.get("chat_id", "")).startswith("-")} - other_chats)

if mode == "plan":
    print(f"PROFILE_EXISTS={int(prof_cfg.exists())}")
    print(f"TG_ID={user_id or ''}")
    print(f"ROUTES={','.join(r.get('name', '?') for r in mine)}")
    print(f"GROUPS={','.join(groups)}")
    raise SystemExit

# --- drop this user's routes (others untouched) ---
lines = text.splitlines(keepends=True)
if mine and "  profile_routes:\n" in lines:  # untouched (formatting too) when the user had no route
    s = lines.index("  profile_routes:\n")
    e = s + 1
    while e < len(lines) and (lines[e].startswith("    ") or lines[e].startswith("  - ")):
        e += 1
    block = ""
    if others:
        dumped = yaml.safe_dump({"profile_routes": others}, sort_keys=False, allow_unicode=True, default_flow_style=False)
        block = "".join("  " + l + "\n" for l in dumped.splitlines())
    lines[s:e] = [block] if block else []
    cfg_path.write_text("".join(lines))
left = parse_profile_routes((yaml.safe_load(cfg_path.read_text()).get("gateway") or {}).get("profile_routes") or [])
assert not [r for r in left if r.profile == name]
if user_id:
    r = match_profile_route(left, "telegram", chat_id=user_id, user_id=user_id)
    assert r is None or r.profile != name

# --- revoke Telegram access, keeping ids that another route still needs ---
env = orig_env = env_path.read_text()

def drop_from_list(env, key, values):
    m = re.search(rf"^{key}=(.*)$", env, re.M)
    if m is None:
        return env
    items = [v.strip() for v in m.group(1).split(",") if v.strip() and v.strip() not in values]
    return env[:m.start()] + f"{key}={','.join(items)}" + env[m.end():]

if user_id and user_id not in other_users:
    env = drop_from_list(env, "TELEGRAM_ALLOWED_USERS", {user_id})
env = drop_from_list(env, "TELEGRAM_GROUP_ALLOWED_CHATS", set(groups))
if env != orig_env:  # an unchanged .env keeps its signature: the default bot is not rebuilt
    env_path.write_text(env)
print(f"   routes left: {[r.name for r in left]}")
PY
}

PLAN=$(hermes_py plan)
PROFILE_EXISTS=$(sed -n 's/^PROFILE_EXISTS=//p' <<<"$PLAN")
TG_ID=$(sed -n 's/^TG_ID=//p' <<<"$PLAN")
ROUTES=$(sed -n 's/^ROUTES=//p' <<<"$PLAN")
GROUP_IDS=$(sed -n 's/^GROUPS=//p' <<<"$PLAN")  # not GROUPS: bash reserves it
if [[ "$PROFILE_EXISTS" == 0 && -z "$ROUTES" ]]; then
  echo "Không có profile hay luật định tuyến nào tên '$NAME'." >&2
  exit 1
fi
[[ -z "$TG_ID" || "$TG_ID" =~ ^[0-9]+$ ]] || { echo "VNMCP_USER_ID lạ: '$TG_ID'" >&2; exit 1; }

COUNTS="(không rõ user id)"
if [[ -n "$TG_ID" ]]; then
  COUNTS=$(psql_admin -tA -F' ' -c "SELECT
      (SELECT count(*) FROM watchlist_extra WHERE added_by = '$TG_ID'),
      (SELECT count(*) FROM positions WHERE declared_by = '$TG_ID')" \
    | awk '{print $1 " mã theo dõi, " $2 " vị thế"}')
fi
cat <<EOF
Sẽ xóa người dùng '$NAME' (telegram user id: ${TG_ID:-?}):
  - profile Hermes và bot riêng: $([[ "$PROFILE_EXISTS" == 1 ]] && echo "có, sẽ lưu trữ rồi xóa" || echo "không còn")
  - luật định tuyến bot chung (cũ): ${ROUTES:-không có}
  - quyền Telegram: user id khỏi TELEGRAM_ALLOWED_USERS${GROUP_IDS:+, nhóm $GROUP_IDS khỏi TELEGRAM_GROUP_ALLOWED_CHATS}
  - dữ liệu riêng: $COUNTS
EOF
if [[ -z "$YES" ]]; then
  read -r -p "Gõ tên '$NAME' để xác nhận: " CONFIRM || CONFIRM=""
  [[ "$CONFIRM" == "$NAME" ]] || { echo "Hủy, chưa thay đổi gì."; exit 1; }
fi

echo "1/4 sao lưu"
HB="/opt/data/backups/remove_user-$STAMP"
docker exec -u hermes "$HERMES" mkdir -p "$HB"
docker exec -u hermes "$HERMES" cp /opt/data/config.yaml /opt/data/.env "$HB/"
if [[ "$PROFILE_EXISTS" == 1 ]]; then
  docker exec "$HERMES" hermes profile export "$NAME" -o "$HB/$NAME.tar.gz"
fi
DB_BACKUP="backups/remove_user-$NAME-$STAMP"
if [[ -n "$TG_ID" ]]; then
  mkdir -p "$DB_BACKUP"
  for q in "watchlist_extra:added_by" "positions:declared_by"; do
    psql_admin -c "COPY (SELECT * FROM ${q%%:*} WHERE ${q##*:} = '$TG_ID') TO STDOUT WITH CSV HEADER" > "$DB_BACKUP/${q%%:*}.csv"
  done
fi

echo "2/4 thu hồi luật định tuyến và quyền Telegram"
hermes_py apply

echo "3/4 xóa dữ liệu riêng trong DB"
if [[ -n "$TG_ID" ]]; then
  psql_admin -q -c "BEGIN;
    DELETE FROM watchlist_extra WHERE added_by = '$TG_ID';
    DELETE FROM positions WHERE declared_by = '$TG_ID';
    COMMIT;"
fi

echo "4/4 xóa profile, dừng bot riêng (không restart gateway)"
if [[ "$PROFILE_EXISTS" == 1 ]]; then
  docker exec -u hermes "$HERMES" hermes profile delete "$NAME" --yes >/dev/null
fi
# profile delete already asks the gateway to unserve it; ask again and check, in case that was missed.
docker exec -i -u hermes -e NAME="$NAME" -w /opt/hermes "$HERMES" /opt/hermes/.venv/bin/python - <<'PY'
import os
from pathlib import Path

from gateway.control_socket import rescan_gateway_profiles

answer = rescan_gateway_profiles(Path("/opt/data"), timeout=8.0) or {}
served = answer.get("served_profiles")
if served is not None and os.environ["NAME"] in served:
    raise SystemExit(f"gateway vẫn phục vụ profile {os.environ['NAME']}: {answer}")
print(f"   gateway đang phục vụ: {served if served is not None else '(không trả lời; tự quét lại trong 30 giây)'}")
PY

echo
echo "Xong, không restart gateway. Bot riêng của '$NAME' đã ngừng; tin nhắn của họ trong nhóm chung vẫn vào profile default."
echo "Nên thu hồi token của bot đó ở @BotFather (/revoke hoặc /deletebot)."
echo "Sao lưu Hermes (cấu hình + profile): ~/.hermes/backups/remove_user-$STAMP/"
[[ -n "$TG_ID" ]] && echo "Sao lưu dữ liệu DB: $DB_BACKUP/"
echo "Khôi phục: hermes profile import <archive>, chép lại config.yaml/.env, rồi ops/add_user.sh và nạp lại các CSV."
