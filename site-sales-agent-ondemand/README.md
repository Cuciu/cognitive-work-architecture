Site Sales Agent
Production-grade AI sales agent with built-in budget governance, behavioral guardrails, and security — open source under MIT.
A conversational AI that handles inbound sales conversations on a website with the same rigor you'd expect from a senior enterprise sales advisor.
Prompt = suggestion. Code = law.
Every behavioral rule is enforced in code, not just instructed in prompts. The model might ignore a prompt instruction. It cannot ignore a strip_premature_cta_language() function that regex-strips booking language before turn 3.
Architecture Overview
flowchart LR W[Widget<br/>browser] -->|HTTP POST + full history| A[Sales Agent<br/>Cloud Run] A --> CL[Capture Lead<br/>Cloud Function] A --> VA[Vertex AI<br/>Gemini] A --> FS[Firestore<br/>state + vectors] CS[Cloud Scheduler] --> RC[Refresh Cache<br/>daily] CS --> MB[Monitor Budget<br/>every 4h] CS --> RB[Reset Budget<br/>monthly] RC --> VA RC --> FS MB --> FS RB --> FS
Topic-Routed Vector Retrieval with Intent Classification
The agent classifies each visitor's intent on their first message, locks a topic flow for the session, and retrieves relevant knowledge chunks via topic-filtered vector search in Firestore.
This means:
System prompt lives in code, injected at call time — never cached
Knowledge base is split per topic — one vector namespace per service area
global.md is cached whole (router + voice) — never chunked
Topic KBs are chunked, embedded, and retrieved per-query via find_nearest
Adding a new topic = add one .md file + one TOPIC_FILES entry
Retrieval miss = agent still works with global fallback (graceful, not broken)
flowchart TD M[First message] --> C{classify_intent} C -->|TOPIC_A| R1[Vector search<br/>namespace: TOPIC_A] C -->|TOPIC_B| R2[Vector search<br/>namespace: TOPIC_B] C -->|TOPIC_C| R3[Vector search<br/>namespace: TOPIC_C] C -->|GENERAL| G[Global fallback<br/>greeter only] R1 --> I[Inject top-K chunks<br/>as SITE CONTENT] R2 --> I R3 --> I G --> I I --> GEN[Generate response<br/>with system prompt]
Flow is locked per session — the classifier runs once, then the topic sticks. This prevents mid-conversation topic drift and keeps retrieval focused.
Why Vector Retrieval Instead of Context Caching
Approach	Cost Model	Problem
Per-topic context caches (old)	Token-hour storage — billed 24×7 regardless of traffic	$0.50–$2/day even with zero visitors
Vector retrieval (current)	Pay-per-query — embed once, retrieve on demand	Cost tracks actual usage
The old approach cached full knowledge files as Vertex AI context caches with 35-day TTLs. This created a flat daily cost that scaled with corpus size, not query volume. For a low-traffic sales agent, this is the wrong cost curve.
The current architecture:
Embeds once — content-addressed chunks are only re-embedded when content changes
Retrieves per-query — cost is proportional to actual visitor conversations
Prunes automatically — stale chunks from edited/deleted content are cleaned up
Budget Governance Lifecycle
Track (per request) → Monitor (every 4h) → Enforce (auto-degrade) → Reset (monthly) → new month → Track …
Spend Range	Tier	Behavior
$0 — $8	🟢 Full	All model calls active
$8 — $10	⚠️ Warning	Alert fired (once), ops notified
$10 — $12	🟠 Degraded	Model calls stop, fallback CTA served
> $12	🛑 Hard Ceiling	Absolute stop, no exceptions
Three independent components enforce this:
sales-agent/main.py — tracks cost per request, checks budget before every model call
monitor-budget/main.py — runs every 4h, fires alerts at 80%/100% thresholds
reset-budget/main.py — runs monthly, initializes fresh budget document
Budget state lives in Firestore (AgentBudget collection), not in application memory. Any component can read it. The agent degrades gracefully — visitors see a booking CTA instead of an error.
5-Layer Output Guardrail Pipeline
Every model response passes through five processing layers before reaching the visitor:
smart_truncate() — Cap at 700 chars, cut at sentence boundary
strip_premature_cta_language() — Regex-remove booking language before turn 3
Extract [BOOK_CTA] marker — Signal retained for CTA logic, marker removed from text
strip_urls() — Remove any URLs the model leaked (model is told not to; code enforces it)
CTA gate check — Booking button only renders if turn_count >= MIN_TURNS_BEFORE_CTA
Why code over prompts? The model is instructed not to push meetings early. But LLMs are probabilistic — instructions leak. The guardrail pipeline guarantees the behavior. The prompt is a suggestion. The code is law.
Cost Optimization
Path	Rate	When
Cached input	$0.15 / 1M tokens	Router cache active (global.md, 35-day TTL)
Non-cached input	$1.50 / 1M tokens	Retrieved KB chunks (standard input pricing)
Output	$7.50 / 1M tokens	Always
Vector Retrieval Cost Model
Embedding cost: One-time per chunk. Content-addressed IDs mean unchanged chunks are never re-embedded
Retrieval cost: Per-query. Only the top-K chunks (default: 4) are retrieved and injected
Storage cost: Firestore document storage — negligible compared to context cache token-hour billing
Cache miss: Agent still works on global fallback, just without topic-specific depth
Retrieval miss = agent still works, just costs more. Graceful degradation, not failure.
Design Principles
🟠 Stateless Server — Client-Side Conversation Memory
The server stores zero conversation content. The widget sends the full conversation history with every request. The server processes one turn and responds — it never persists what was said.
How it works:
Widget maintains history[] array in browser memory
Every API call sends the full history (capped at MAX_HISTORY = 10 turns)
Server rebuilds context from the payload, generates one response, forgets
If the visitor refreshes the page, conversation resets (by design)
Why: No PII retention on the server. No conversation database. No GDPR data subject requests against chat logs. The server is a pure function: (history, message) → response.
🟢 Session-Scoped Flow Lock
The intent classifier runs once — on the visitor's first message. The result is stored in Firestore as ActiveFlow on the session document. All subsequent turns use the locked topic for retrieval.
Why: Prevents mid-conversation topic drift. If a visitor asks about cloud migration, then casually mentions costs, the agent stays in the cloud migration knowledge — it doesn't jump to FinOps. This keeps retrieval focused and responses coherent.
🔵 Content-Addressed Incremental Indexing
Knowledge chunks are identified by a SHA-256 hash of {topic}::{content}. This means:
Identical content = same ID — re-running the indexer skips unchanged chunks
Changed content = new ID — edited paragraphs get new embeddings automatically
Deleted content = pruned — stale chunks not in the current file are removed
No full re-index — adding a new topic only embeds the new file's chunks
chunk_id = sha256(f"{topic}::{content}")[:32]
# e.g., "cloud_a3b7c9d1e5f2..."
🔴 Anti-Bot Protection
Cloudflare Turnstile runs in interaction-only mode:
Invisible to real visitors — no CAPTCHA puzzles
Blocks automated scrapers from burning your Gemini budget
Token verified server-side before any model call
Widget queues the question and sends it when the token arrives — no UX delay
Conversation State Machine
The system prompt implements a 4-state conversation model:
stateDiagram-v2 [*] --> DISCOVERY: Greeting / vague [*] --> SOLUTION: Specific question DISCOVERY --> ACKNOWLEDGE: Challenge stated ACKNOWLEDGE --> SOLUTION: Problem clear SOLUTION --> SOLUTION: Follow-up / "tell me more" SOLUTION --> INTENT: Buying signal detected INTENT --> [*]: CTA shown (once)
State	Trigger	Behavior
DISCOVERY	Greeting or vague opener	Ask ONE question. Enter once, never return.
ACKNOWLEDGE	Challenge stated	Validate the pain. One sentence.
SOLUTION	Problem clear	Match to capability. Cite proof point. Go deeper on follow-ups.
INTENT	Buying signal (pricing, timelines, "how do we start")	Show booking CTA once. Never before turn 3.
Low-energy reply handling: If the visitor says "ok", "cool", "thanks" — this is NOT buying intent. The agent offers a new insight instead of pushing a meeting.
Repository Structure
site-sales-agent/
│
├── .gitignore
├── LICENSE                     MIT
├── README.md                   This file
│
├── capture-lead/
│   ├── main.py                 Lead capture endpoint
│   └── requirements.txt
│
├── refresh-cache/
│   ├── main.py                 Scheduled cache rebuild
│   ├── requirements.txt
│   └── knowledge/
│       ├── README.md           KB file format docs
│       └── sample-knowledge.md KB template
│
├── sales-agent/
│   ├── main.py                 Core agent logic
│   ├── cache_setup.py          Vector index builder (CLI)
│   ├── env.yaml.example        Env var template
│   ├── requirements.txt
│   └── knowledge/
│       ├── README.md           KB file format docs
│       └── sample-practice.md  KB template
│
├── scheduled-jobs/
│   ├── monitor-budget/
│   │   ├── main.py             Budget monitor (4h cycle)
│   │   └── requirements.txt
│   └── reset-budget/
│       ├── main.py             Monthly budget reset
│       └── requirements.txt
│
├── scripts/
│   ├── 00-config.sh                  Shared env config
│   ├── 01-enable-apis.sh             Enable GCP APIs
│   ├── 02-create-firestore.sh        Create Firestore DB + vector index
│   ├── 03-setup-turnstile-secret.sh  Turnstile in Secret Manager
│   ├── 04-index-kb.sh                Build vector index from KB files
│   ├── 05-deploy-refresh-cache.sh    Deploy cache refresh function
│   ├── 06-deploy-sales-agent.sh      Deploy agent to Cloud Run
│   ├── 09-cache-cost.sh              Inspect context cache costs
│   ├── 10-delete-old-caches.sh       Delete legacy per-topic caches
│   └── 12-test-agent.sh              Smoke test router classification
│
└── widget/
    ├── widget.html             Chat widget (single-file)
    └── widget.js               Standalone JS (CSP option)
What's Not in This Repo
The following files are required at runtime but excluded from source control:
File	Purpose	How to Create
knowledge/*.md	Per-topic knowledge base content	Populate using knowledge/sample-practice.md as template
env.yaml	Environment variables (project ID, URLs)	Copy env.yaml.example and fill in your values
Turnstile secret	Bot protection key	Store in Secret Manager via scripts/03-setup-turnstile-secret.sh
Quick Start
Prerequisites
Google Cloud project with billing enabled
gcloud CLI authenticated
Cloudflare Turnstile site key + secret (free tier)
Deploy
# 1. Configure
source scripts/00-config.sh          # edit GCP_PROJECT first
# 2. Infrastructure
bash scripts/01-enable-apis.sh
bash scripts/02-create-firestore.sh
# 3. Secrets
export TURNSTILE_VALUE='your-turnstile-secret'
bash scripts/03-setup-turnstile-secret.sh
# 4. Knowledge base
# Add your .md files to sales-agent/knowledge/
# Update TOPIC_FILES in cache_setup.py to match
bash scripts/04-index-kb.sh
# 5. Environment
cp sales-agent/env.yaml.example sales-agent/env.yaml
# Edit env.yaml with your project ID, URLs, etc.
# 6. Deploy
bash scripts/05-deploy-refresh-cache.sh
bash scripts/06-deploy-sales-agent.sh
# 7. Test
bash scripts/12-test-agent.sh
Embed on Your Site
Add the Turnstile script and the widget before </body>:
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<div id="sa-ts"></div>
<!-- Include widget.html content or link widget.js -->

Update AGENT_URL, LEAD_URL, and SITE_KEY in widget.js to match your deployed endpoints.
Cost Model — Real Numbers
For a low-traffic sales agent (~50 conversations/month):
Component	Monthly Cost
Gemini API (flash-lite)	~$0.30–$1.00
Firestore (sessions + vectors)	~$0.00 (free tier)
Cloud Run	~$0.00 (free tier)
Cloud Functions	~$0.00 (free tier)
Embeddings (incremental)	~$0.01
Total	< $2/month
The budget governance system caps this at your configured limit ($10/month default) and degrades gracefully — never a surprise bill.
License
MIT — use it, fork it, ship it.
Contributing
Issues and PRs welcome. If you deploy this for your own business, consider sharing:
Prompt techniques that improved conversion
Knowledge base structures that worked well
Budget configurations for different traffic levels
Cloudflare Turnstile
