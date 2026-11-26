#!/usr/bin/env bash
# Deploy sales-agent (Cloud Run) with Turnstile injected from Secret Manager.
set -uo pipefail

LOG="/tmp/deploy-sales-agent.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== deploy sales-agent start: $(date) ==="

die() { echo "❌ FAILED at: $1"; echo "   See $LOG"; exit 1; }

# --- config checks ---
[ -n "${GCP_PROJECT:-}" ] || { echo "❌ GCP_PROJECT not set. Run: source scripts/00-config.sh"; exit 1; }
[ -n "${GCP_REGION:-}" ]  || { echo "❌ GCP_REGION not set. Run: source scripts/00-config.sh"; exit 1; }
TURNSTILE_SECRET_NAME="${TURNSTILE_SECRET_NAME:-TURNSTILE_SECRET}"

echo "✅ project=$GCP_PROJECT region=$GCP_REGION secret=$TURNSTILE_SECRET_NAME"

# --- locate source ---
SA_DIR="$(cd "$(dirname "$0")/../sales-agent" && pwd)" || die "cd sales-agent"
cd "$SA_DIR" || die "cd $SA_DIR"
echo "✅ source=$(pwd)"

# --- env.yaml must exist and must NOT contain TURNSTILE_SECRET (collision) ---
[ -f env.yaml ] || die "env.yaml not found in sales-agent/"
if grep -q "^TURNSTILE_SECRET:" env.yaml; then
  die "env.yaml contains TURNSTILE_SECRET — remove it (it is injected from Secret Manager)"
fi
echo "✅ env.yaml present and clean"

# --- secret must exist ---
gcloud secrets describe "$TURNSTILE_SECRET_NAME" --project="$GCP_PROJECT" >/dev/null 2>&1 \
  || die "secret $TURNSTILE_SECRET_NAME not found — run scripts/03-setup-turnstile-secret.sh"
echo "✅ secret exists"

# --- deploy to Cloud Run ---
echo "Deploying sales-agent..."
gcloud run deploy sales-agent \
  --source=. \
  --region="$GCP_REGION" \
  --allow-unauthenticated \
  --env-vars-file=env.yaml \
  --set-secrets="TURNSTILE_SECRET=${TURNSTILE_SECRET_NAME}:latest" \
  --memory=512Mi \
  --timeout=60s \
  --project="$GCP_PROJECT" \
  || die "gcloud run deploy sales-agent"

echo "✅ sales-agent deployed."

URL="$(gcloud run services describe sales-agent --region="$GCP_REGION" --format='value(status.url)' 2>/dev/null)"
echo "🌐 Agent URL: ${URL:-<unknown>}"
echo "=== deploy sales-agent done: $(date) ==="
