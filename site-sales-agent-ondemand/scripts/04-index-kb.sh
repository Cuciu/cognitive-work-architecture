#!/usr/bin/env bash
# Hardened KB indexer — logs every step; never kills the parent shell.
set -uo pipefail          # NOTE: no -e; we handle errors explicitly below.

LOG="/tmp/index-kb.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== index-kb start: $(date) ==="

die() { echo "❌ FAILED at: $1"; echo "   See $LOG"; exit 1; }

# 1) Config check
if [ -z "${GCP_PROJECT:-}" ]; then
  echo "❌ GCP_PROJECT not set. Run: source scripts/00-config.sh"
  exit 1
fi
echo "✅ GCP_PROJECT=$GCP_PROJECT"

# 2) Move to sales-agent
SA_DIR="$(cd "$(dirname "$0")/../sales-agent" && pwd)" || die "cd sales-agent"
cd "$SA_DIR" || die "cd $SA_DIR"
echo "✅ cwd=$(pwd)"

# 3) Python available?
command -v python3 >/dev/null 2>&1 || die "python3 not found"
echo "✅ python3=$(python3 --version 2>&1)"

# 4) Create venv (resilient)
if [ ! -d ".venv" ]; then
  echo "Creating venv..."
  python3 -m venv .venv || die "python3 -m venv .venv"
fi
echo "✅ venv ready"

# 5) Use venv WITHOUT 'source'/'activate' (can't disturb parent shell)
VENV_PY="$SA_DIR/.venv/bin/python"
[ -x "$VENV_PY" ] || die "venv python missing at $VENV_PY"

echo "Installing requirements..."
"$VENV_PY" -m pip install -q --upgrade pip || echo "⚠️ pip upgrade warning (continuing)"
"$VENV_PY" -m pip install -q -r requirements.txt || die "pip install -r requirements.txt"
echo "✅ deps installed"

# 6) Env for the indexer
export GCP_PROJECT="$GCP_PROJECT"
export REGION="global"
export EMBED_MODEL="${EMBED_MODEL:-text-embedding-005}"
export KB_DIR="knowledge"
export VECTOR_COLLECTION="KBVectors"

# 7) ADC check (indexer writes Firestore + calls Vertex)
echo "Checking Application Default Credentials..."
"$VENV_PY" - <<'PY' || die "ADC not configured — run: gcloud auth application-default login"
import google.auth
creds, proj = google.auth.default()
print("✅ ADC OK, quota project:", proj)
PY

# 8) Run the indexer (no set -e, so a crash prints but won't kill parent)
echo "Indexing knowledge into KBVectors..."
if "$VENV_PY" cache_setup.py; then
  echo "✅ KB indexed successfully."
else
  rc=$?
  echo "❌ cache_setup.py exited with code $rc"
  echo "   Full log: $LOG"
  exit "$rc"
fi

echo "=== index-kb done: $(date) ==="
