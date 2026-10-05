#!/usr/bin/env bash
# Add (or update) one user on the shared Telegram bot. Does the 4 steps of README §10
# "Nhiều người dùng": Hermes profile pinned to the user (VNMCP_USER_ID), route
# user_id+chat_id -> profile, Telegram allowlist, users row for result delivery.
# Idempotent: re-running with the same args changes nothing; with new args it updates.
#
# Usage: ops/add_user.sh <tên> <telegram_user_id> [group_id]
#   <tên>              profile name, lowercase letters/digits (e.g. lan)
#   <telegram_user_id> numeric Telegram user id
#   [group_id]         optional private group (the user + the bot), e.g. -1001234567890;
#                      results are then delivered to that group instead of the private chat
set -euo pipefail
cd "$(dirname "$0")/.."

HERMES="${HERMES_CONTAINER:-hermes-gateway}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vn-market-mcp}"  # same stack when run from a git worktree
NAME="${1:-}"; TG_ID="${2:-}"; GROUP_ID="${3:-}"
if [[ ! "$NAME" =~ ^[a-z0-9]+$ || ! "$TG_ID" =~ ^[0-9]+$ || ( -n "$GROUP_ID" && ! "$GROUP_ID" =~ ^-[0-9]+$ ) ]]; then
  echo "usage: $0 <tên: a-z0-9> <telegram_user_id: số> [group_id: số âm]" >&2
  exit 2
fi
if [[ "$NAME" == "default" ]]; then echo "'default' là profile nhóm chung, không dùng cho người dùng" >&2; exit 2; fi
CHAT_ID="${GROUP_ID:-$TG_ID}"
STAMP=$(date +%Y%m%d-%H%M%S)

echo "1/4 profile $NAME"
NEW_PROFILE=0
if ! docker exec "$HERMES" test -d "/opt/data/profiles/$NAME"; then
  docker exec "$HERMES" hermes profile create "$NAME" --clone --no-alias --description "vn-market cá nhân: $NAME" >/dev/null
  NEW_PROFILE=1
fi
docker exec "$HERMES" sh -c "mkdir -p /opt/data/backups/add_user-$STAMP && cp /opt/data/config.yaml /opt/data/.env /opt/data/backups/add_user-$STAMP/ && cp /opt/data/profiles/$NAME/config.yaml /opt/data/backups/add_user-$STAMP/config.$NAME.yaml"

echo "2-3/4 VNMCP_USER_ID, profile_routes, allowlist"
docker exec -i -e NAME="$NAME" -e TG_ID="$TG_ID" -e GROUP_ID="$GROUP_ID" -e NEW_PROFILE="$NEW_PROFILE" \
  -w /opt/hermes "$HERMES" /opt/hermes/.venv/bin/python - <<'PY'
import os
import re
from pathlib import Path

import yaml

from gateway.profile_routing import match_profile_route, parse_profile_routes

name, tg_id, group_id = os.environ["NAME"], os.environ["TG_ID"], os.environ["GROUP_ID"]
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
cfg_path.write_text(text)
cfg = yaml.safe_load(cfg_path.read_text())
assert cfg["mcp_servers"]["vn-market-mcp"]["env"]["VNMCP_USER_ID"] == tg_id
assert EXT in cfg["skills"]["external_dirs"]

# A cloned profile inherits default's memory, which holds notes about other people.
if os.environ["NEW_PROFILE"] == "1":
    (prof / "memories" / "USER.md").write_text("")

# --- default config: routes for this user (replaced by name, others untouched) ---
cfg_path = home / "config.yaml"
text = cfg_path.read_text()
routes = (yaml.safe_load(text).get("gateway") or {}).get("profile_routes") or []
wanted = [{"name": f"{name}-dm", "platform": "telegram", "profile": name, "user_id": tg_id, "chat_id": tg_id}]
if group_id:
    wanted.append({"name": f"{name}-group", "platform": "telegram", "profile": name, "user_id": tg_id, "chat_id": group_id})
routes = [r for r in routes if r.get("name") not in (f"{name}-dm", f"{name}-group")] + wanted
dumped = yaml.safe_dump({"profile_routes": routes}, sort_keys=False, allow_unicode=True, default_flow_style=False)
new_block = "".join("  " + l + "\n" for l in dumped.splitlines())
lines = text.splitlines(keepends=True)
if "  profile_routes:\n" in lines:
    s = lines.index("  profile_routes:\n")
    e = s + 1
    while e < len(lines) and (lines[e].startswith("    ") or lines[e].startswith("  - ")):
        e += 1
    lines[s:e] = [new_block]
else:
    g = lines.index("gateway:\n")
    lines.insert(g + 1, new_block)
cfg_path.write_text("".join(lines))
routes = parse_profile_routes(yaml.safe_load(cfg_path.read_text())["gateway"]["profile_routes"])

def routed(user, chat):
    r = match_profile_route(routes, "telegram", chat_id=chat, user_id=user)
    return r.profile if r else "default"

assert routed(tg_id, tg_id) == name
assert routed("1", tg_id) == "default"  # someone else can't reach this user's profile
if group_id:
    assert routed(tg_id, group_id) == name
    assert routed("1", group_id) == "default"

# --- default .env: let the shared bot accept this user's DMs (and the private group) ---
env_path = home / ".env"
env = env_path.read_text()

def add_to_list(env, key, value):
    m = re.search(rf"^{key}=(.*)$", env, re.M)
    if m is None:
        return env.rstrip("\n") + f"\n{key}={value}\n"
    items = [v.strip() for v in m.group(1).split(",") if v.strip()]
    if value in items:
        return env
    return env[:m.start()] + f"{key}={','.join(items + [value])}" + env[m.end():]

env = add_to_list(env, "TELEGRAM_ALLOWED_USERS", tg_id)
if group_id:
    env = add_to_list(env, "TELEGRAM_GROUP_ALLOWED_CHATS", group_id)
env_path.write_text(env)
print(f"   routes ok: {[r.name for r in routes]}")
PY

echo "4/4 users row (kết quả gửi về chat $CHAT_ID)"
docker compose exec -T postgres psql -U vnmcp_admin -p 5433 vnmcp -v ON_ERROR_STOP=1 -q -c \
  "INSERT INTO users (user_id, chat_id) VALUES ('$TG_ID', '$CHAT_ID')
   ON CONFLICT (user_id) DO UPDATE SET chat_id = EXCLUDED.chat_id"

echo "restart $HERMES"
docker restart "$HERMES" >/dev/null
for attempt in $(seq 1 6); do
  sleep 10
  if OUT=$(docker exec "$HERMES" hermes -p "$NAME" mcp test vn-market-mcp 2>&1) && grep -q "Tools discovered" <<<"$OUT"; then
    grep -E "Connected|Tools discovered" <<<"$OUT"
    break
  fi
  [[ $attempt == 6 ]] && { echo "MCP của profile $NAME chưa kết nối được:" >&2; echo "$OUT" >&2; exit 1; }
done

echo
echo "Xong. Sao lưu cấu hình cũ: ~/.hermes/backups/add_user-$STAMP/"
echo "Người dùng cần nhắn /start cho bot chung trong chat riêng."
if [[ -n "$GROUP_ID" ]]; then
  echo "Nhóm riêng: thêm bot vào nhóm $GROUP_ID; trong nhóm phải @bot hoặc dùng lệnh /... (require_mention)."
fi
