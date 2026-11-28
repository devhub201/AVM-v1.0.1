"""Legacy Cloud Pterodactyl installer for AVM.

Adds a "Panel" button to the VPS manage screen. One click installs a Pterodactyl Panel (+ optional Wings)
inside the VPS container, with live progress in Discord.

Flow
  1. Check the VPS (running, supported OS, enough RAM/disk).
  2. Choose the public IP:
       1) Default IP: the bot's own IP, panel and wings ports are forwarded for you.
       2) Add your IP: paste a Serververse Transit token. If it connects, the Transit IP is used;
          if it fails, the setup falls back to option 1 and carries on.
  3. Five short questions (email, username, password, timezone, install Wings?).
  4. The installer runs inside the container. It is idempotent: every step leaves a marker, so "Retry"
     resumes where it stopped. Progress is a RUN|idx|total|name / FAIL|idx|name|msg / DONE state file.
  5. When it finishes you get the panel URL and an ephemeral "Show login" button.

Loaded from the bottom of avm_bot.py:  lc_ptero.setup(sys.modules[__name__])
"""

import asyncio
import base64
import ipaddress
import json
import os
import re
import secrets
import shlex
import string
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

WORKDIR = "/root/lc-ptero"
SUPPORTED_OS = ("ubuntu-22.04", "ubuntu-24.04", "debian-12")
MIN_RAM_MB = 1024
MIN_DISK_MB = 6000
STEPS = ["Preparing system", "PHP and web server", "Database and cache", "Panel files",
         "Panel setup and admin", "Docker", "Wings", "Node and finish"]
TRANSIT_URL = "https://labs-api.serververs.com/api/v0.3/provision/install.sh"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
USER_RE = re.compile(r"^[a-z0-9_]{3,32}$")
PASS_RE = re.compile(r"^[A-Za-z0-9!@#%^_+=.-]{8,64}$")
TZ_RE = re.compile(r"^[A-Za-z]+(?:/[A-Za-z0-9_+-]+){0,2}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,120}$")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
GAME_PORT_COUNT = 5


# ----------------------------------------------------------------------------- pure helpers
def parse_token(text: str) -> Optional[str]:
    """Accept a bare token or the whole `bash <(curl ...) --token XYZ` command."""
    t = (text or "").strip()
    m = re.search(r"--token[=\s]+['\"]?([A-Za-z0-9_-]+)", t)
    cand = m.group(1) if m else t
    return cand if TOKEN_RE.match(cand) else None


def redact(text: str, secrets_: List[str]) -> str:
    for s in secrets_:
        if s:
            text = text.replace(s, "***")
    return text


def public_ipv4s(text: str) -> List[str]:
    found: List[str] = []
    for cand in IPV4_RE.findall(text or ""):
        try:
            ip = ipaddress.ip_address(cand)
        except ValueError:
            continue
        if ip.is_global and cand not in found:
            found.append(cand)
    return found


def make_password() -> str:
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    chars = [secrets.choice(p) for p in pools] + [secrets.choice(string.ascii_letters + string.digits) for _ in range(13)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def password_problem(pw: str) -> Optional[str]:
    if not PASS_RE.match(pw):
        return "Password 8-64 characters, only letters, digits and ! @ # % ^ _ + = . -"
    if not (re.search(r"[a-z]", pw) and re.search(r"[A-Z]", pw) and re.search(r"\d", pw)):
        return "Password needs a lowercase letter, an uppercase letter and a digit."
    return None


def validate_answers(email: str, user: str, pw: str, tz: str, wings: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    email, user, tz = (email or "").strip(), (user or "").strip().lower() or "admin", (tz or "").strip() or "Asia/Kolkata"
    if not EMAIL_RE.match(email):
        return None, "That email does not look right."
    if not USER_RE.match(user):
        return None, "Username: 3-32 characters, lowercase letters, digits and underscore."
    pw = (pw or "").strip()
    generated = not pw
    if generated:
        pw = make_password()
    else:
        problem = password_problem(pw)
        if problem:
            return None, problem
    if not TZ_RE.match(tz):
        return None, "Timezone must look like Asia/Kolkata."
    w = (wings or "yes").strip().lower()
    if w not in ("yes", "no", "y", "n", "haan", "nahi", "1", "0"):
        return None, "Install Wings: type yes or no."
    return {"email": email, "user": user, "password": pw, "generated": generated, "tz": tz,
            "wings": w in ("yes", "y", "haan", "1")}, None


def parse_state(line: str) -> Dict[str, Any]:
    """RUN|idx|total|name   FAIL|idx|name|msg   DONE"""
    line = (line or "").strip()
    parts = line.split("|")
    try:
        if parts[0] == "RUN" and len(parts) >= 4:
            return {"kind": "run", "idx": int(parts[1]), "total": int(parts[2]), "name": parts[3]}
        if parts[0] == "FAIL" and len(parts) >= 4:
            return {"kind": "fail", "idx": int(parts[1]), "name": parts[2], "msg": "|".join(parts[3:])}
    except ValueError:
        pass
    if parts[0] == "DONE":
        return {"kind": "done"}
    return {"kind": "none"}


def progress_text(st: Dict[str, Any], skipped: Optional[set] = None) -> str:
    skipped = skipped or set()
    kind = st.get("kind")
    total = len(STEPS)
    cur = st.get("idx", 0) if kind in ("run", "fail") else (total + 1 if kind == "done" else 0)
    done_n = total if kind == "done" else max(0, cur - 1)
    pct = int(done_n * 100 / total)
    bar = "▰" * done_n + "▱" * (total - done_n)
    lines = [f"`{bar}` **{pct}%**", ""]
    for i, name in enumerate(STEPS, 1):
        if i in skipped:
            mark, label = "⏭️", f"{name} (skipped)"
        elif i < cur:
            mark, label = "✅", name
        elif i == cur and kind == "run":
            mark, label = "⏳", f"**{name}**"
        elif i == cur and kind == "fail":
            mark, label = "❌", f"**{name}**"
        else:
            mark, label = "▫️", name
        lines.append(f"{mark} {label}")
    return "\n".join(lines)


def build_env(opts: Dict[str, Any]) -> str:
    keys = ["PANEL_URL", "PUBLIC_IP", "ADMIN_EMAIL", "ADMIN_USER", "ADMIN_PASS", "TZ_NAME",
            "WANT_WINGS", "API_PORT", "SFTP_PORT", "GAME_PORTS", "NODE_NAME"]
    return "\n".join(f"{k}={shlex.quote(str(opts[k]))}" for k in keys) + "\n"


INSTALL_SCRIPT = r'''#!/usr/bin/env bash
# Legacy Cloud Pterodactyl installer. Runs inside the container. Safe to re-run: finished steps are skipped.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive COMPOSER_ALLOW_SUPERUSER=1 LC_ALL=C.UTF-8
D=/root/lc-ptero
mkdir -p "$D/done"
STATE="$D/state"
TOTAL=8
. "$D/env"
. /etc/os-release
CUR_IDX=0
CUR_NAME=init
APT="apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
PANEL=/var/www/pterodactyl

say() { echo "[$(date +%T)] $*"; }
fail() {
  local rc=$?
  local msg="${BASH_COMMAND:-unknown}"
  msg="${msg//|/ }"
  echo "FAIL|$CUR_IDX|$CUR_NAME|${msg:0:160} (exit $rc)" > "$STATE"
  say "FAILED at step $CUR_IDX ($CUR_NAME): $msg (exit $rc)"
  exit "$rc"
}
die() {
  trap - ERR
  echo "FAIL|$CUR_IDX|$CUR_NAME|$*" > "$STATE"
  say "FAILED: $*"
  exit 1
}
trap fail ERR
step() {
  CUR_IDX=$1; CUR_NAME=$2
  local fn=$3
  if [ -f "$D/done/$fn" ]; then say "skip: $2 (already done)"; return 0; fi
  echo "RUN|$1|$TOTAL|$2" > "$STATE"
  say "== $2"
  "$fn"
  touch "$D/done/$fn"
}
wings_ok() { [ "$WANT_WINGS" = 1 ] && [ "$(cat "$D/wings_ok" 2>/dev/null || echo 0)" = 1 ]; }

s_prepare() {
  case "${ID}-${VERSION_ID}" in
    ubuntu-22.04|ubuntu-24.04|debian-12) ;;
    *) die "Unsupported OS: ${PRETTY_NAME}. Use Ubuntu 22.04/24.04 or Debian 12." ;;
  esac
  local mem disk
  mem=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
  disk=$(df -m --output=avail / | tail -1 | tr -d ' ')
  [ "$mem" -ge 900 ] || die "Not enough RAM: ${mem} MB (need about 1 GB)."
  [ "$disk" -ge 6000 ] || die "Not enough free disk: ${disk} MB (need about 6 GB)."
  $APT update
  $APT install curl ca-certificates gnupg lsb-release tar unzip git cron openssl apt-transport-https
  systemctl enable --now cron || true
}

s_web() {
  if [ "$ID" = ubuntu ]; then
    $APT install software-properties-common
    add-apt-repository -y ppa:ondrej/php
  else
    curl -fsSL https://packages.sury.org/php/apt.gpg -o /etc/apt/trusted.gpg.d/php.gpg
    echo "deb https://packages.sury.org/php/ $VERSION_CODENAME main" > /etc/apt/sources.list.d/php.list
  fi
  $APT update
  $APT install php8.3 php8.3-cli php8.3-gd php8.3-mysql php8.3-mbstring php8.3-bcmath php8.3-xml php8.3-fpm php8.3-curl php8.3-zip php8.3-intl nginx
  systemctl enable --now php8.3-fpm
}

s_db() {
  $APT install mariadb-server redis-server
  systemctl enable --now mariadb redis-server
  [ -f "$D/dbpass" ] || openssl rand -hex 16 > "$D/dbpass"
  local dbp
  dbp=$(cat "$D/dbpass")
  mysql -u root <<SQL
CREATE USER IF NOT EXISTS 'pterodactyl'@'127.0.0.1' IDENTIFIED BY '$dbp';
ALTER USER 'pterodactyl'@'127.0.0.1' IDENTIFIED BY '$dbp';
CREATE DATABASE IF NOT EXISTS panel;
GRANT ALL PRIVILEGES ON panel.* TO 'pterodactyl'@'127.0.0.1' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL
  curl -fsSL https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
}

s_files() {
  mkdir -p "$PANEL"
  cd "$PANEL"
  curl -fL --retry 3 -o /tmp/panel.tar.gz https://github.com/pterodactyl/panel/releases/latest/download/panel.tar.gz
  tar -xzf /tmp/panel.tar.gz
  chmod -R 755 storage/* bootstrap/cache/
  cp -n .env.example .env
  composer install --no-dev --optimize-autoloader --no-interaction
}

setenv() {
  local k=$1 v=$2 esc
  esc=$(printf '%s' "$v" | sed -e 's/[\\/&|]/\\&/g')
  if grep -q "^$k=" .env; then sed -i "s|^$k=.*|$k=$esc|" .env; else echo "$k=$v" >> .env; fi
}

s_setup() {
  cd "$PANEL"
  local dbp
  dbp=$(cat "$D/dbpass")
  grep -q '^APP_KEY=base64' .env || php artisan key:generate --force
  setenv APP_ENV production
  setenv APP_DEBUG false
  setenv APP_URL "$PANEL_URL"
  setenv APP_TIMEZONE "$TZ_NAME"
  setenv APP_SERVICE_AUTHOR "$ADMIN_EMAIL"
  setenv CACHE_DRIVER redis
  setenv SESSION_DRIVER redis
  setenv QUEUE_CONNECTION redis
  setenv REDIS_HOST 127.0.0.1
  setenv DB_HOST 127.0.0.1
  setenv DB_PORT 3306
  setenv DB_DATABASE panel
  setenv DB_USERNAME pterodactyl
  setenv DB_PASSWORD "$dbp"
  setenv MAIL_MAILER log
  setenv MAIL_DRIVER log
  grep -q '^HASHIDS_SALT=.\+' .env || setenv HASHIDS_SALT "$(openssl rand -hex 10)"
  setenv HASHIDS_LENGTH 8
  php artisan migrate --seed --force
  local cnt
  cnt=$(mysql -N panel -e "SELECT COUNT(*) FROM users WHERE email='${ADMIN_EMAIL}'")
  if [ "$cnt" = 0 ]; then
    php artisan p:user:make --email="$ADMIN_EMAIL" --username="$ADMIN_USER" --name-first=Admin --name-last=User \
      --password="$ADMIN_PASS" --admin=1 --no-interaction
  fi
  chown -R www-data:www-data "$PANEL"/*
  echo '* * * * * www-data php /var/www/pterodactyl/artisan schedule:run >> /dev/null 2>&1' > /etc/cron.d/pterodactyl
  chmod 644 /etc/cron.d/pterodactyl
  cat > /etc/systemd/system/pteroq.service <<'UNIT'
[Unit]
Description=Pterodactyl Queue Worker
After=redis-server.service

[Service]
User=www-data
Group=www-data
Restart=always
ExecStart=/usr/bin/php /var/www/pterodactyl/artisan queue:work --queue=high,standard,low --sleep=3 --tries=3
StartLimitInterval=180
StartLimitBurst=30
RestartSec=5s

[Install]
WantedBy=multi-user.target
UNIT
  cat > /etc/nginx/sites-available/pterodactyl.conf <<'NGX'
server {
    listen 80 default_server;
    server_name _;
    root /var/www/pterodactyl/public;
    index index.html index.htm index.php;
    charset utf-8;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location = /favicon.ico { access_log off; log_not_found off; }
    location = /robots.txt { access_log off; log_not_found off; }
    access_log off;
    error_log /var/log/nginx/pterodactyl.app-error.log error;
    client_max_body_size 100m;
    client_body_timeout 120s;
    sendfile off;
    location ~ \.php$ {
        fastcgi_split_path_info ^(.+\.php)(/.+)$;
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;
        fastcgi_index index.php;
        include /etc/nginx/fastcgi_params;
        fastcgi_param PHP_VALUE "upload_max_filesize = 100M \n post_max_size=100M";
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_param HTTP_PROXY "";
        fastcgi_intercept_errors off;
        fastcgi_buffer_size 16k;
        fastcgi_buffers 4 16k;
        fastcgi_connect_timeout 300;
        fastcgi_send_timeout 300;
        fastcgi_read_timeout 300;
    }
    location ~ /\.ht { deny all; }
}
NGX
  rm -f /etc/nginx/sites-enabled/default
  ln -sf /etc/nginx/sites-available/pterodactyl.conf /etc/nginx/sites-enabled/pterodactyl.conf
  nginx -t
  systemctl daemon-reload
  systemctl enable --now pteroq
  systemctl restart nginx
  systemctl enable nginx
}

s_docker() {
  if [ "$WANT_WINGS" != 1 ]; then say "Wings not requested, skipping Docker"; echo 0 > "$D/wings_ok"; return 0; fi
  if ! curl -fsSL https://get.docker.com | sh; then say "Docker install failed"; echo 0 > "$D/wings_ok"; return 0; fi
  systemctl enable --now docker || true
  if ! docker run --rm hello-world > /dev/null 2>&1; then
    say "Docker test failed, trying the vfs storage driver"
    mkdir -p /etc/docker
    echo '{"storage-driver":"vfs"}' > /etc/docker/daemon.json
    systemctl restart docker || true
    sleep 4
    if ! docker run --rm hello-world > /dev/null 2>&1; then
      say "Docker cannot run in this container"
      echo 0 > "$D/wings_ok"
      return 0
    fi
  fi
  echo 1 > "$D/wings_ok"
}

s_wings() {
  wings_ok || { say "Wings skipped"; return 0; }
  local arch
  case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) die "Unsupported CPU: $(uname -m)" ;; esac
  mkdir -p /etc/pterodactyl /var/lib/pterodactyl
  curl -fL --retry 3 -o /usr/local/bin/wings "https://github.com/pterodactyl/wings/releases/latest/download/wings_linux_${arch}"
  chmod u+x /usr/local/bin/wings
  cat > /etc/systemd/system/wings.service <<'UNIT'
[Unit]
Description=Pterodactyl Wings Daemon
After=docker.service
Requires=docker.service
PartOf=docker.service

[Service]
User=root
WorkingDirectory=/etc/pterodactyl
LimitNOFILE=4096
PIDFile=/var/run/wings/daemon.pid
ExecStart=/usr/local/bin/wings
Restart=on-failure
StartLimitInterval=180
StartLimitBurst=30
RestartSec=5s

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
}

s_node() {
  local node_ok=0 node_id=0
  if wings_ok; then
    cd "$PANEL"
    local memmb dskmb nm nd loc
    memmb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
    dskmb=$(df -m --output=size / | tail -1 | tr -d ' ')
    nm=$((memmb - 600)); [ "$nm" -ge 512 ] || nm=512
    nd=$((dskmb * 70 / 100))
    if [ "$(mysql -N panel -e 'SELECT COUNT(*) FROM locations')" = 0 ]; then
      php artisan p:location:make --short=legacy --long="Legacy Cloud" --no-interaction
    fi
    loc=$(mysql -N panel -e 'SELECT id FROM locations ORDER BY id LIMIT 1')
    if [ "$(mysql -N panel -e 'SELECT COUNT(*) FROM nodes')" = 0 ]; then
      if ! php artisan p:node:make --name="$NODE_NAME" --description="Created by Legacy Cloud" --locationId="$loc" \
          --fqdn="$PUBLIC_IP" --public=1 --scheme=http --proxy=0 --maxMemory="$nm" --overallocateMemory=0 \
          --maxDisk="$nd" --overallocateDisk=0 --uploadSize=100 --daemonListeningPort="$API_PORT" \
          --daemonSFTPPort="$SFTP_PORT" --daemonBase=/var/lib/pterodactyl --no-interaction; then
        say "Could not create the node automatically"
      fi
    fi
    node_id=$(mysql -N panel -e 'SELECT COALESCE(MAX(id),0) FROM nodes')
    if [ "$node_id" != 0 ]; then
      php artisan p:node:configuration "$node_id" > /etc/pterodactyl/config.yml
      grep -q '^token:' /etc/pterodactyl/config.yml || die "Node configuration was not generated."
      sed -i -E 's|^([[:space:]]*remote:).*|\1 "http://127.0.0.1"|' /etc/pterodactyl/config.yml
      for p in $GAME_PORTS; do
        mysql panel -e "INSERT INTO allocations (node_id, ip, ip_alias, port, created_at, updated_at) SELECT $node_id, '0.0.0.0', '$PUBLIC_IP', $p, NOW(), NOW() FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM allocations WHERE node_id=$node_id AND port=$p)"
      done
      systemctl enable --now wings
      for _ in $(seq 1 30); do (echo > "/dev/tcp/127.0.0.1/$API_PORT") 2> /dev/null && break; sleep 2; done
      if (echo > "/dev/tcp/127.0.0.1/$API_PORT") 2> /dev/null; then node_ok=1; else say "Wings did not open its port"; fi
    fi
  fi
  chown -R www-data:www-data "$PANEL"/* || true
  local wings_flag=false node_flag=false
  wings_ok && wings_flag=true
  [ "$node_ok" = 1 ] && node_flag=true
  cat > "$D/credentials.json" <<JSON
{"url":"$PANEL_URL","email":"$ADMIN_EMAIL","username":"$ADMIN_USER","password":"$ADMIN_PASS","ip":"$PUBLIC_IP","wings":$wings_flag,"node":$node_flag,"node_id":$node_id,"api_port":$API_PORT,"sftp_port":$SFTP_PORT,"game_ports":"$GAME_PORTS"}
JSON
  chmod 600 "$D/credentials.json"
}

step 1 "Preparing system" s_prepare
step 2 "PHP and web server" s_web
step 3 "Database and cache" s_db
step 4 "Panel files" s_files
step 5 "Panel setup and admin" s_setup
step 6 "Docker" s_docker
step 7 "Wings" s_wings
step 8 "Node and finish" s_node
echo "DONE" > "$STATE"
say "All done."
'''


def script_b64() -> str:
    return base64.b64encode(INSTALL_SCRIPT.encode()).decode()


# ----------------------------------------------------------------------------- bot integration
def setup(m):
    import discord
    from discord.ext import commands

    bot = m.bot
    ORANGE = 0xFFA12B
    data_dir = os.path.join(m.BASE_DIR, "lc_data")
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    STORE = os.path.join(data_dir, "ptero.json")
    flows: Dict[str, asyncio.Task] = {}      # container -> watcher task

    def load() -> Dict[str, Any]:
        try:
            with open(STORE) as fh:
                return json.load(fh)
        except Exception:
            return {}

    def save(db: Dict[str, Any]):
        tmp = STORE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(db, fh)
        os.replace(tmp, STORE)

    def embed(title: str, desc: str, color: int = ORANGE):
        return discord.Embed(title=title, description=desc, color=color)

    async def sh(container: str, script: str, timeout: int = 60) -> Tuple[int, str]:
        rc, out, err = await m.run_lxc_exec(container, ["bash", "-c", script], timeout=timeout)
        return rc, (out + ("\n" + err if err.strip() else "")).strip()

    def is_staff(uid: str) -> bool:
        return uid == str(m.MAIN_ADMIN_ID) or uid in m.admin_data.get("admins", [])

    def can_manage(uid: str, container: str) -> bool:
        if is_staff(uid):
            return True
        return any(v.get("container_name") == container for v in m.vps_data.get(uid, []))

    # ------------------------------------------------------------ checks and port setup
    async def preflight(container: str) -> Tuple[bool, str]:
        status = await m.get_container_status(container)
        if str(status).lower() != "running":
            return False, f"`{container}` is {status}. Start it first, then press Panel again."
        rc, out = await sh(container, ". /etc/os-release; echo \"${ID}-${VERSION_ID}\"; awk '/MemTotal/{print int($2/1024)}' /proc/meminfo; df -m --output=avail / | tail -1", 30)
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        if rc != 0 or len(lines) < 3:
            return False, "Could not inspect the VPS. Try again in a minute."
        os_id, mem, disk = lines[0], int(lines[1]), int(lines[2])
        if os_id not in SUPPORTED_OS:
            return False, f"`{os_id}` is not supported. Use Ubuntu 22.04, Ubuntu 24.04 or Debian 12."
        if mem < MIN_RAM_MB:
            return False, f"This VPS has {mem} MB RAM. The panel needs about 1 GB or more."
        if disk < MIN_DISK_MB:
            return False, f"Only {disk} MB free disk. The panel needs about 6 GB."
        return True, os_id

    async def ensure_nesting(container: str):
        """Docker (Wings) needs security.nesting. A restart is needed if it was off."""
        try:
            cur = await m.execute_lxc(f"lxc config get {container} security.nesting")
        except Exception:
            return
        if str(cur).strip().lower() == "true":
            return
        await m.execute_lxc(f"lxc config set {container} security.nesting true")
        await m.execute_lxc(f"lxc restart {container}", timeout=120)
        for _ in range(30):
            try:
                rc, _ = await sh(container, "true", 10)
                if rc == 0:
                    return
            except Exception:
                pass
            await asyncio.sleep(2)

    def record_forward(user_id: str, container: str, port: int):
        conn = m.get_db()
        conn.execute("INSERT INTO port_forwards (user_id, vps_container, vps_port, host_port, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, container, port, port, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    async def same_port_forward(user_id: str, container: str, port: int):
        await m.execute_lxc(f"lxc config device add {container} tcp_proxy_{port} proxy listen=tcp:0.0.0.0:{port} connect=tcp:127.0.0.1:{port}")
        await m.execute_lxc(f"lxc config device add {container} udp_proxy_{port} proxy listen=udp:0.0.0.0:{port} connect=udp:127.0.0.1:{port}")
        record_forward(user_id, container, port)

    async def plan_default_ip(owner: str, container: str, wings: bool) -> Dict[str, Any]:
        """Option 1: the bot's IP. Panel gets a forwarded port, wings/sftp/game ports are forwarded same-port."""
        ip = m.YOUR_SERVER_IP
        if not ip:
            raise RuntimeError("The bot has no server_ip configured, so it cannot build the default panel address.")
        panel_port = await m.create_port_forward(owner, container, 80)
        if not panel_port:
            raise RuntimeError("No free port left to forward the panel.")
        plan = {"mode": "default", "ip": ip, "panel_url": f"http://{ip}:{panel_port}", "api_port": 8080, "sftp_port": 2022, "game_ports": []}
        if wings:
            ports = []
            for _ in range(2 + GAME_PORT_COUNT):
                p = m.get_available_host_port()
                if not p or p in ports:
                    raise RuntimeError("Not enough free ports for Wings.")
                ports.append(p)
                await same_port_forward(owner, container, p)
            plan.update(api_port=ports[0], sftp_port=ports[1], game_ports=ports[2:])
        return plan

    def plan_transit_ip(ip: str) -> Dict[str, Any]:
        return {"mode": "transit", "ip": ip, "panel_url": f"http://{ip}", "api_port": 8080, "sftp_port": 2022,
                "game_ports": [25565 + i for i in range(GAME_PORT_COUNT)]}

    # ------------------------------------------------------------ Serververse Transit
    async def connect_transit(container: str, token: str) -> Tuple[bool, List[str], str]:
        cmd = (f"set -o pipefail; curl -fsSL {TRANSIT_URL} -o /tmp/svt-install.sh && bash /tmp/svt-install.sh --token {shlex.quote(token)}; "
               "echo '--- status ---'; (sv-transit status 2>&1 || true) | head -40")
        try:
            rc, out = await sh(container, cmd, 150)
        except asyncio.TimeoutError:
            return False, [], "The Transit installer took too long."
        out = redact(out, [token])
        ok_part = out.split("--- status ---")[0]
        ips = public_ipv4s(out.split("--- status ---")[-1]) or public_ipv4s(ok_part)
        return rc == 0, ips, out[-900:]

    # ------------------------------------------------------------ install runner
    async def start_install(container: str, owner: str, requester: str, answers: Dict[str, Any], plan: Dict[str, Any], channel):
        opts = {"PANEL_URL": plan["panel_url"], "PUBLIC_IP": plan["ip"], "ADMIN_EMAIL": answers["email"],
                "ADMIN_USER": answers["user"], "ADMIN_PASS": answers["password"], "TZ_NAME": answers["tz"],
                "WANT_WINGS": 1 if answers["wings"] else 0, "API_PORT": plan["api_port"], "SFTP_PORT": plan["sftp_port"],
                "GAME_PORTS": " ".join(str(p) for p in plan["game_ports"]), "NODE_NAME": f"{container}-node"[:40]}
        env_b64 = base64.b64encode(build_env(opts).encode()).decode()
        prep = (f"mkdir -p {WORKDIR} && umask 077 && echo {env_b64} | base64 -d > {WORKDIR}/env && "
                f"echo {script_b64()} | base64 -d > {WORKDIR}/install.sh && chmod 700 {WORKDIR}/install.sh && rm -f {WORKDIR}/state")
        rc, out = await sh(container, prep, 60)
        if rc != 0:
            raise RuntimeError(f"Could not write the installer: {out[-300:]}")
        await launch(container)
        db = load()
        db[container] = {"owner": owner, "mode": plan["mode"], "ip": plan["ip"], "url": plan["panel_url"],
                         "wings": answers["wings"], "started": time.time(), "status": "running"}
        save(db)
        await watch(container, requester, channel, plan)

    async def launch(container: str):
        rc, out = await sh(container, f"cd {WORKDIR} && (flock -n {WORKDIR}/lock bash {WORKDIR}/install.sh > {WORKDIR}/log 2>&1 < /dev/null &) ; sleep 1; echo started", 30)
        if rc != 0:
            raise RuntimeError(f"Could not start the installer: {out[-300:]}")

    async def read_state(container: str) -> Dict[str, Any]:
        try:
            rc, out = await sh(container, f"cat {WORKDIR}/state 2>/dev/null || true", 20)
        except Exception:
            return {"kind": "unreachable"}
        return parse_state(out)

    async def read_log(container: str, lines: int = 25) -> str:
        try:
            rc, out = await sh(container, f"tail -n {lines} {WORKDIR}/log 2>/dev/null", 20)
        except Exception as exc:
            return f"(could not read log: {exc})"
        return out[-1500:] or "(log is empty)"

    async def read_creds(container: str) -> Optional[Dict[str, Any]]:
        try:
            rc, out = await sh(container, f"cat {WORKDIR}/credentials.json", 20)
            return json.loads(out) if rc == 0 else None
        except Exception:
            return None

    class RetryView(discord.ui.View):
        def __init__(self, container: str, requester: str, plan: Dict[str, Any]):
            super().__init__(timeout=3600)
            self.container, self.requester, self.plan = container, requester, plan

        @discord.ui.button(label="Retry", style=discord.ButtonStyle.primary)
        async def retry(self, interaction: discord.Interaction, _b):
            if not can_manage(str(interaction.user.id), self.container):
                return await interaction.response.send_message("Not your VPS.", ephemeral=True)
            if self.container in flows and not flows[self.container].done():
                return await interaction.response.send_message("Already running.", ephemeral=True)
            await interaction.response.send_message("Resuming the install from the failed step.", ephemeral=True)
            try:
                await launch(self.container)
            except Exception as exc:
                return await interaction.followup.send(embed=m.create_error_embed("Could not restart", str(exc)), ephemeral=True)
            await watch(self.container, self.requester, interaction.channel, self.plan)

        @discord.ui.button(label="Show log", style=discord.ButtonStyle.secondary)
        async def log(self, interaction: discord.Interaction, _b):
            if not can_manage(str(interaction.user.id), self.container):
                return await interaction.response.send_message("Not your VPS.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            text = await read_log(self.container, 30)
            await interaction.followup.send(f"```\n{text[-1800:]}\n```", ephemeral=True)

    class DoneView(discord.ui.View):
        def __init__(self, container: str):
            super().__init__(timeout=None)
            self.container = container

        @discord.ui.button(label="Show login", style=discord.ButtonStyle.success)
        async def show(self, interaction: discord.Interaction, _b):
            if not can_manage(str(interaction.user.id), self.container):
                return await interaction.response.send_message("Only the VPS owner can see this.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            c = await read_creds(self.container)
            if not c:
                return await interaction.followup.send("Could not read the login. Try again in a moment.", ephemeral=True)
            await interaction.followup.send(embed=embed("Panel login",
                f"**URL:** {c['url']}\n**Email:** `{c['email']}`\n**Username:** `{c['username']}`\n**Password:** ||`{c['password']}`||\n\n"
                "Change the password after you sign in.", 0x00FF88), ephemeral=True)

    async def watch(container: str, requester: str, channel, plan: Dict[str, Any]):
        msg = await channel.send(content=f"<@{requester}>", embed=embed("Installing Pterodactyl Panel", progress_text({"kind": "none"})))

        async def loop():
            last = None
            started = time.time()
            skipped: set = set()
            miss = 0
            while True:
                await asyncio.sleep(5)
                st = await read_state(container)
                if st["kind"] == "unreachable":
                    miss += 1
                    if miss > 24:
                        await msg.edit(embed=embed("Lost contact", "The VPS stopped answering. Start it and press Panel to resume.", 0xFF3366))
                        return
                    continue
                miss = 0
                if not plan.get("wings_expected", True):
                    skipped.update({6, 7})
                if st["kind"] == "done":
                    c = await read_creds(container) or {}
                    skip = set() if c.get("wings") else {6, 7}
                    lines = [progress_text(st, skip), "", f"**Panel:** {plan['panel_url']}"]
                    if c.get("node"):
                        lines.append(f"**Node:** ready, Wings is online on port `{plan['api_port']}`. Game ports: `{' '.join(str(p) for p in plan['game_ports'])}`")
                    elif c.get("wings"):
                        lines.append("**Node:** Wings is installed but the node did not come online. Open the panel, check Admin > Nodes.")
                    else:
                        lines.append("**Wings:** not installed (Docker could not run in this VPS, or you chose no). The panel works; add a node on another machine.")
                    db = load()
                    db.setdefault(container, {})["status"] = "done"
                    save(db)
                    await msg.edit(embed=embed("Panel is ready", "\n".join(lines), 0x00FF88), view=DoneView(container))
                    return
                if st["kind"] == "fail":
                    log = await read_log(container, 12)
                    await msg.edit(embed=embed("Install stopped", f"{progress_text(st, skipped)}\n\n**Why:** `{st['msg'][:200]}`\n```\n{log[-700:]}\n```\nPress **Retry** to continue from this step.", 0xFF3366),
                                   view=RetryView(container, requester, plan))
                    return
                key = (st.get("kind"), st.get("idx"))
                if key != last:
                    last = key
                    await msg.edit(embed=embed("Installing Pterodactyl Panel", progress_text(st, skipped) + "\n\nThis takes about 10 to 20 minutes."))
                if time.time() - started > 3600:
                    await msg.edit(embed=embed("Taking too long", "The install ran for over an hour. Press Panel again to check on it.", 0xFF3366))
                    return

        task = asyncio.create_task(loop())
        flows[container] = task

        def cleanup(_t):
            flows.pop(container, None)
        task.add_done_callback(cleanup)

    # ------------------------------------------------------------ UI: IP choice, token, questions
    class Questions(discord.ui.Modal, title="Panel setup (5 quick questions)"):
        def __init__(self, container: str, owner: str, plan: Dict[str, Any]):
            super().__init__()
            self.container, self.owner, self.plan = container, owner, plan
            self.email = discord.ui.TextInput(label="1. Admin email", placeholder="you@example.com", max_length=120)
            self.user = discord.ui.TextInput(label="2. Admin username", default="admin", max_length=32)
            self.pw = discord.ui.TextInput(label="3. Password (empty = we generate one)", required=False, max_length=64)
            self.tz = discord.ui.TextInput(label="4. Timezone", default="Asia/Kolkata", max_length=40)
            self.wings = discord.ui.TextInput(label="5. Install Wings too? (yes/no)", default="yes", max_length=3)
            for it in (self.email, self.user, self.pw, self.tz, self.wings):
                self.add_item(it)

        async def on_submit(self, interaction: discord.Interaction):
            answers, problem = validate_answers(self.email.value, self.user.value, self.pw.value, self.tz.value, self.wings.value)
            if problem:
                return await interaction.response.send_message(embed=m.create_error_embed("Check your answers", problem), ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            plan = dict(self.plan)
            try:
                if answers["wings"]:
                    await ensure_nesting(self.container)
                if plan["mode"] == "default":
                    plan = await plan_default_ip(self.owner, self.container, answers["wings"])
                plan["wings_expected"] = answers["wings"]
                await interaction.followup.send("Starting. Progress will appear in this channel.", ephemeral=True)
                await start_install(self.container, self.owner, str(interaction.user.id), answers, plan, interaction.channel)
            except Exception as exc:
                m.logger.exception("ptero start failed")
                await interaction.followup.send(embed=m.create_error_embed("Could not start", str(exc)), ephemeral=True)

    class TokenModal(discord.ui.Modal, title="Add your IP (Serververse)"):
        def __init__(self, container: str, owner: str):
            super().__init__()
            self.container, self.owner = container, owner
            self.token = discord.ui.TextInput(label="Serververse token", placeholder="Paste the token or the full command", max_length=400)
            self.add_item(self.token)

        async def on_submit(self, interaction: discord.Interaction):
            tok = parse_token(self.token.value)
            if not tok:
                return await interaction.response.send_message(embed=m.create_error_embed("Token not recognised",
                    "Paste only the token (letters, digits, - and _), or the full `--token` command."), ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send("Connecting your IP. This can take up to two minutes.", ephemeral=True)
            ok, ips, tail = await connect_transit(self.container, tok)
            if ok and len(ips) == 1:
                plan = plan_transit_ip(ips[0])
                return await interaction.followup.send(embed=embed("IP connected", f"Using **{ips[0]}** for your panel."),
                                                       view=ContinueView(self.container, self.owner, plan), ephemeral=True)
            if ok:
                return await interaction.followup.send(embed=embed("Connected, IP unclear",
                    "The token worked but I could not tell which IP was assigned. Enter it yourself, or use the default IP."
                    + (f"\n```\n{tail[-600:]}\n```" if tail else "")), view=ManualIPView(self.container, self.owner), ephemeral=True)
            await interaction.followup.send(embed=embed("Could not connect",
                f"The Transit token did not work, so I will use the default IP instead.\n```\n{tail[-600:]}\n```", 0xFFAA00),
                view=ContinueView(self.container, self.owner, {"mode": "default"}), ephemeral=True)

    class IPModal(discord.ui.Modal, title="Your Transit IP"):
        def __init__(self, container: str, owner: str):
            super().__init__()
            self.container, self.owner = container, owner
            self.ip = discord.ui.TextInput(label="Transit IPv4 address", max_length=15)
            self.add_item(self.ip)

        async def on_submit(self, interaction: discord.Interaction):
            ips = public_ipv4s(self.ip.value)
            if not ips:
                return await interaction.response.send_message(embed=m.create_error_embed("Not a public IPv4", "Enter an address like 203.0.113.10."), ephemeral=True)
            await interaction.response.send_message(embed=embed("IP saved", f"Using **{ips[0]}**."),
                                                    view=ContinueView(self.container, self.owner, plan_transit_ip(ips[0])), ephemeral=True)

    class ContinueView(discord.ui.View):
        def __init__(self, container: str, owner: str, plan: Dict[str, Any]):
            super().__init__(timeout=900)
            self.container, self.owner, self.plan = container, owner, plan

        @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
        async def go(self, interaction: discord.Interaction, _b):
            if not can_manage(str(interaction.user.id), self.container):
                return await interaction.response.send_message("Not your VPS.", ephemeral=True)
            await interaction.response.send_modal(Questions(self.container, self.owner, self.plan))

    class ManualIPView(discord.ui.View):
        def __init__(self, container: str, owner: str):
            super().__init__(timeout=900)
            self.container, self.owner = container, owner

        @discord.ui.button(label="Enter my IP", style=discord.ButtonStyle.primary)
        async def enter(self, interaction: discord.Interaction, _b):
            await interaction.response.send_modal(IPModal(self.container, self.owner))

        @discord.ui.button(label="Use default IP", style=discord.ButtonStyle.secondary)
        async def default(self, interaction: discord.Interaction, _b):
            await interaction.response.send_modal(Questions(self.container, self.owner, {"mode": "default"}))

    class IPChoiceView(discord.ui.View):
        def __init__(self, container: str, owner: str):
            super().__init__(timeout=900)
            self.container, self.owner = container, owner

        @discord.ui.button(label="1. Default IP (from the bot)", style=discord.ButtonStyle.success)
        async def default(self, interaction: discord.Interaction, _b):
            if not can_manage(str(interaction.user.id), self.container):
                return await interaction.response.send_message("Not your VPS.", ephemeral=True)
            await interaction.response.send_modal(Questions(self.container, self.owner, {"mode": "default"}))

        @discord.ui.button(label="2. Add your IP", style=discord.ButtonStyle.primary)
        async def custom(self, interaction: discord.Interaction, _b):
            if not can_manage(str(interaction.user.id), self.container):
                return await interaction.response.send_message("Not your VPS.", ephemeral=True)
            await interaction.response.send_modal(TokenModal(self.container, self.owner))

    # ------------------------------------------------------------ entry point
    async def open_panel_flow(interaction: discord.Interaction, container: str, owner: str):
        uid = str(interaction.user.id)
        if not can_manage(uid, container):
            return await interaction.response.send_message(embed=m.create_error_embed("Access Denied", "This is not your VPS."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            running = container in flows and not flows[container].done()
            if running:
                return await interaction.followup.send(embed=embed("Already installing", "An install is running for this VPS. Watch the progress message in the channel."), ephemeral=True)
            st = await read_state(container) if str(await m.get_container_status(container)).lower() == "running" else {"kind": "none"}
            if st["kind"] == "done":
                return await interaction.followup.send(embed=embed("Panel already installed", "Press the button to see your login."), view=DoneView(container), ephemeral=True)
            if st["kind"] in ("run", "fail"):
                plan = {"panel_url": (load().get(container) or {}).get("url", "?"), "api_port": 8080, "sftp_port": 2022, "game_ports": []}
                await interaction.followup.send("An earlier install was found. Resuming it.", ephemeral=True)
                await launch(container)
                return await watch(container, uid, interaction.channel, plan)
            ok, info = await preflight(container)
            if not ok:
                return await interaction.followup.send(embed=m.create_error_embed("Cannot install here", info), ephemeral=True)
            await interaction.followup.send(embed=embed("Pterodactyl Panel",
                f"VPS `{container}` ({info}) is ready.\n\n**Which IP should your panel use?**\n"
                "**1. Default IP:** the IP the bot gave you. Ports are forwarded for you.\n"
                "**2. Add your IP:** paste a Serververse Transit token. If it does not connect, setup falls back to option 1.\n\n"
                "After this I ask 5 short questions."), view=IPChoiceView(container, owner), ephemeral=True)
        except Exception as exc:
            m.logger.exception("ptero flow failed")
            await interaction.followup.send(embed=m.create_error_embed("Panel setup failed", f"`{type(exc).__name__}: {exc}`"), ephemeral=True)

    # ------------------------------------------------------------ ManageView button
    orig_add = m.ManageView.add_action_buttons

    def patched_add(self):
        orig_add(self)
        try:
            if self.is_shared and not self.is_admin:
                return
            btn = discord.ui.Button(label="Panel", emoji="🦅", style=discord.ButtonStyle.primary)

            async def on_click(interaction: discord.Interaction, view=self):
                if view.selected_index is None:
                    return await interaction.response.send_message("Select a VPS first.", ephemeral=True)
                idx = view.actual_index if view.is_shared else view.indices[view.selected_index]
                v = m.vps_data[view.owner_id][idx]
                await open_panel_flow(interaction, v["container_name"], view.owner_id)

            btn.callback = on_click
            self.add_item(btn)
        except Exception:
            m.logger.exception("Could not add the Panel button")

    m.ManageView.add_action_buttons = patched_add

    # ------------------------------------------------------------ .ptero command
    @bot.command(name="ptero")
    async def ptero_cmd(ctx, container: str = ""):
        uid = str(ctx.author.id)
        if not container:
            mine = [v["container_name"] for v in m.vps_data.get(uid, [])]
            if len(mine) != 1:
                names = ", ".join(f"`{n}`" for n in mine) or "none"
                return await ctx.send(embed=embed("Pterodactyl Panel", f"Use `{m.PREFIX}ptero <container>` or press **Panel** in `{m.PREFIX}manage`.\nYour VPS: {names}"))
            container = mine[0]
        if not can_manage(uid, container):
            return await ctx.send(embed=m.create_error_embed("Access Denied", "This is not your VPS."))
        owner = uid
        for o, lst in m.vps_data.items():
            if any(v.get("container_name") == container for v in lst):
                owner = o
        view = discord.ui.View(timeout=300)
        b = discord.ui.Button(label="Set up panel", emoji="🦅", style=discord.ButtonStyle.primary)

        async def go(interaction: discord.Interaction):
            await open_panel_flow(interaction, container, owner)
        b.callback = go
        view.add_item(b)
        await ctx.send(embed=embed("Pterodactyl Panel", f"Install a panel on `{container}`."), view=view)

    m.logger.info("Legacy Cloud Pterodactyl installer loaded")
