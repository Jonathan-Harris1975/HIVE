#!/usr/bin/env sh
set -eu

PORT="${PORT:-8080}"
APP_DIR="${APP_DIR:-backend}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
UVICORN_LIMIT_CONCURRENCY="${UVICORN_LIMIT_CONCURRENCY:-32}"
UVICORN_BACKLOG="${UVICORN_BACKLOG:-128}"
UVICORN_TIMEOUT_KEEP_ALIVE="${UVICORN_TIMEOUT_KEEP_ALIVE:-10}"
UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN="${UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN:-30}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"
LOG_LEVEL="${LOG_LEVEL:-info}"

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --app-dir "$APP_DIR" \
  --workers "$WEB_CONCURRENCY" \
  --limit-concurrency "$UVICORN_LIMIT_CONCURRENCY" \
  --backlog "$UVICORN_BACKLOG" \
  --timeout-keep-alive "$UVICORN_TIMEOUT_KEEP_ALIVE" \
  --timeout-graceful-shutdown "$UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN" \
  --proxy-headers \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" \
  --log-level "$LOG_LEVEL" \
  --no-access-log \
  --no-server-header
