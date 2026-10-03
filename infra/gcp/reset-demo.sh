#!/usr/bin/env bash
# Reset the shared demo workspace on the live service (same as Dispatcher → Demo → Reset demo).
# Sandboxes are untouched. Open browsers reload by themselves.
set -euo pipefail
cd "$(dirname "$0")/../.."
URL="${URL:-}"
if [ -z "$URL" ]; then
  # shellcheck disable=SC1091
  source infra/gcp/env.sh
  URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
fi
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
curl -fsS -c "$JAR" -H 'Content-Type: application/json' -H 'X-Relay-Client: web' \
  -d "{\"email\":\"nirosha@waypoint.lk\",\"password\":\"${DEMO_PASSWORD:-relay2026}\"}" \
  "$URL/api/auth/login" >/dev/null
curl -fsS -b "$JAR" -X POST -H 'X-Relay-Client: web' "$URL/api/demo/reset"
echo
echo "Shared demo reset on $URL"
