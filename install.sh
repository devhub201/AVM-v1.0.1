#!/usr/bin/env bash
# AxionHost bot installer / configurator
#   ./install.sh                       interactive install (asks token, IDs, logo URL)
#   ./install.sh set <key> <value>     change one config value   e.g.  ./install.sh set logo_url https://.../logo.png
#   ./install.sh logo <url>            shortcut for: set logo_url
#   ./install.sh banner <url>          shortcut for: set banner_url
#   ./install.sh show                  print config (token hidden)
#   ./install.sh service               install + start systemd service
#   ./install.sh restart | logs
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="$DIR/config.json"
BOT="$DIR/axionhost_bot.py"
VENV="$DIR/venv"
SVC="axionhost-bot"

die() { echo "ERROR: $*" >&2; exit 1; }
need_py() { command -v python3 >/dev/null || die "python3 not found (apt install python3 python3-venv python3-pip)"; }

cfg_set() {  # key value
  python3 - "$CFG" "$1" "$2" <<'PY'
import json, os, sys
path, key, value = sys.argv[1:4]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
data[key] = value
with open(path, "w") as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
PY
}

check_url() {
  local url="$1"
  [[ "$url" =~ ^https?:// ]] || die "URL must start with http:// or https://"
  if [[ "$url" == *cdn.discordapp.com* || "$url" == *media.discordapp.net* ]]; then
    die "Discord CDN links expire in ~24h. Use a permanent link (GitHub raw URL) or run .setlogo in Discord with the image attached."
  fi
  local ct
  ct=$(curl -sIL --max-time 15 -o /dev/null -w '%{http_code} %{content_type}' "$url") || die "Could not reach $url"
  [[ "$ct" == 200\ image/* ]] || die "URL is not a reachable image (got: $ct)"
}

setup_venv() {
  need_py
  if [ ! -d "$VENV" ]; then python3 -m venv "$VENV" || die "venv failed — run: apt install python3-venv"; fi
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$DIR/requirements.txt" || die "pip install failed"
}

ask() {  # prompt key [secret]
  local prompt="$1" key="$2" val=""
  if [ "${3:-}" = secret ]; then read -r -s -p "$prompt: " val; echo; else read -r -p "$prompt: " val; fi
  [ -n "$val" ] && cfg_set "$key" "$val"
  return 0
}

case "${1:-install}" in
  install)
    echo "== AxionHost installer =="
    setup_venv
    [ -f "$CFG" ] || echo '{}' > "$CFG"
    cfg_set brand_name AxionHost
    ask "Discord bot token" token secret
    ask "Your Discord user ID (main admin)" main_admin_id
    ask "Your Discord server (guild) ID" guild_id
    ask "This node's public IP" server_ip
    ask "Node name (e.g. Node-1)" node_name
    read -r -p "Logo URL (direct image link, Enter to skip): " L
    if [ -n "$L" ]; then check_url "$L"; cfg_set logo_url "$L"; fi
    read -r -p "Banner URL (Enter to skip): " B
    if [ -n "$B" ]; then check_url "$B"; cfg_set banner_url "$B"; fi
    echo
    echo "Done. Next:  ./install.sh service     (runs the bot 24/7 with systemd)"
    echo "Then in Discord: .setalertchannel #alerts   and   .setstatuschannel #node-status"
    ;;
  set)
    [ $# -ge 3 ] || die "usage: $0 set <key> <value>"
    [[ "$2" == *url ]] && [ -n "$3" ] && check_url "$3"
    cfg_set "$2" "$3"; echo "Saved $2. Restart the bot:  ./install.sh restart" ;;
  logo)   [ $# -ge 2 ] || die "usage: $0 logo <url>";   check_url "$2"; cfg_set logo_url "$2";   echo "Logo saved. ./install.sh restart" ;;
  banner) [ $# -ge 2 ] || die "usage: $0 banner <url>"; check_url "$2"; cfg_set banner_url "$2"; echo "Banner saved. ./install.sh restart" ;;
  show)
    [ -f "$CFG" ] || die "no config.json yet — run ./install.sh"
    python3 -c "import json;d=json.load(open('$CFG'));d['token']='***hidden***' if d.get('token') else 'NOT SET';print(json.dumps(d,indent=2))" ;;
  service)
    [ "$(id -u)" -eq 0 ] || die "run as root (sudo ./install.sh service)"
    [ -f "$CFG" ] || die "no config.json — run ./install.sh first"
    python3 -c "import json,sys;d=json.load(open('$CFG'));sys.exit(0 if d.get('token') else 1)" || die "token missing in config.json"
    [ -x "$VENV/bin/python" ] || setup_venv
    cat > "/etc/systemd/system/$SVC.service" <<UNIT
[Unit]
Description=AxionHost Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$VENV/bin/python $BOT
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload && systemctl enable --now "$SVC"
    sleep 3
    systemctl is-active --quiet "$SVC" && echo "Bot is running. Logs: ./install.sh logs" || { journalctl -u "$SVC" -n 30 --no-pager; die "service failed to start"; } ;;
  restart) systemctl restart "$SVC" && echo restarted ;;
  logs)    journalctl -u "$SVC" -f -n 50 ;;
  *) die "unknown command '$1' (install | set | logo | banner | show | service | restart | logs)" ;;
esac
