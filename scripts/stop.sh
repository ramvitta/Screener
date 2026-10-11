#!/usr/bin/env bash
# Stops the frontend and backend started by scripts/start.sh.
# ClickHouse (Docker) is left running by default — pass --all to stop it too.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
RUN_DIR="$ROOT_DIR/.run"
BACKEND_PORT=8000
FRONTEND_PORT=3001
STOP_DB=false

for arg in "$@"; do
  case "$arg" in
    --all) STOP_DB=true ;;
  esac
done

log() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }

stop_by_pidfile() {
  local name="$1" pidfile="$2" port="$3"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      log "Stopping $name (pid $pid)..."
      kill "$pid" 2>/dev/null
      for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
  fi

  # Fallback: anything still bound to the port that the pidfile didn't cover
  # (e.g. a dev server restarted itself under a new child pid).
  local leftover
  leftover="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$leftover" ]; then
    warn "Port $port still in use by pid(s) $leftover — stopping."
    kill $leftover 2>/dev/null
  fi
}

stop_by_pidfile "frontend" "$RUN_DIR/frontend.pid" "$FRONTEND_PORT"
stop_by_pidfile "backend" "$RUN_DIR/backend.pid" "$BACKEND_PORT"

if [ "$STOP_DB" = true ]; then
  log "Stopping ClickHouse (Docker)..."
  (cd "$BACKEND_DIR" && docker compose down)
else
  log "Leaving ClickHouse running (pass --all to stop it too)."
fi

log "Done."
