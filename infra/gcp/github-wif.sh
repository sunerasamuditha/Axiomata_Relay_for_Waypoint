set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck disable=SC1091
source infra/gcp/env.sh
: "${GH_REPO:?Set GH_REPO=<owner>/<repo>}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
DEPLOYER="relay-deployer@$PROJECT_ID.iam.gserviceaccount.com"
RUNTIME="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud artifacts repositories describe relay --location="$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create relay --repository-format=docker --location="$REGION"

gcloud iam service-accounts describe "$DEPLOYER" >/dev/null 2>&1 ||
  gcloud iam service-accounts create relay-deployer --display-name="GitHub Actions deployer"
for r in roles/run.admin roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOYER" --role="$r" --condition=None >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --member="serviceAccount:$DEPLOYER" --role=roles/iam.serviceAccountUser >/dev/null

gcloud iam workload-identity-pools describe github --location=global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create github --location=global --display-name="GitHub"
gcloud iam workload-identity-pools providers describe github --location=global --workload-identity-pool=github >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc github \
    --location=global --workload-identity-pool=github \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='$GH_REPO'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GH_REPO" >/dev/null

PROVIDER="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github"
# plain KEY=VALUE lines (macOS ships bash 3.2, which has no associative arrays)
VARS="GCP_PROJECT_ID=$PROJECT_ID
GCP_REGION=$REGION
GCP_SERVICE=$SERVICE
GCP_WIF_PROVIDER=$PROVIDER
GCP_DEPLOYER_SA=$DEPLOYER
GCP_RUNTIME_SA=$RUNTIME
GCP_SQL_CONN=$PROJECT_ID:$REGION:$SQL_INSTANCE"
if command -v gh >/dev/null 2>&1; then
  printf '%s\n' "$VARS" | while IFS='=' read -r k v; do gh variable set "$k" --repo "$GH_REPO" --body "$v"; done
  echo "Repository variables set on $GH_REPO."
else
  echo "Set these repository variables (Settings → Secrets and variables → Actions → Variables):"
  printf '%s\n' "$VARS" | sed 's/^/  /'
fi
