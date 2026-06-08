#!/usr/bin/env sh
set -eu

PORT="${PORT:-8080}"
APP_DIR="${APP_DIR:-backend}"

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --app-dir "$APP_DIR" \
  --proxy-headers \
  --forwarded-allow-ips "*"
