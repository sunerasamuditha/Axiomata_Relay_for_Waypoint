#!/bin/sh
# Container start: migrate, seed what is missing, serve. Safe to run on several instances at once:
# migrations and seeding each hold a Postgres advisory lock, so only one instance does the work.
set -eu
cd /app

echo "relay: applying database migrations"
alembic -c apps/api/alembic.ini upgrade head

echo "relay: seeding reference data, accounts and the demo workspace (only what is missing)"
python -m relay_api.seed --if-empty

echo "relay: starting on port ${PORT:-8080}"
# One worker per container: the live-update hub and the simulation ticker live in-process.
# Scale out with more Cloud Run instances, not more workers.
exec uvicorn relay_api.main:app \
  --host 0.0.0.0 --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips='*' \
  --timeout-keep-alive 75 \
  --no-access-log
