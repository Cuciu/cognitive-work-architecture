#!/usr/bin/env bash
# ===========================================================================
# Cognitive Work Architecture — Site Sales Agent — API Setup
# ===========================================================================
# Enables required GCP APIs for the agent infrastructure.
# Run once per project before deploying any Cloud Functions.
#
# Usage:
#   export GCP_PROJECT=your-project-id
#   chmod +x 01-enable-apis.sh
#   ./01-enable-apis.sh
# ===========================================================================

set -euo pipefail

PROJECT=${GCP_PROJECT:?Set GCP_PROJECT environment variable}

echo "Enabling APIs for project: $PROJECT"

gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  cloudscheduler.googleapis.com \
  pubsub.googleapis.com \
  --project="$PROJECT"

echo "✅ All APIs enabled."
