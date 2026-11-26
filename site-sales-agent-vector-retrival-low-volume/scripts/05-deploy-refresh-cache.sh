#!/usr/bin/env bash
# Deploy refresh-cache (router cache: global.md) + fire once.
set -uo pipefail

LOG="/tmp/deploy-refresh-cache.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== deploy refresh-cache start: $(date) ==="

die() { echo "❌ FAILED at: $1"; echo "   See $LOG"; exit 1; }

# --- config checks ---
[ -n "${GCP_PROJECT:-}" ] || { echo "❌ GCP_PROJECT not set. Run: source scripts/00-config.sh"; exit 1; }
[ -n "${GCP_REGION:-}" ]  || { echo "❌ GCP_REGION not set. Run: source scripts/00-config.sh"; exit 1; }
[ -n "${REFRESH_TOPIC:-}" ] || { echo "❌ REFRESH_TOPIC not set. Run: source scripts/00-config.sh"; exit 1; }
MODEL="${MODEL:-gemini-3.5-flash-lite}"

echo "✅ project=$GCP_PROJECT region=$GCP_REGION topic=$REFRESH_TOPIC model=$MODEL"

# --- locate source ---
FN_DIR="$(cd "$(dirname "$0")/../refresh-cache" && pwd)" || die "cd refresh-cache"
cd "$FN_DIR" || die "cd $FN_DIR"
echo "✅ source=$(pwd)"

# --- ensure the Pub/Sub topic exists (idempotent) ---
if ! gcloud pubsub topics describe "$REFRESH_TOPIC" --project="$GCP_PROJECT" >/dev/null 2>&1; then
  echo "Creating Pub/Sub topic $REFRESH_TOPIC..."
  gcloud pubsub topics create "$REFRESH_TOPIC" --project="$GCP_PROJECT" || die "create topic"
fi
echo "✅ topic ready"

# --- deploy (gen2 function, pub/sub trigger) ---
echo "Deploying refresh-cache..."
gcloud functions deploy refresh-cache \
  --gen2 \
  --runtime=python312 \
  --region="$GCP_REGION" \
  --source=. \
  --entry-point=create_site_cache \
  --trigger-topic="$REFRESH_TOPIC" \
  --set-env-vars="GCP_PROJECT=${GCP_PROJECT},REGION=global,MODEL=${MODEL}" \
  --memory=512Mi \
  --timeout=120s \
  --project="$GCP_PROJECT" \
  || die "gcloud functions deploy refresh-cache"

echo "✅ refresh-cache deployed."

# --- fire once to build the router cache ---
echo "Triggering one refresh..."
gcloud pubsub topics publish "$REFRESH_TOPIC" --message="manual-refresh" --project="$GCP_PROJECT" \
  || echo "⚠️ publish warning (deploy still succeeded)"

echo ""
echo "✅ Done. Check logs for: 'Router cache refreshed: ... (global.md ... whole, not chunked)'"
echo "   gcloud functions logs read refresh-cache --region=$GCP_REGION --gen2 --limit=20"
echo "=== deploy refresh-cache done: $(date) ==="
