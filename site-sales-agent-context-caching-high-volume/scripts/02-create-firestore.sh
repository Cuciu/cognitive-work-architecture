#!/usr/bin/env bash
# ===========================================================================
# Cognitive Work Architecture — Site Sales Agent — Firestore Setup
# ===========================================================================
# Creates the Firestore database in Native mode.
# Run once per project. Firestore mode cannot be changed after creation.
#
# BUDGET NOTE: Firestore Native mode is chosen over Datastore mode
# because it supports real-time listeners and document-level writes
# with atomic increments (used by budget tracking).
#
# Usage:
#   export GCP_PROJECT=your-project-id
#   chmod +x 02-create-firestore.sh
#   ./02-create-firestore.sh
# ===========================================================================

set -euo pipefail

PROJECT=${GCP_PROJECT:?Set GCP_PROJECT environment variable}
REGION=${GCP_REGION:-us-central1}

echo "Creating Firestore database for project: $PROJECT in $REGION"

gcloud firestore databases create \
  --project="$PROJECT" \
  --location="$REGION" \
  --type=firestore-native

echo "✅ Firestore database created."
