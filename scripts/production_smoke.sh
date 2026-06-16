#!/usr/bin/env sh
set -eu

: "${HIVE_URL:?Set HIVE_URL, for example https://your-service.koyeb.app}"
: "${ADMIN_BEARER_TOKEN:?Set ADMIN_BEARER_TOKEN}"

BASE_URL="${HIVE_URL%/}"
AUTH_HEADER="Authorization: Bearer ${ADMIN_BEARER_TOKEN}"

printf '%s\n' '1/4 livez'
curl --fail --silent --show-error --max-time 15 "${BASE_URL}/livez"
printf '\n%s\n' '2/4 readyz'
curl --fail --silent --show-error --max-time 15 "${BASE_URL}/readyz"
printf '\n%s\n' '3/4 authenticated readiness'
curl --fail --silent --show-error --max-time 20 -H "$AUTH_HEADER" "${BASE_URL}/v1/runtime/readiness"
printf '\n%s\n' '4/4 models'
curl --fail --silent --show-error --max-time 30 -H "$AUTH_HEADER" "${BASE_URL}/v1/models"
printf '\nHIVE production smoke checks passed.\n'
