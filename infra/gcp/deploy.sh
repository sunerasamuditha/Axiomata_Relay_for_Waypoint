#!/usr/bin/env bash
# Build the root Dockerfile with Cloud Build and deploy it to Cloud Run (docs/DEPLOY_GCP.md §6).
# Uploads the repo minus .gcloudignore: private CSVs stay on this machine, trained models go up.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f infra/gcp/env.sh ] || { echo "Missing infra/gcp/env.sh: see docs/DEPLOY_GCP.md §2"; exit 1; }
# shellcheck disable=SC1091
source infra/gcp/env.sh
: "${PROJECT_ID:?}" "${REGION:?}" "${SERVICE:?}" "${SQL_INSTANCE:?}" "${SA_NAME:?}"
SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then SHA="$SHA-dirty"; fi

if [ ! -f packages/ml/models/service_min.txt ]; then
  echo "Note: packages/ml/models/ has no trained models; this revision will use the heuristic predictor."
fi

gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --add-cloudsql-instances "$PROJECT_ID:$REGION:$SQL_INSTANCE" \
  --set-secrets "DATABASE_URL=DATABASE_URL:latest,SECRET_KEY=SECRET_KEY:latest" \
  --set-env-vars "ENV=production,COOKIE_SECURE=true,GIT_SHA=$SHA" \
  --allow-unauthenticated \
  --cpu "${CPU:-2}" --memory "${MEMORY:-2Gi}" --concurrency 40 \
  --min-instances "${MIN_INSTANCES:-1}" --max-instances "${MAX_INSTANCES:-3}" \
  --timeout 3600 --execution-environment gen2 \
  --labels "app=relay,commit=${SHA//[^a-z0-9-]/-}"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo
echo "Service: $URL"
for i in $(seq 1 20); do
  if curl -fsS "$URL/api/health"; then echo; exit 0; fi
  sleep 3
done
echo "The health check did not answer; read the logs: gcloud run services logs read $SERVICE --region $REGION --limit 100"
exit 1
