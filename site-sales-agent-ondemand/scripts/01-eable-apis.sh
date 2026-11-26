#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Enable required GCP APIs for the sales agent stack.
# Run once per project, or after project creation.
# Prerequisites: source scripts/00-config.sh
# ---------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

echo "🔧 Enabling APIs for project: $GCP_PROJECT"

gcloud services enable \
  aiplatform.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  --project="$GCP_PROJECT"

echo "✅ All required APIs enabled."
