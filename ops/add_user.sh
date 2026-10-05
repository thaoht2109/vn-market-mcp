#!/usr/bin/env bash
# Add (or update) one user with their OWN Telegram bot. No gateway or worker restart: the Hermes
# multiplexer hot-serves a new/changed profile and starts only that profile's bot (other users'
# bots are never touched), and the worker reads secrets/users.env on every send.
#   - Hermes profile <tên>: VNMCP_USER_ID pinned, shared skill dir, the bot token and an allowlist
#     of just this user in the profile's .env
#   - secrets/users.env + users row: where the worker sends this user's results
#   - leftovers of the old shared-bot setup (<tên>-dm/-group routes, ids in default's allowlists)
#     are removed from the files; the running gateway drops them at its next restart
# Idempotent: re-running with the same args changes nothing; with new args it updates.
#
# Usage: ops/add_user.sh <tên> <telegram_user_id> <bot_token> [group_id]
#   <tên>              profile name, lowercase letters/digits (e.g. lan)
#   <telegram_user_id> numeric Telegram user id of the owner
#   <bot_token>        token of a bot created for this user with @BotFather
#   [group_id]         optional private group (the user + their bot), e.g. -1001234567890;
#                      results are then delivered to that group instead of the private chat
set -euo pipefail
cd "$(dirname "$0")/.."

HERMES="${HERMES_CONTAINER:-hermes-gateway}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vn-market-mcp}"  # same stack when run from a git worktree
NAME="${1:-}"; TG_ID="${2:-}"; BOT_TOKEN="${3:-}"; GROUP_ID="${4:-}"
if [[ ! "$NAME" =~ ^[a-z0-9]+$ || "$NAME" == "default" || ! "$TG_ID" =~ ^[0-9]+$ \
      || ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ || ( -n "$GROUP_ID" && ! "$GROUP_ID" =~ ^-[0-9]+$ ) ]]; then
  echo "usage: $0 <tên: a-z0-9, không phải default> <telegram_user_id: số> <bot_token từ @BotFather> [group_id: số âm]" >&2
  exit 2
fi
CHAT_ID="${GROUP_ID:-$TG_ID}"
TOKEN_ENV="TELEGRAM_BOT_TOKEN_${NAME^^}"
STAMP=$(date +%Y%m%d-%H%M%S)
START_TS=$(date -u +"%Y-%m-%d %H:%M:%S")

echo "1/5 kiểm tra bot token"
docker exec -i -e NAME="$NAME" -e BOT_TOKEN="$BOT_TOKEN" -e SKIP_GETME="${ADD_USER_SKIP_GETME:-0}" -w /opt/hermes "$HERMES" /opt/hermes/.venv/bin/python - <<'PY'
import json
import os
import re
import urllib.request
from pathlib import Path

name, token = os.environ["NAME"], os.environ["BOT_TOKEN"]
home = Path("/opt/data")
# Two profiles polling one token steal each other's updates: refuse a token already in use.
for env in [home / ".env", *sorted((home / "profiles").glob("*/.env"))]:
    owner = "default" if env.parent == home else env.parent.name
    if owner != name and re.search(rf"^TELEGRAM_BOT_TOKEN={re.escape(token)}\s*$", env.read_text(), re.M):
        raise SystemExit(f"token này đang được profile '{owner}' dùng")
if os.environ.get("SKIP_GETME") == "1":  # tests only: a fake token
    raise SystemExit(0)
try:
    me = json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15))["result"]
except Exception as exc:
    raise SystemExit(f"Telegram từ chối token (getMe): {exc}")
print(f"   bot @{me['username']} (id {me['id']})")
PY

echo "2/5 profile $NAME"
NEW_PROFILE=0
if ! docker exec "$HERMES" test -d "/opt/data/profiles/$NAME"; then
  docker exec -u hermes "$HERMES" hermes profile create "$NAME" --clone --no-alias --description "vn-market cá nhân: $NAME" >/dev/null
  NEW_PROFILE=1
fi
docker exec -u hermes "$HERMES" sh -c "mkdir -p /opt/data/backups/add_user-$STAMP && cp /opt/data/config.yaml /opt/data/.env /opt/data/backups/add_user-$STAMP/ && cp /opt/data/profiles/$NAME/config.yaml /opt/data/backups/add_user-$STAMP/config.$NAME.yaml && cp /opt/data/profiles/$NAME/.env /opt/data/backups/add_user-$STAMP/env.$NAME"

echo "3/5 cấu hình profile (VNMCP_USER_ID, skill chung, bot riêng, chỉ chủ bot được chat)"
docker exec -i -u hermes -e NAME="$NAME" -e TG_ID="$TG_ID" -e GROUP_ID="$GROUP_ID" -e BOT_TOKEN="$BOT_TOKEN" \
  -e NEW_PROFILE="$NEW_PROFILE" -w /opt/hermes "$HERMES" /opt/hermes/.venv/bin/python - <<'PY'
import os
import re
from pathlib import Path

import yaml

name, tg_id, group_id, token = (os.environ[k] for k in ("NAME", "TG_ID", "GROUP_ID", "BOT_TOKEN"))
EXT = "/opt/vn-market-mcp/.hermes/skills"
home = Path("/opt/data")
prof = home / "profiles" / name

# --- profile config: pin the user, make sure the shared skill dir is loaded ---
cfg_path = prof / "config.yaml"
lines = cfg_path.read_text().splitlines(keepends=True)
start = next(i for i, l in enumerate(lines) if l == "  vn-market-mcp:\n")
env_i = next(i for i in range(start + 1, len(lines)) if lines[i] == "    env:\n")
end = env_i + 1
while end < len(lines) and lines[end].startswith("      "):
    end += 1
block = [l for l in lines[env_i + 1:end] if not l.lstrip().startswith("VNMCP_USER_ID:")]
lines[env_i + 1:end] = block + [f"      VNMCP_USER_ID: '{tg_id}'\n"]
text = "".join(lines)
if EXT not in text:
    text = text.replace("  external_dirs: []\n", f"  external_dirs:\n    - {EXT}\n", 1)
cfg = yaml.safe_load(text)
assert cfg["mcp_servers"]["vn-market-mcp"]["env"]["VNMCP_USER_ID"] == tg_id
assert EXT in cfg["skills"]["external_dirs"]
if cfg_path.read_text() != text:
    cfg_path.write_text(text)

# A cloned profile inherits default's memory, which holds notes about other people.
if os.environ["NEW_PROFILE"] == "1":
    (prof / "memories" / "USER.md").write_text("")

# --- profile .env: its own bot, answering only its owner (and their private group) ---
def set_key(env, key, value):
    pattern = re.compile(rf"^{key}=.*$", re.M)
    if value is None:
        return pattern.sub("", env).replace("\n\n\n", "\n\n")
    if pattern.search(env):
        return pattern.sub(f"{key}={value}", env, count=1)
    return env.rstrip("\n") + f"\n{key}={value}\n"

env_path = prof / ".env"
env = env_path.read_text()
new = set_key(env, "TELEGRAM_BOT_TOKEN", token)
new = set_key(new, "TELEGRAM_ALLOWED_USERS", tg_id)
new = set_key(new, "TELEGRAM_GROUP_ALLOWED_CHATS", group_id or None)
if new != env:  # an unchanged file keeps its signature: no needless reconnect of this bot
    env_path.write_text(new)
print("   profile ok")
PY

echo "4/5 nơi nhận kết quả (secrets/users.env, bảng users → chat $CHAT_ID)"
mkdir -p secrets && chmod 700 secrets
touch secrets/users.env && chmod 600 secrets/users.env
python3 - "$TOKEN_ENV" "$BOT_TOKEN" secrets/users.env <<'PY'
import sys
from pathlib import Path

key, value, path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
lines = [l for l in path.read_text().splitlines() if not l.startswith(f"{key}=")]
path.write_text("\n".join(lines + [f"{key}={value}"]) + "\n")
PY
docker compose exec -T postgres psql -U vnmcp_admin -p 5433 vnmcp -v ON_ERROR_STOP=1 -q -c \
  "INSERT INTO users (user_id, chat_id, bot_token_env) VALUES ('$TG_ID', '$CHAT_ID', '$TOKEN_ENV')
   ON CONFLICT (user_id) DO UPDATE SET chat_id = EXCLUDED.chat_id, bot_token_env = EXCLUDED.bot_token_env" </dev/null

echo "5/5 dọn cấu hình bot chung cũ (nếu có) và bật bot riêng (không restart)"
docker exec -i -u hermes -e NAME="$NAME" -e TG_ID="$TG_ID" -w /opt/hermes "$HERMES" /opt/hermes/.venv/bin/python - <<'PY'
import os
import re
from pathlib import Path

import yaml

from gateway.control_socket import rescan_gateway_profiles

name, tg_id = os.environ["NAME"], os.environ["TG_ID"]
home = Path("/opt/data")

# Shared-bot leftovers (pre-own-bot setup): written to the files only; the running gateway keeps its
# in-memory copy until its next restart, which is harmless (they route to the same profile).
cfg_path = home / "config.yaml"
text = cfg_path.read_text()
routes = (yaml.safe_load(text).get("gateway") or {}).get("profile_routes") or []
mine = [r for r in routes if r.get("profile") == name]
if mine:
    others = [r for r in routes if r.get("profile") != name]
    lines = text.splitlines(keepends=True)
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
    others_users = {str(r.get("user_id")) for r in others}
    others_chats = {str(r.get("chat_id")) for r in others}
    env_path = home / ".env"
    env = env_path.read_text()
    for key, drop in (("TELEGRAM_ALLOWED_USERS", {tg_id} - others_users),
                      ("TELEGRAM_GROUP_ALLOWED_CHATS", {str(r["chat_id"]) for r in mine if str(r.get("chat_id", "")).startswith("-")} - others_chats)):
        m = re.search(rf"^{key}=(.*)$", env, re.M)
        if m and drop:
            items = [v.strip() for v in m.group(1).split(",") if v.strip() and v.strip() not in drop]
            env = env[:m.start()] + f"{key}={','.join(items)}" + env[m.end():]
    env_path.write_text(env)
    print(f"   bỏ luật bot chung: {[r.get('name') for r in mine]} (gateway áp dụng ở lần restart tới)")

answer = rescan_gateway_profiles(home, timeout=8.0)
print(f"   gateway: {answer}")
if answer is None:
    print("   gateway không trả lời lệnh quét; nó tự quét lại trong 30 giây")
PY

# The bot connects in the background; the gateway logs "✓/✗ telegram ... (profile: <tên>)".
# Wait for a line from this run; none (nothing changed, so no reconnect) → judge by the latest one.
STATUS=""
for _ in $(seq 1 9); do
  sleep 5
  STATUS=$(docker exec "$HERMES" awk -v t="$START_TS" '$1" "$2 >= t' /opt/data/logs/gateway.log \
           | grep -E "telegram .*\(profile: $NAME\)" | tail -1 || true)
  [[ -n "$STATUS" ]] && break
done
[[ -n "$STATUS" ]] || STATUS=$(docker exec "$HERMES" grep -E "telegram .*\(profile: $NAME\)" /opt/data/logs/gateway.log | tail -1 || true)
echo "   $STATUS"
if ! grep -q "✓ telegram" <<<"$STATUS"; then
  echo "Bot của profile $NAME chưa kết nối được Telegram (xem log trên và: docker logs $HERMES)." >&2
  exit 1
fi
docker exec "$HERMES" hermes -p "$NAME" mcp test vn-market-mcp 2>&1 | grep -E "Connected|Tools discovered|rror" || true

echo
echo "Xong, không restart gateway hay worker. Sao lưu cấu hình cũ: ~/.hermes/backups/add_user-$STAMP/"
echo "Người dùng nhắn /start cho bot riêng của mình rồi nhắn thử một câu hỏi."
if [[ -n "$GROUP_ID" ]]; then
  echo "Nhóm riêng: thêm bot vào nhóm $GROUP_ID; trong nhóm phải @bot hoặc dùng lệnh /... (require_mention)."
fi
