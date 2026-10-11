#!/usr/bin/env bash
# Starts ClickHouse (Docker), the FastAPI backend, and the Next.js frontend.
# Safe to re-run — skips anything already running.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
RUN_DIR="$ROOT_DIR/.run"
LOG_DIR="$ROOT_DIR/logs"
BACKEND_PORT=8000
FRONTEND_PORT=3001

mkdir -p "$RUN_DIR" "$LOG_DIR"

log() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }

is_port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

pid_is_running() {
  [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

# ---------- ClickHouse ----------
log "Starting ClickHouse (Docker)..."
if ! docker info >/dev/null 2>&1; then
  warn "Docker daemon not running — attempting to launch Docker Desktop."
  open -a Docker 2>/dev/null || true
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
  if ! docker info >/dev/null 2>&1; then
    warn "Docker still not ready after 60s — skipping ClickHouse. Backend will fall back to sample data."
  fi
fi

if docker info >/dev/null 2>&1; then
  (cd "$BACKEND_DIR" && docker compose up -d)
  log "Waiting for ClickHouse to accept connections..."
  for _ in $(seq 1 30); do
    curl -sf http://localhost:8123/ping >/dev/null 2>&1 && break
    sleep 1
  done
  if curl -sf http://localhost:8123/ping >/dev/null 2>&1; then
    log "ClickHouse is up (http://localhost:8123)."
  else
    warn "ClickHouse did not respond to /ping in time — check 'docker logs options_clickhouse'."
  fi
fi

# ---------- Backend ----------
if pid_is_running "$RUN_DIR/backend.pid"; then
  log "Backend already running (pid $(cat "$RUN_DIR/backend.pid"))."
elif is_port_listening "$BACKEND_PORT"; then
  warn "Port $BACKEND_PORT is already in use by another process — leaving it alone."
else
  log "Starting backend (FastAPI) on port $BACKEND_PORT..."
  if [ ! -d "$BACKEND_DIR/.venv" ]; then
    log "No venv found — creating one and installing dependencies (first run only)."
    python3 -m venv "$BACKEND_DIR/.venv"
    "$BACKEND_DIR/.venv/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
  fi
  (
    cd "$BACKEND_DIR"
    # Loopback only; the app itself writes the redacted, rotating logs/backend.log.
    nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
      > "$LOG_DIR/backend.stdout.log" 2>&1 &
    echo $! > "$RUN_DIR/backend.pid"
  )
  sleep 2
  if pid_is_running "$RUN_DIR/backend.pid"; then
    log "Backend started (pid $(cat "$RUN_DIR/backend.pid")) — log: logs/backend.log"
  else
    warn "Backend failed to start — check logs/backend.stdout.log"
  fi
fi

# ---------- Frontend ----------
if pid_is_running "$RUN_DIR/frontend.pid"; then
  log "Frontend already running (pid $(cat "$RUN_DIR/frontend.pid"))."
elif is_port_listening "$FRONTEND_PORT"; then
  warn "Port $FRONTEND_PORT is already in use by another process — leaving it alone."
else
  log "Starting frontend (Next.js) on port $FRONTEND_PORT..."
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "No node_modules found — running npm install (first run only)."
    (cd "$FRONTEND_DIR" && npm install)
  fi
  (
    cd "$FRONTEND_DIR"
    nohup npm run dev -- -p "$FRONTEND_PORT" \
      > "$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "$RUN_DIR/frontend.pid"
  )
  sleep 3
  if pid_is_running "$RUN_DIR/frontend.pid"; then
    log "Frontend started (pid $(cat "$RUN_DIR/frontend.pid")) — log: logs/frontend.log"
  else
    warn "Frontend failed to start — check logs/frontend.log"
  fi
fi

echo
log "Done."
echo "  Frontend:  http://localhost:$FRONTEND_PORT"
echo "  Backend:   http://localhost:$BACKEND_PORT/docs"
echo "  ClickHouse: http://localhost:8123"
echo
echo "  Log in to Definedge (uses DEFINEDGE_TOTP_SECRET from backend/.env):"
echo "    curl -X POST -H \"Authorization: Bearer \$API_AUTH_TOKEN\" http://localhost:$BACKEND_PORT/api/v1/auth/definedge/login/auto"
echo "    (API_AUTH_TOKEN is in backend/.env — every API call now requires it)"
echo
echo "  Stop everything with: scripts/stop.sh"
