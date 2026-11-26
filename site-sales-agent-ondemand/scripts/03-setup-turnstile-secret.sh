#!/usr/bin/env bash
# Create/update the Turnstile secret in Secret Manager + grant Cloud Run access.
set -uo pipefail

LOG="/tmp/setup-turnstile.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== setup-turnstile start: $(date) ==="

die() { echo "❌ FAILED at: $1"; echo "   See $LOG"; exit 1; }

[ -n "${GCP_PROJECT:-}" ] || { echo "❌ GCP_PROJECT not set. Run: source scripts/00-config.sh"; exit 1; }

# Default matches the EXISTING lowercase secret. Overridden by 00-config.sh if set.
SECRET_NAME="${TURNSTILE_SECRET_NAME:-turnstile_secret}"

# The secret VALUE must come from the shell env, never committed:
#   export TURNSTILE_VALUE='your-cloudflare-turnstile-secret'
[ -n "${TURNSTILE_VALUE:-}" ] || die "Set TURNSTILE_VALUE in your shell first (do not commit it)"

echo "✅ project=$GCP_PROJECT secret=$SECRET_NAME"

# 1) Create the secret if missing (idempotent)
if ! gcloud secrets describe "$SECRET_NAME" --project="$GCP_PROJECT" >/dev/null 2>&1; then
  echo "Creating secret $SECRET_NAME..."
  gcloud secrets create "$SECRET_NAME" \
    --replication-policy="automatic" \
    --project="$GCP_PROJECT" || die "create secret"
else
  echo "ℹ️  Secret $SECRET_NAME already exists — will add a new version."
fi

# 2) Add a new version with the value
printf '%s' "$TURNSTILE_VALUE" | \
  gcloud secrets versions add "$SECRET_NAME" --data-file=- --project="$GCP_PROJECT" \
  || die "add secret version"
echo "✅ secret value stored"

# 3) Grant the Cloud Run runtime service account access
PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')" \
  || die "get project number"
RUNTIME_SA="${RUNTIME_SA:-${PROJECT_NUMBER}-compute@developer.gserviceaccount.com}"

gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="$GCP_PROJECT" || die "grant secretAccessor"

echo "✅ granted secretAccessor to ${RUNTIME_SA}"
echo "=== setup-turnstile done: $(date) ==="
