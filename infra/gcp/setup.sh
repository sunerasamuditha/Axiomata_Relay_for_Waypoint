#!/usr/bin/env bash
# One-time Google Cloud setup for Relay: APIs, Cloud SQL, secrets, the runtime service account.
# Safe to re-run: every step checks whether its resource already exists.
# Prerequisites: docs/DEPLOY_GCP.md §1–§2 (project created, billing linked, infra/gcp/env.sh written).
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f infra/gcp/env.sh ] || { echo "Missing infra/gcp/env.sh: cp infra/gcp/env.sh.example infra/gcp/env.sh and edit it."; exit 1; }
# shellcheck disable=SC1091
source infra/gcp/env.sh
: "${PROJECT_ID:?}" "${REGION:?}" "${SQL_INSTANCE:?}" "${SA_NAME:?}"
gcloud config set project "$PROJECT_ID" >/dev/null
SA="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
step() { printf "\n\033[1m==> %s\033[0m\n" "$*"; }

step "Enabling APIs (1–2 minutes)"
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com iam.googleapis.com iamcredentials.googleapis.com

step "Cloud SQL instance $SQL_INSTANCE (about 10 minutes the first time)"
if gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  echo "exists"
else
  gcloud sql instances create "$SQL_INSTANCE" \
    --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-g1-small \
    --region="$REGION" --availability-type=zonal \
    --storage-type=SSD --storage-size=10 --storage-auto-increase \
    --backup-start-time=20:30
fi

step "Database and user"
gcloud sql databases describe relay --instance="$SQL_INSTANCE" >/dev/null 2>&1 || gcloud sql databases create relay --instance="$SQL_INSTANCE"
if gcloud secrets describe DATABASE_URL >/dev/null 2>&1; then
  echo "DATABASE_URL secret exists: keeping the current password"
else
  DB_PASS="$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 28)"
  if gcloud sql users list --instance="$SQL_INSTANCE" --format='value(name)' | grep -qx relay; then
    gcloud sql users set-password relay --instance="$SQL_INSTANCE" --password="$DB_PASS"
  else
    gcloud sql users create relay --instance="$SQL_INSTANCE" --password="$DB_PASS"
  fi
  CONN="$PROJECT_ID:$REGION:$SQL_INSTANCE"
  # the password goes straight into Secret Manager; it is never printed or written to disk
  printf '%s' "postgresql+psycopg://relay:${DB_PASS}@/relay?host=/cloudsql/${CONN}" | gcloud secrets create DATABASE_URL --data-file=-
  unset DB_PASS
fi

step "SECRET_KEY secret"
gcloud secrets describe SECRET_KEY >/dev/null 2>&1 || openssl rand -hex 32 | tr -d '\n' | gcloud secrets create SECRET_KEY --data-file=-

step "Runtime service account $SA"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create "$SA_NAME" --display-name="Relay Cloud Run runtime"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role=roles/cloudsql.client --condition=None >/dev/null
for s in DATABASE_URL SECRET_KEY; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor >/dev/null
done

step "Let source deploys build (Cloud Build runs as the Compute Engine default service account)"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" --role=roles/run.builder --condition=None >/dev/null

printf "\nDone. Next: make deploy\n"
