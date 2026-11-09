# Cognitive Work Architecture
Production-grade AI agent framework with built-in budget governance, behavioral guardrails, and security — open source under MIT.
This repo contains the first published agent: the **Site Sales Agent**, a conversational AI that handles inbound sales conversations on a website with the same rigor you'd expect from a senior enterprise sales advisor.
> **Prompt = suggestion. Code = law.**
>
> Every behavioral rule is enforced in code, not just instructed in prompts. The model might ignore a prompt instruction. It cannot ignore a `strip_premature_cta_language()` function that regex-strips booking language before turn 3.
---
## Architecture Overview
### Budget Governance Lifecycle
**Track** (per request, `main.py`) → **Monitor** (every 4h, `monitor-budget`) → **Enforce** (auto-degrade, `sales-agent`) → **Reset** (monthly, `reset-budget`) → _new month starts_ → **Track** …
### Budget Thresholds
| Spend Range | Tier | Behavior |
|-------------|------|----------|
| $0 — $8 | ✅ **Full** | All model calls active |
| $8 — $10 | ⚠️ **Warning** | Alert fired (once), ops notified |
| $10 — $12 | 🛑 **Degraded** | Model calls stop, fallback CTA served |
| > $12 | 🚫 **Hard Ceiling** | Absolute stop, no exceptions |
### 5-Layer Output Guardrail Pipeline
Every model response passes through five processing layers before reaching the visitor:
1. **`smart_truncate()`** — Cap at 700 chars, cut at sentence boundary
2. **`strip_premature_cta_language()`** — Regex-remove booking language before turn 3
3. **Extract `[BOOK_CTA]` marker** — Signal retained for CTA logic, marker removed from display text
4. **`strip_urls()`** — Remove any URLs the model leaked (model is told not to; code enforces it)
5. **CTA gate check** — Booking button only renders if `turn_count >= MIN_TURNS_BEFORE_CTA`
### Cost Optimization
| Path | Rate | When |
|------|------|------|
| **Cached input** | $0.15 / 1M tokens | Context cache active (normal) |
| **Non-cached input** | $1.50 / 1M tokens | Cache expired or missing — **10x more expensive** |
| **Output** | $7.50 / 1M tokens | Always |
Cache miss = agent still works, just costs more. Graceful degradation, not failure.
---
## Design Principles
### 🛡️ Guardrails — Defense in Depth
The system prompt tells the model what to do. Code enforces it.
| Layer | What It Does | Why It Exists |
|-------|-------------|---------------|
| `MAX_PROMPTS_PER_SESSION = 6` | Hard session cap | A conversation that hasn't converted in 6 turns won't convert in 60 |
| `MAX_ANSWER_CHARS = 700` | Character limit with smart truncation | Prevents wall-of-text responses in a small chat window |
| `MAX_OUTPUT_TOKENS = 250` | Model-level output cap | Defense in depth — even if truncation fails |
| `MAX_HISTORY_TURNS = 10` | Conversation window cap | Bounds token costs as conversations grow |
| `MIN_TURNS_BEFORE_CTA = 3` | Booking CTA turn gate | Code-level override — model cannot push meetings early |
| `strip_urls()` | Removes any URLs from model output | Model is told not to output URLs; this enforces it |
| `strip_premature_cta_language()` | Regex removal of booking language | Catches "Shall we schedule..." before the turn gate |
### 💰 Budget Governance — Per-Token Cost Tracking
Most agent builders discover they've overspent when the invoice arrives. This agent tracks cost **per request** and enforces monthly budgets with three escalation tiers.
- **Cached vs. non-cached token rates** tracked separately
- **Firestore atomic increments** prevent race conditions on spend accumulation
- **Auto-recovery**: if budget headroom returns (manual increase or new month), the tier automatically restores to "Full" — no human intervention required
- **Alert deduplication**: threshold alerts fire once, not every 4 hours
### 🔒 Security
| Measure | Implementation |
|---------|---------------|
| Bot protection | Cloudflare Turnstile verification on every request |
| CORS | Configurable origin, explicit method/header allowlist |
| Secrets | All credentials via environment variables, never in source |
| Error handling | Stack traces truncated — never exposed to clients |
| Input validation | Email regex, `.strip()` sanitization, silent JSON parsing |
| URL stripping | Model output scrubbed of any leaked URLs |
| XSS prevention | `escapeHtml()` on all user input before rendering |
### ⚡ Why This Approach
| Decision | Alternative | Why This Is Better |
|----------|------------|-------------------|
| Context caching (25hr TTL) | Send site content every request | 10x cheaper at scale |
| `flash-lite` model | Larger models | Sufficient quality at fraction of cost |
| Stateless server | Server-side session storage | No PII stored server-side |
| 4-state conversation machine | Stateless chatbot | Feels like a senior advisor, not a FAQ bot |
| Code-level output pipeline | Prompt-only instructions | Models don't always follow prompts; code always executes |
| Graceful degradation | Hard error on budget/failure | Visitors always see a professional response |
| Fire-and-forget CRM webhook | Synchronous CRM integration | CRM failure never blocks lead capture |
---
## Repository Structure
    cognitive-work-architecture/
    ├── .gitignore
    ├── LICENSE                              MIT
    ├── README.md                            This file
    │
    └── site-sales-agent/                    First published agent
        │
        ├── site_content_template.txt        Agent knowledge base (template + example)
        │
        ├── scripts/
        │   ├── 01-enable-apis.sh            Enable required GCP APIs
        │   └── 02-create-firestore.sh       Create Firestore database
        │
        ├── capture-lead/
        │   ├── main.py                      Lead capture Cloud Function
        │   ├── requirements.txt
        │   └── .env.example
        │
        ├── refresh-cache/
        │   ├── main.py                      Scheduled context cache refresh
        │   ├── requirements.txt
        │   ├── .env.example
        │   └── .gitignore
        │
        ├── sales-agent/
        │   ├── main.py                      Core conversational agent
        │   ├── cache_setup.py               CLI cache creation (manual/debug)
        │   ├── requirements.txt
        │   ├── .env.example
        │   ├── .gitignore
        │   └── site_content_template.txt    Agent knowledge base
        │
        ├── monitor-budget/
        │   ├── main.py                      Budget threshold monitor (every 4h)
        │   ├── requirements.txt
        │   └── .env.example
        │
        ├── reset-budget/
        │   ├── main.py                      Monthly budget reset
        │   ├── requirements.txt
        │   └── .env.example
        │
        └── widget/
            └── widget.html                  Self-contained chat widget (HTML+CSS+JS)
---
## Module Reference
### capture-lead/ — Lead Capture
Serverless endpoint that captures inbound leads with email validation, Turnstile bot protection, Firestore persistence, and optional CRM webhook forwarding. Links leads back to agent sessions for attribution.
### refresh-cache/ — Context Cache Refresh
Scheduled Cloud Function that creates a Gemini context cache from site content. The cache reduces input token costs by ~75%. Runs on a schedule with a 25-hour TTL. Stores the cache name in Firestore so the agent picks it up at runtime without redeployment.
### sales-agent/ — Core Agent
The conversational engine. Processes one turn per request with a 5-layer output guardrail pipeline, per-request cost tracking, and cache-first inference with inline fallback. Includes `cache_setup.py` for manual cache creation during initial deployment or debugging.
**Conversation State Machine:**
| State | Name | Behavior |
|-------|------|----------|
| 1 | **Discovery** | Ask one question. Enter once, never return. |
| 2 | **Acknowledge** | Validate their challenge. One sentence. |
| 3 | **Solution** | Match problem to capability. Primary operating state. |
| 4 | **Intent** | Booking CTA. Only on explicit buying signals. Minimum 2 substantive exchanges required. |
### monitor-budget/ — Budget Monitor
Runs every 4 hours via Cloud Scheduler. Checks spend against DegradeThreshold (80%) and MonthlyLimit (100%). Fires deduplicated alerts via webhook (Slack, Teams, PagerDuty). Sets ServiceTier to "Degraded" when budget is exhausted. Auto-recovers when headroom returns.
### reset-budget/ — Monthly Budget Reset
Runs on the 1st of each month. Resets spend counters and alert flags. Preserves custom budget limits — if you manually adjusted MonthlyLimitUSD mid-month, the reset only clears counters, not config.
### widget/ — Chat Widget
Self-contained HTML+CSS+JS chat widget. Zero dependencies, no build step. Paste before `</body>` on any page. Features:
- Rotating teaser bubbles (random engagement hook, shown once per session)
- `#chat` deep linking (any `<a href="#chat">` opens the widget)
- Turnstile bot protection with queue-based send
- Conversation history sent with each request (stateless server)
- Lead capture form at session end
- WordPress-compatible (`!important` positioning, max z-index)
---

---

## Getting Started

### Prerequisites

- Google Cloud Platform account with billing enabled
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated
- Cloudflare account with Turnstile configured (free tier works)
- Python 3.11+

---
### Step 1 — Enable APIs and Create Firestore
```bash
export GCP_PROJECT=your-project-id
cd site-sales-agent/scripts
chmod +x 01-enable-apis.sh 02-create-firestore.sh
./01-enable-apis.sh
./02-create-firestore.sh 
```
Step 2 — Prepare Site Content
```bash
cd site-sales-agent/sales-agent
cp site_content_template.txt site_content_full.txt
```
Edit site_content_full.txt with your services, proof points, etc.

Step 3 — Create the Context Cache
```bash
export GCP_PROJECT=your-project-id
python cache_setup.py
```

Step 4 — Deploy Cloud Functions
Sales agent:
```bash
cd site-sales-agent/sales-agent
gcloud functions deploy sales-agent \
  --gen2 --runtime=python311 --trigger-http \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT=$GCP_PROJECT,CONSULTATION_URL=https://your-booking-url" \
  --entry-point=sales_agent
```
Lead capture:
```bash
cd ../capture-lead
gcloud functions deploy capture-lead \
  --gen2 --runtime=python311 --trigger-http \
  --allow-unauthenticated \
  --entry-point=capture_lead
```
Cache refresh (scheduled):
```bash
cd ../refresh-cache
gcloud functions deploy refresh-cache \
  --gen2 --runtime=python311 --trigger-topic=refresh-cache \
  --entry-point=create_site_cache
```
Budget monitor (scheduled):
```bash
cd ../monitor-budget
gcloud functions deploy monitor-budget \
  --gen2 --runtime=python311 --trigger-topic=monitor-budget \
  --entry-point=monitor_budget
```
Budget reset (scheduled):
```bash
cd ../reset-budget
gcloud functions deploy reset-budget \
  --gen2 --runtime=python311 --trigger-topic=reset-budget \
  --entry-point=reset_budget
```
Step 5 — Set Up Schedules
Cache refresh — daily at 2am UTC:
```bash
gcloud scheduler jobs create pubsub refresh-cache-daily \
  --schedule="0 2 * * *" --topic=refresh-cache \
  --message-body="{}"
```
Budget monitor — every 4 hours:
```bash
gcloud scheduler jobs create pubsub monitor-budget-4h \
  --schedule="0 */4 * * *" --topic=monitor-budget \
  --message-body="{}"
```
Budget reset — 1st of each month at midnight UTC:
```bash
gcloud scheduler jobs create pubsub reset-budget-monthly \
  --schedule="0 0 1 * *" --topic=reset-budget \
  --message-body="{}"
```
Step 6 — Deploy the Widget
Update AGENT_URL, LEAD_URL, and SITE_KEY in widget/widget.html
Paste the contents of widget.html before </body> on your site

System Prompt Architecture
The agent uses a 4-state conversation machine with explicit transition rules, forbidden transitions, and behavioral constraints. Key design choices:
"Answer ONLY from provided content" — prevents hallucinated capabilities
"Never write a URL" — enforced in both prompt AND code via strip_urls()
"[BOOK_CTA] at most ONCE" — prevents aggressive sales behavior
"Not in first two responses" — forces value-first conversation
"Low-energy reply handling" — prevents misreading politeness as purchase intent
The prompt is intentionally duplicated across three files (main.py, cache_setup.py, refresh-cache/main.py). This is a deployment isolation decision — each module deploys independently as a separate Cloud Function. Shared imports would create coupling and versioning risk.
See the inline comments in sales-agent/main.py for the full rationale.
Prompt Duplication — Architectural Trade-off
The system prompt appears in three files. This is deliberate:
Approach	Trade-off
Shared import	Deployment coupling — updating one function requires redeploying all three
Firestore/GCS storage	Network dependency on cold start — adds ~200ms latency
Duplication (current)	Manual sync required, but each module deploys independently with zero coupling
If you update the prompt, update all three files. The comment "MUST be identical" in each file is the manual contract.
Cost Model
At the time of writing (Gemini 2.5 Flash Lite pricing):
Component	Rate
Cached input tokens	$0.15 / 1M tokens
Non-cached input tokens	$1.50 / 1M tokens
Output tokens	$7.50 / 1M tokens
Example: 1,000 conversations/month, average 4 turns each
Scenario	Monthly Cost
With cache	~$2.50
Without cache	~$18.00
Savings	~85%
The default budget is set to $10/month with degradation at $8 and a hard ceiling at $12. Adjust in reset-budget/main.py or directly in Firestore.
Part of the Cognitive Work Architecture Series
This repo is the code companion to the Cognitive Work Architecture series on LinkedIn — exploring how to build AI agents that operate with the same governance, accountability, and cost discipline you'd expect from any production enterprise system.
The architecture is the moat. The code is open.
License
MIT — see LICENSE for details.
Use it, fork it, build on it. The architecture decisions are documented in inline comments throughout the codebase. If you find them useful, a star helps others find the repo.
