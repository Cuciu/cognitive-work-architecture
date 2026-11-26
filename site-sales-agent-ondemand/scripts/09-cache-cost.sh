#!/usr/bin/env bash
# List Vertex AI context caches + estimate daily storage cost.
set -uo pipefail

# Self-contained defaults so it works even without 00-config.sh:
export GCP_PROJECT="${GCP_PROJECT:?Set GCP_PROJECT environment variable}"
export REGION="${REGION:-global}"

cd "$(dirname "$0")/../sales-agent"
PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || PY="python3"   # fall back if venv missing

"$PY" - <<'PYEOF'
import os
from google import genai

# ---- SET THIS to your model's cache-STORAGE rate (USD per 1M tokens / HOUR) ----
# Verify against billing SKU "...Text Input Caching Storage" or Vertex pricing.
STORAGE_RATE_PER_1M_TOKEN_HOUR = 0.075   # <-- REPLACE with your real rate

project = os.environ["GCP_PROJECT"]
region  = os.environ.get("REGION", "global")
print(f"Project: {project} | Region: {region}\n")

client = genai.Client(vertexai=True, project=project, location=region)

print(f"{'DISPLAY NAME':35} {'TOKENS':>10} {'TTL':>14}   {'$/day':>10}")
print("-" * 78)

total = 0.0
count = 0
for c in client.caches.list():
    count += 1
    dn = getattr(c, "display_name", "") or "(no name)"
    um = getattr(c, "usage_metadata", None)
    tokens = 0
    if um is not None:
        tokens = (getattr(um, "total_token_count", 0)
                  or getattr(um, "token_count", 0) or 0)
    if not tokens:   # some SDKs only populate on get()
        try:
            full = client.caches.get(name=c.name)
            um = getattr(full, "usage_metadata", None)
            tokens = getattr(um, "total_token_count", 0) if um else 0
        except Exception:
            pass
    ttl = getattr(c, "ttl", "") or getattr(c, "expire_time", "")
    daily = (tokens / 1_000_000) * STORAGE_RATE_PER_1M_TOKEN_HOUR * 24
    total += daily
    print(f"{dn[:35]:35} {tokens:>10,} {str(ttl)[:14]:>14}   ${daily:>8.4f}")

print("-" * 78)
print(f"{'TOTAL':35} {'':>10} {'':>14}   ${total:>8.4f} / day")
print(f"\nCaches found: {count}")
print(f"Estimated monthly (x30): ${total*30:.2f}")
print(f"(rate used: ${STORAGE_RATE_PER_1M_TOKEN_HOUR}/1M tokens/hour — verify vs billing)")
PYEOF
