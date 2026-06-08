#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT:-8080}}"

echo "Checking ${BASE_URL}/health"
curl -fsS "${BASE_URL}/health"
echo ""
