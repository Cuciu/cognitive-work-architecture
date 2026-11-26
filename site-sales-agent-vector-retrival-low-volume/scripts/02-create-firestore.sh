#!/usr/bin/env bash
# ===========================================================================
# Sales Agent — Firestore Setup
# ===========================================================================
# Creates the Firestore database in Native mode AND the vector index used by
# the topic-filtered KB vector search architecture.
#
# Run once per project (safe to re-run — existing resources are skipped).
# Firestore mode cannot be changed after creation.
#
# BUDGET NOTE: Firestore Native mode is chosen over Datastore mode
# because it supports real-time listeners and document-level writes
# with atomic increments (used by budget tracking), AND vector search
# (find_nearest) used by the sales agent for KB retrieval.
#
# VECTOR INDEX NOTE: The KBVectors composite index enables topic-scoped
# nearest neighbour search — filter by `topic` (namespace), then rank by
# `embedding`. This replaces per-topic context caches (token-hour storage)
# with pay-per-query retrieval. The KB vectors are PERSISTENT (no TTL);
# you only re-index when a knowledge file changes (re-run cache_setup.py).
#
# Usage:
#   source scripts/00-config.sh
#   chmod +x scripts/02-create-firestore.sh
#   ./scripts/02-create-firestore.sh
# ===========================================================================

set -euo pipefail

PROJECT=${GCP_PROJECT:?Set GCP_PROJECT environment variable}
REGION=${GCP_REGION:-us-central1}

# Vector index config. Must match the embedding model used by the indexer
# (sales-agent/cache_setup.py) and the agent (sales-agent/main.py).
# text-embedding-005 -> 768 dimensions.
VECTOR_COLLECTION=${VECTOR_COLLECTION:-KBVectors}
EMBED_DIM=${EMBED_DIM:-768}

# ---------------------------------------------------------------------------
# 1) Firestore database (Native mode) — safe to re-run.
# ---------------------------------------------------------------------------
echo "Creating Firestore database for project: $PROJECT in $REGION"

if gcloud firestore databases create \
      --project="$PROJECT" \
      --location="$REGION" \
      --type=firestore-native 2>/tmp/fs_db_err; then
  echo "✅ Firestore database created."
else
  if grep -qiE "already exists|ALREADY_EXISTS|Database already exists" /tmp/fs_db_err; then
    echo "ℹ️  Firestore database already exists — skipping."
  else
    echo "❌ Firestore database create failed:"
    cat /tmp/fs_db_err
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 2) Vector index on KBVectors — enables topic-filtered find_nearest.
#    Composite: topic (ASCENDING) + embedding (VECTOR, cosine-ready).
#    Safe to re-run — existing index is skipped.
# ---------------------------------------------------------------------------
echo "Creating vector index on '${VECTOR_COLLECTION}' (dimension ${EMBED_DIM})..."

if gcloud firestore indexes composite create \
      --project="$PROJECT" \
      --collection-group="$VECTOR_COLLECTION" \
      --query-scope=COLLECTION \
      --field-config field-path=topic,order=ASCENDING \
      --field-config field-path=embedding,vector-config="{\"dimension\":${EMBED_DIM},\"flat\":{}}" \
      2>/tmp/fs_idx_err; then
  echo "✅ Vector index creation started."
else
  if grep -qiE "already exists|ALREADY_EXISTS|index already exists" /tmp/fs_idx_err; then
    echo "ℹ️  Vector index already exists — skipping."
  else
    echo "❌ Vector index create failed:"
    cat /tmp/fs_idx_err
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 3) Report index build status. Index builds are ASYNC — wait for READY
#    before running the agent against it.
# ---------------------------------------------------------------------------
echo ""
echo "Current composite indexes (watch for state: READY):"
gcloud firestore indexes composite list --project="$PROJECT" || true

echo ""
echo "✅ Firestore setup complete."
echo "ℹ️  NOTE: The vector index may take a few minutes to reach READY."
echo "    Re-run this line to check:"
echo "      gcloud firestore indexes composite list --project=$PROJECT"
echo "ℹ️  NEXT: populate vectors with:  python sales-agent/cache_setup.py"
