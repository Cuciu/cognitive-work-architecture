#!/usr/bin/env bash
# Delete old per-topic context caches (from the pre-vector architecture).
# Safe to skip if you never used per-topic context caching.
set -uo pipefail

export GCP_PROJECT="${GCP_PROJECT:?Set GCP_PROJECT environment variable}"
export REGION="${REGION:-global}"

# Prefix used by the OLD per-topic caches. Change to match your naming.
export CACHE_PREFIX="${CACHE_PREFIX:-agent_}"

cd "$(dirname "$0")/../sales-agent"
PY="$PWD/.venv/bin/python"; [ -x "$PY" ] || PY="python3"

"$PY" - <<'PYEOF'
import os
from google import genai

client = genai.Client(vertexai=True,
    project=os.environ["GCP_PROJECT"],
    location=os.environ.get("REGION", "global"))

prefix = os.environ.get("CACHE_PREFIX", "agent_")

for c in client.caches.list():
    dn = getattr(c, "display_name", "") or ""
    if dn.startswith(prefix) and dn.endswith("_kb"):
        client.caches.delete(name=c.name)
        print("deleted _kb:", dn)

print("done")
PYEOF
