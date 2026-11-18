# Site Sales Agent
Production-grade AI sales agent with built-in budget governance, behavioral guardrails, and security — open source under MIT.
A conversational AI that handles inbound sales conversations on a website with the same rigor you'd expect from a senior enterprise sales advisor.
> **Prompt = suggestion. Code = law.**
>
> Every behavioral rule is enforced in code, not just instructed in prompts.
> The model might ignore a prompt instruction. It cannot ignore a
> `strip_premature_cta_language()` function that regex-strips booking
> language before turn 3.
---
## Architecture Overview
```mermaid
flowchart LR
    W[Widget<br/>browser] -->|HTTP POST + full history| SA[Sales Agent<br/>Cloud Function]
    SA --> VA[Vertex AI<br/>Gemini]
    SA --> FS[Firestore<br/>state]
    SA --> CL[Capture Lead<br/>Cloud Function]
    SCH[Cloud Scheduler] --> MB[Monitor Budget<br/>every 4h]
    SCH --> RB[Reset Budget<br/>monthly]
    SCH --> RC[Refresh Cache<br/>daily]
    MB --> FS
    RB --> FS
    RC --> VA
    style W fill:#0d497a,color:#fff
    style SA fill:#0d497a,color:#fff
    style VA fill:#1a73e8,color:#fff
    style FS fill:#f9ab00,color:#000
    style SCH fill:#666,color:#fff
Per-Topic Caching with Intent Classification
The agent classifies each visitor's intent on their first message, locks a topic flow for the session, and routes to a topic-specific Vertex AI content cache. This means:
System prompt lives in code, injected at call time — never cached
Knowledge base is split per topic — one cache per service line
Adding a new topic = add one .md file + one TOPIC_FILES entry
Cache miss = agent still works with inline fallback (graceful, not broken)
flowchart TD MSG[First message] --> CL{classify_intent} CL -->|TOPIC_A| CA[Topic A Cache<br/>KB content only] CL -->|TOPIC_B| CB[Topic B Cache<br/>KB content only] CL -->|GENERAL| GEN[No cache<br/>system prompt + global.md] CA --> SYS[system_instruction<br/>injected at call time] CB --> SYS GEN --> SYS SYS --> RESP[Generate response] RESP --> P1[smart_truncate] P1 --> P2[strip_premature_cta] P2 --> P3[extract BOOK_CTA] P3 --> P4[strip_urls] P4 --> P5[CTA gate check] P5 --> OUT[Response to visitor]
Caches are set with a 35-day TTL (3024000s) covering a 31-day billing cycle plus buffer. The daily refresh schedule rebuilds caches every 24 hours — the TTL is a safety net, not the primary refresh mechanism.
Budget Governance Lifecycle
Track (per request) → Monitor (every 4h) → Enforce (auto-degrade) → Reset (monthly) → new month → Track …
Spend Range	Tier	Behavior
$0 — $8	✅ Full	All model calls active
$8 — $10	⚠️ Warning	Alert fired (once), ops notified
$10 — $12	🛑 Degraded	Model calls stop, fallback CTA served
> $12	🚫 Hard Ceiling	Absolute stop, no exceptions
5-Layer Output Guardrail Pipeline
Every model response passes through five processing layers before reaching the visitor:
smart_truncate() — Cap at 700 chars, cut at sentence boundary
strip_premature_cta_language() — Regex-remove booking language before turn 3
Extract [BOOK_CTA] marker — Signal retained for CTA logic, marker removed from text
strip_urls() — Remove any URLs the model leaked (model is told not to; code enforces it)
CTA gate check — Booking button only renders if turn_count >= MIN_TURNS_BEFORE_CTA
Cost Optimization
Path	Rate	When
Cached input	$0.15 / 1M tokens	Topic cache active — 35-day TTL, refreshed daily
Non-cached input	$1.50 / 1M tokens	Cache expired or missing — 10× more expensive
Output	$7.50 / 1M tokens	Always
Cache miss = agent still works, just costs more. Graceful degradation, not failure.
Design Principles
🧠 Stateless Server — Client-Side Conversation Memory
The server stores zero conversation content. The widget sends the full conversation history with every request. The server processes one turn and responds — it never persists what was said.
How it works:
Widget maintains history[] array in browser memory
Every API call sends the full history (capped at MAX_HISTORY = 10 turns)
Server rebuilds context from the payload, generates one response, forgets
Server stores only metadata: prompt count, budget spend, active flow
Why this matters:
Benefit	Detail
No PII server-side	Conversation text never touches the database
No session storage costs	Firestore stores 4 fields per session, not full transcripts
Defense in depth	Client caps at 10 turns, server independently caps at 10 turns
Instant scalability	No session affinity — any function instance can serve any request
GDPR-friendly	Nothing to delete if the visitor never submits a lead form
The trade-off: if the visitor refreshes the page, conversation history is lost. This is intentional — a sales conversation that requires persistence across page loads needs a CRM, not a chat widget.
🛡️ Guardrails — Defense in Depth
The system prompt tells the model what to do. Code enforces it.
Layer	What It Does	Why It Exists
MAX_PROMPTS_PER_SESSION = 6	Hard session cap	A conversation that hasn't converted in 6 turns won't convert in 60
MAX_ANSWER_CHARS = 700	Character limit with smart truncation	Prevents wall-of-text responses in a small chat window
MAX_OUTPUT_TOKENS = 250	Model-level output cap	Defense in depth — even if truncation fails
MAX_HISTORY_TURNS = 10	Conversation window cap	Bounds token costs as conversations grow
MIN_TURNS_BEFORE_CTA = 3	Booking CTA turn gate	Code-level override — model cannot push meetings early
strip_urls()	Removes any URLs from model output	Model is told not to output URLs; this enforces it
strip_premature_cta_language()	Regex removal of booking language	Catches "Shall we schedule..." before the turn gate
💰 Budget Governance — Per-Token Cost Tracking
Most agent builders discover they've overspent when the invoice arrives. This agent tracks cost per request and enforces monthly budgets with three escalation tiers.
Cached vs. non-cached token rates tracked separately
Firestore atomic increments prevent race conditions on spend accumulation
Auto-recovery: if budget headroom returns (manual increase or new month), the tier automatically restores to Full — no human intervention required
Alert deduplication: threshold alerts fire once, not every 4 hours
🔒 Security
Measure	Implementation
Bot protection	Cloudflare Turnstile verification on every request
CORS	Configurable origin, explicit method/header allowlist
Secrets	All credentials via environment variables, never in source
Error handling	Stack traces truncated — never exposed to clients
Input validation	Email regex, .strip() sanitization, silent JSON parsing
URL stripping	Model output scrubbed of any leaked URLs
XSS prevention	escapeHtml() on all user input before rendering
⚡ Why This Approach
Decision	Alternative	Why This Is Better
Per-topic caching (35-day TTL)	Single monolithic cache	Smaller caches, higher hit rate, cheaper. 31-day billing alignment.
Intent classification + flow lock	Stateless topic detection	Consistent experience, no topic drift mid-conversation
System prompt at call time	System prompt in cache	One source of truth, easy to update without cache rebuild
Client-side history	Server-side session storage	No PII stored, no session affinity, GDPR-friendly
flash-lite model	Larger models	Sufficient quality at fraction of cost
4-state conversation machine	Stateless chatbot	Feels like a senior advisor, not a FAQ bot
Code-level output pipeline	Prompt-only instructions	Models don't always follow prompts; code always executes
Graceful degradation	Hard error on budget/failure	Visitors always see a professional response
Fire-and-forget CRM webhook	Synchronous CRM integration	CRM failure never blocks lead capture
Repository Structure
skyalleys-sales-agent/
│
├── .gitignore
├── LICENSE                          MIT
├── README.md                        This file
├── README-cloudshell.txt            Cloud Shell quick-start
│
├── capture-lead/
│   ├── main.py                      Lead capture endpoint
│   └── requirements.txt
│
├── refresh-cache/
│   ├── main.py                      Scheduled cache rebuild
│   ├── requirements.txt
│   └── knowledge/
│       ├── README.md                KB file format docs
│       └── example.md               KB template
│
├── sales-agent/
│   ├── main.py                      Core agent logic
│   ├── cache_setup.py               Manual cache builder (CLI)
│   ├── env.yaml.example             Env var template
│   ├── system_prompt.example.md     Prompt template
│   ├── requirements.txt
│   └── knowledge/
│       ├── README.md                KB file format docs
│       └── example.md               KB template
│
├── scheduled-jobs/
│   ├── monitor-budget/
│   │   ├── main.py                  Budget monitor (4h cycle)
│   │   └── requirements.txt
│   └── reset-budget/
│       ├── main.py                  Monthly budget reset
│       └── requirements.txt
│
├── scripts/
│   ├── 01-enable-apis.sh            Enable GCP APIs
│   └── 02-create-firestore.sh       Create Firestore DB
│
└── widget/
    ├── widget.html                  Chat widget (single-file)
    └── widget.js                    Standalone JS (CSP option)
What's Not in This Repo
The following files are required at runtime but excluded from source control:
File	Purpose	How to Create
knowledge/*.md	Per-topic knowledge base content	Populate using knowledge/example.md as template
system_prompt.md	Agent system prompt and conversation rules	Populate using system_prompt.example.md as template
env.yaml	Environment variables and secrets	Populate using env.yaml.example as template
Module Reference
capture-lead/ — Lead Capture
Serverless endpoint that captures inbound leads with email validation, Turnstile bot protection, Firestore persistence, and optional CRM webhook forwarding. Links leads back to agent sessions for attribution.
refresh-cache/ — Per-Topic Cache Refresh
Scheduled Cloud Function that rebuilds Vertex AI content caches for each topic. Each topic gets its own lean cache (KB content only — no system prompt). Caches are set with a 35-day TTL covering a 31-day billing cycle plus buffer. The daily schedule ensures caches are refreshed well before expiry. Writes the CacheMap to Firestore so the agent picks up new caches at runtime without redeployment.
sales-agent/ — Core Agent
The conversational engine. Classifies visitor intent on first message, locks a topic flow for the session, and routes to the appropriate content cache. Processes one turn per request with a 5-layer output guardrail pipeline, per-request cost tracking, and cache-first inference with inline fallback.
The widget sends the full conversation history with every request. The server is stateless regarding conversation content — it rebuilds context from the payload, generates one response, and forgets. Only metadata (prompt count, budget spend, active flow) is persisted.
Includes cache_setup.py for manual cache creation during initial deployment or debugging.
Conversation State Machine:
State	Name	Behavior
1	Discovery	Ask one question. Enter once, never return.
2	Acknowledge	Validate their challenge. One sentence.
3	Solution	Match problem to capability. Primary operating state.
4	Intent	Booking CTA. Only on explicit buying signals. Minimum 2 substantive exchanges required.
scheduled-jobs/monitor-budget/ — Budget Monitor
Runs every 4 hours via Cloud Scheduler. Checks spend against DegradeThreshold (80%) and MonthlyLimit (100%). Fires deduplicated alerts via webhook (Slack, Teams, PagerDuty). Sets ServiceTier to Degraded when budget is exhausted. Auto-recovers when headroom returns.
scheduled-jobs/reset-budget/ — Monthly Budget Reset
Runs on the 1st of each month. Resets spend counters and alert flags. Preserves custom budget limits — if you manually adjusted MonthlyLimitUSD mid-month, the reset only clears counters, not config.
widget/ — Chat Widget
Self-contained chat widget. Zero dependencies, no build step.
Two deployment options:
Option	File	Best For
Single-file	widget.html	WordPress, Webflow, static HTML — paste before </body>
Separate JS	widget.html + widget.js	SPAs or CSP-restricted environments
Features:
Rotating teaser bubbles (random engagement hook, shown once per session)
#chat deep linking (any <a href="#chat"> on the page opens the widget)
Turnstile bot protection with queue-based token lifecycle
Full conversation history sent with each request (stateless server)
Lead capture form at session end
WordPress-compatible (!important positioning, max z-index)
Getting Started
Prerequisites
Google Cloud Platform account with billing enabled
gcloud CLI installed and authenticated
Cloudflare account with Turnstile configured (free tier works)
Python 3.11+
Step 1 — Enable APIs and Create Firestore
export GCP_PROJECT=your-project-id
cd scripts/
chmod +x 01-enable-apis.sh 02-create-firestore.sh
./01-enable-apis.sh
./02-create-firestore.sh
Step 2 — Prepare Knowledge Base
cd sales-agent/knowledge/
# Create one .md file per topic defined in TOPIC_FILES
# Use example.md as your template
cp example.md topic_a.md
cp example.md topic_b.md
# ... edit each file with your domain-specific content
Step 3 — Configure System Prompt
cd sales-agent/
cp system_prompt.example.md system_prompt.md
# Edit system_prompt.md with your brand voice, conversation rules,
# proof points, and behavioral constraints
Step 4 — Configure Environment
cp env.yaml.example env.yaml
# Edit env.yaml with your project ID, region, consultation URL,
# Turnstile secret, and other configuration
Step 5 — Create the Context Caches
cd sales-agent/
export GCP_PROJECT=your-project-id
python cache_setup.py

This creates per-topic caches and writes the CacheMap to Firestore.
Step 6 — Deploy Cloud Functions
Sales agent:
cd sales-agent/
gcloud functions deploy sales-agent \
  --gen2 --runtime=python311 --trigger-http \
  --allow-unauthenticated \
  --env-vars-file=env.yaml \
  --entry-point=sales_agent

Lead capture:
cd capture-lead/
gcloud functions deploy capture-lead \
  --gen2 --runtime=python311 --trigger-http \
  --allow-unauthenticated \
  --entry-point=capture_lead

Cache refresh (scheduled):
cd refresh-cache/
gcloud functions deploy refresh-cache \
  --gen2 --runtime=python311 --trigger-topic=refresh-cache \
  --entry-point=create_site_cache

Budget monitor (scheduled):
cd scheduled-jobs/monitor-budget/
gcloud functions deploy monitor-budget \
  --gen2 --runtime=python311 --trigger-topic=monitor-budget \
  --entry-point=monitor_budget

Budget reset (scheduled):
cd scheduled-jobs/reset-budget/
gcloud functions deploy reset-budget \
  --gen2 --runtime=python311 --trigger-topic=reset-budget \
  --entry-point=reset_budget
Step 7 — Set Up Schedules
Cache refresh — daily at 2am UTC:
gcloud scheduler jobs create pubsub refresh-cache-daily \
  --schedule="0 2 * * *" --topic=refresh-cache \
  --message-body="{}"

Budget monitor — every 4 hours:
gcloud scheduler jobs create pubsub monitor-budget-4h \
  --schedule="0 */4 * * *" --topic=monitor-budget \
  --message-body="{}"

Budget reset — 1st of each month at midnight UTC:
gcloud scheduler jobs create pubsub reset-budget-monthly \
  --schedule="0 0 1 * *" --topic=reset-budget \
  --message-body="{}"
Step 8 — Deploy the Widget
Update AGENT_URL, LEAD_URL, and SITE_KEY in widget/widget.html
Update AGENT_NAME with your brand
Customize the TEASERS array with your engagement hooks
Paste the contents of widget.html before </body> on your site
System Prompt Architecture
The agent uses a 4-state conversation machine with explicit transition rules, forbidden transitions, and behavioral constraints. Key design choices:
Rule	Why
"Answer ONLY from provided content"	Prevents hallucinated capabilities
"Never write a URL"	Enforced in both prompt AND code via strip_urls()
"[BOOK_CTA] at most ONCE"	Prevents aggressive sales behavior
"Not in first two responses"	Forces value-first conversation
"Low-energy reply handling"	Prevents misreading politeness as purchase intent
"Proof point governance"	Never cite the same metric twice per conversation
The system prompt is loaded from an external file (system_prompt.md) at function startup. See system_prompt.example.md for the expected structure and conversation state definitions.
Cost Model
At the time of writing (Gemini Flash Lite pricing):
Component	Rate
Cached input tokens	$0.15 / 1M tokens
Non-cached input tokens	$1.50 / 1M tokens
Output tokens	$7.50 / 1M tokens
Example: 1,000 conversations/month, average 4 turns each
Scenario	Monthly Cost
With topic caches	~$2.50
Without caches	~$18.00
Savings	~85%
The default budget is $10/month with degradation at $8 and a hard ceiling at $12. Adjust via environment variables or directly in Firestore.
Customization Guide
Adding a New Topic
Create knowledge/new_topic.md with your content
Add entry to TOPIC_FILES in sales-agent/main.py, cache_setup.py, and refresh-cache/main.py
Add the topic to VALID_FLOWS and CLASSIFIER_PROMPT in sales-agent/main.py
Run python cache_setup.py to create the cache
Deploy the updated functions
Adjusting Budget Limits
Variable	Default	Where
MONTHLY_LIMIT_USD	$10.00	reset-budget/main.py or env var
DEGRADE_THRESHOLD_USD	$8.00	reset-budget/main.py or env var
HARD_CEILING_USD	$12.00	reset-budget/main.py or env var
Adjusting Cache TTL
Default: 3024000s (35 days). Set via CACHE_TTL environment variable. Aligned to 31-day billing cycles with 4-day buffer. The daily refresh schedule means caches are rebuilt every 24 hours regardless of TTL — the TTL is a safety net, not the primary refresh mechanism.
Changing the Model
Set the MODEL environment variable. The agent is tested with gemini-2.0-flash-lite but works with any Gemini model that supports content caching. Larger models improve response quality but increase cost — update the token rate constants in sales-agent/main.py accordingly.
Firestore Schema
Collection	Document	Key Fields
AgentConfig	SalesAgent	CacheMap (topic → cache resource name)
AgentBudget	{tenant}_{YYYY-MM}	CurrentSpendUSD, ServiceTier, MonthlyLimitUSD, alert flags
AgentSessions	{session_id}	PromptCount, ActiveFlow, LeadCaptured, ExpiresAt
Leads	auto-generated	email, company, session_id, source, tier_at_capture
License
MIT — see LICENSE for details.
Use it, fork it, build on it. The architecture decisions are documented in inline comments throughout the codebase. If you find them useful, a star helps others find the repo.
