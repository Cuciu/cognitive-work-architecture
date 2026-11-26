#!/usr/bin/env bash
# Smoke test: send sample messages and verify the router classifies correctly.
set -uo pipefail

# Get the agent URL (or use AGENT_URL if already exported)
REGION="${GCP_REGION:-us-central1}"
URL="${AGENT_URL:-$(gcloud run services describe sales-agent --region="$REGION" --format='value(status.url)')}"

if [ -z "$URL" ]; then
  echo "❌ Could not resolve agent URL. Is sales-agent deployed?"
  exit 1
fi
echo "Agent URL: $URL"
echo "-------------------------------------------"

run_test () {
  local msg="$1"
  local expect="$2"
  local got
  got=$(curl -s -X POST "$URL" \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"$msg\",\"session_id\":\"rt-$RANDOM\",\"history\":[]}" \
        | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get("active_flow","<no active_flow>"))
except Exception as e:
    print("<parse error / maybe 403 Turnstile>")' 2>/dev/null)
  printf "expect=%-13s got=%s\n" "$expect" "$got"
}

# ---------------------------------------------------------------------------
# Replace these with test cases that match YOUR topic taxonomy.
# Each test: a sample user message + the expected topic classification.
# ---------------------------------------------------------------------------
run_test "tell me about your cloud migration services"  "TOPIC_A"
run_test "how do you handle AI governance"              "TOPIC_B"
run_test "our costs are out of control"                 "TOPIC_C"
run_test "we need help with executive advisory"         "TOPIC_D"
run_test "hi what do you do"                            "GENERAL"
