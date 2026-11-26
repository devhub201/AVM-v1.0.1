#!/usr/bin/env bash
# Legacy Cloud — Mining Guard TEST simulator.
# Starts HARMLESS fake "miners" inside a VPS so you can check that Mining Guard notices them.
# Nothing here mines anything or contacts any pool.
#
#   bash minertest.sh start [binary|pool|port|cpu|all]     (default: all)
#   bash minertest.sh status
#   bash minertest.sh stop                                  (always run this when you are done)
set -u
DIR=/tmp/lc-minertest
PIDS="$DIR/pids"
mkdir -p "$DIR"; touch "$PIDS"

track() { echo "$1" >>"$PIDS"; }

t_binary() {   # a process literally named like a known miner
  cp "$(command -v sleep)" "$DIR/xmrig"
  nohup "$DIR/xmrig" 1800 >/dev/null 2>&1 & track $!
  echo "[binary] started fake 'xmrig' (pid $!)  ->  expect: known miner binary"
}
t_pool() {     # a process whose arguments look like a mining pool command line
  if command -v python3 >/dev/null 2>&1; then
    nohup python3 -c 'import time; time.sleep(1800)' --donate-level=1 'stratum+tcp://pool.example.com:3333' >/dev/null 2>&1 & track $!
    echo "[pool]   started pool-style command line (pid $!)  ->  expect: stratum URL / --donate-level"
  else
    echo "[pool]   skipped: python3 is not installed in this VPS (apt install -y python3)"
  fi
}
t_port() {     # an established connection to a pool-style port (3333), entirely on localhost
  if command -v python3 >/dev/null 2>&1; then
    nohup python3 - >/dev/null 2>&1 <<'PY' &
import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 3333)); s.listen(5)
c = socket.create_connection(("127.0.0.1", 3333)); conn, _ = s.accept()
time.sleep(1800)
PY
    track $!
    echo "[port]   opened a local connection to port 3333 (pid $!)  ->  expect: pool-style port (medium alert needs high CPU too)"
  else
    echo "[port]   skipped: python3 is not installed in this VPS"
  fi
}
t_cpu() {      # burn every core so CPU stays above the threshold
  n=$(nproc 2>/dev/null || echo 1)
  for _ in $(seq 1 "$n"); do nohup sh -c 'while :; do :; done' >/dev/null 2>&1 & track $!; done
  echo "[cpu]    burning $n core(s)  ->  expect: CPU above threshold (medium alert needs the port test as well)"
}

stop_all() {
  while read -r p; do [ -n "$p" ] && kill "$p" 2>/dev/null; done <"$PIDS"
  pkill -f "$DIR/xmrig" 2>/dev/null
  pkill -f 'stratum+tcp://pool.example.com' 2>/dev/null
  rm -rf "$DIR"
  echo "stopped and cleaned up."
}

case "${1:-}" in
  start)
    case "${2:-all}" in
      binary) t_binary ;; pool) t_pool ;; port) t_port ;; cpu) t_cpu ;;
      all) t_binary; t_pool; t_port; t_cpu ;;
      *) echo "unknown test '$2' (binary|pool|port|cpu|all)"; exit 1 ;;
    esac
    echo
    echo "Now run in Discord:  .minerscan <container-name>     (report only)"
    echo "Auto guard scans every ~2 min.  When done:  bash minertest.sh stop"
    ;;
  status) echo "tracked pids:"; cat "$PIDS" 2>/dev/null | while read -r p; do [ -d /proc/$p ] && echo "  $p alive" || echo "  $p gone"; done ;;
  stop) stop_all ;;
  *) echo "usage: bash minertest.sh start [binary|pool|port|cpu|all] | status | stop"; exit 1 ;;
esac
