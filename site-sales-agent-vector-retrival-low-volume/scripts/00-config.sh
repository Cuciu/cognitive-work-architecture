#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Shared config for the sales-agent deploy scripts.
# USAGE:  source scripts/00-config.sh      (NOT ./scripts/00-config.sh)
# It exports env vars into your CURRENT shell for the other scripts to read.
# ---------------------------------------------------------------------------

# --- Core project ---
export GCP_PROJECT="your-gcp-project-id"       # <-- CHANGE THIS
export GCP_REGION="us-central1"                 # Cloud Run / Functions region

# --- Models (must match indexer + firestore index dimension) ---
export MODEL="gemini-3.5-flash-lite"
export EMBED_MODEL="text-embedding-005"         # 768-dim

# --- refresh-cache trigger ---
export REFRESH_TOPIC="monitor-budget-topic"

# --- Secret Manager ---
export TURNSTILE_SECRET_NAME="TURNSTILE_SECRET"

# --- Apply gcloud defaults (quietly) ---
gcloud config set project "$GCP_PROJECT" >/dev/null 2>&1 || true
gcloud config set run/region "$GCP_REGION" >/dev/null 2>&1 || true

echo "✅ Config loaded:"
echo "   GCP_PROJECT=$GCP_PROJECT"
echo "   GCP_REGION=$GCP_REGION"
echo "   MODEL=$MODEL  EMBED_MODEL=$EMBED_MODEL"
echo "   REFRESH_TOPIC=$REFRESH_TOPIC"
echo "   TURNSTILE_SECRET_NAME=$TURNSTILE_SECRET_NAME"
