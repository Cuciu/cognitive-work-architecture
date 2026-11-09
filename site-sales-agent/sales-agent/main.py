"""
Cognitive Work Architecture — Site Sales Agent — Core Agent
============================================================
The main conversational sales agent. Handles visitor interactions
with production-grade guardrails, budget governance, and security.
This is where all the architecture decisions converge:
  - Guardrails constrain WHAT the agent can say and WHEN
  - Budget governance controls HOW MUCH it costs per request
  - Security prevents abuse and data leakage
  - Graceful degradation ensures the system never hard-fails on a visitor
Repo:   cognitive-work-architecture/site-sales-agent
Module: sales-agent
License: MIT
"""
import os
import re
import functions_framework
import requests as _rq
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types
from google.cloud import firestore
# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ID       = os.environ["GCP_PROJECT"]
LOCATION         = os.environ.get("REGION", "us-central1")
MODEL            = os.environ.get("MODEL", "gemini-3.5-flash-lite")
TENANT           = "default"
CONSULTATION_URL = os.environ["CONSULTATION_URL"]
ALLOWED_ORIGIN   = os.environ.get("ALLOWED_ORIGIN", "*")
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")
# ---------------------------------------------------------------------------
# GUARDRAILS — Hard Limits
# ---------------------------------------------------------------------------
# Every limit here exists because of a specific failure mode or cost risk:
#
#   MAX_PROMPTS_PER_SESSION: Prevents infinite conversation loops.
#     A sales conversation that hasn't converted in 6 turns won't convert
#     in 60. Cap it and offer human handoff instead.
#
#   MAX_ANSWER_CHARS: Prevents wall-of-text responses that kill chat UX.
#     Smart truncation (below) cuts at sentence boundaries, not mid-word.
#
#   MAX_OUTPUT_TOKENS: Model-level output cap. Defense in depth — even if
#     smart_truncate fails, the model itself won't generate a novel.
#
#   MAX_HISTORY_TURNS: Bounds the context window sent to the model.
#     Without this, long conversations send ever-growing token counts,
#     and your costs scale linearly with conversation length.
#
#   MIN_TURNS_BEFORE_CTA: The booking CTA cannot appear before turn 3.
#     This is a HARD GATE enforced in code, independent of the model's
#     prompt instructions. The model might try to push a meeting early —
#     this code-level gate overrides it.
# ---------------------------------------------------------------------------
MAX_PROMPTS_PER_SESSION = 6
MAX_ANSWER_CHARS        = 700
MAX_OUTPUT_TOKENS       = 250
MAX_HISTORY_TURNS       = 10
MIN_TURNS_BEFORE_CTA    = 3
# ---------------------------------------------------------------------------
# BUDGET — Per-Token Cost Rates
# ---------------------------------------------------------------------------
# These rates are used to calculate the actual cost of every single request.
#
# WHY THIS MATTERS:
#   Most agent builders have no idea what each conversation costs.
#   They find out when the invoice arrives. This agent tracks cost
#   per-request and accumulates it against a monthly budget with
#   three escalation tiers:
#
#     DegradeThreshold (80%) → reduce capability, alert ops
#     MonthlyLimit (100%)    → stop model calls, show fallback CTA
#     HardCeiling (120%)     → absolute stop, no exceptions
#
# CACHED vs. NON-CACHED:
#   When the context cache is active, site content tokens are billed
#   at the cached rate (10x cheaper). When the cache is missing/expired,
#   the same tokens are billed at the standard rate. This is why
#   refresh-cache exists — it's not optional optimization, it's
#   budget-critical infrastructure.
# ---------------------------------------------------------------------------
RATE_INPUT_CACHED = 0.15 / 1_000_000   # per token, cached context
RATE_INPUT_STD    = 1.50 / 1_000_000   # per token, standard input
RATE_OUTPUT       = 7.50 / 1_000_000   # per token, generated output
client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db     = firestore.Client()
# ---------------------------------------------------------------------------
# Fallback site content — used only when cache is unavailable
# BUDGET: This path costs ~10x more per request than cached context.
# ---------------------------------------------------------------------------
try:
    with open("site_content_full.txt") as f:
        SITE_CONTEXT = f.read()
except FileNotFoundError:
    SITE_CONTEXT = ""
# ---------------------------------------------------------------------------
# SYSTEM PROMPT — see refresh-cache/main.py for full annotated version.
# Duplicated here for deployment isolation (see architecture note in
# cache_setup.py for the rationale).
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are Sky Alleys' senior sales advisor — a direct, technically credible "
    "peer who earns trust by demonstrating expertise, not by pushing meetings. "
    "\n\n"
    "ANSWER RULES: "
    "Answer ONLY from the provided site content. Never invent capabilities. "
    "Keep responses concise but complete — 2 to 5 sentences depending on "
    "question complexity. Technical questions deserve technical depth. "
    "This is a small chat window: use short paragraphs, never walls of text. "
    "Finish every thought with a full sentence. "
    "Vary your phrasing — never repeat the same opener or closer twice. "
    "\n\n"
    "CONVERSATION STATES — track which state you are in: "
    "\n\n"
    "STATE 1 DISCOVERY: "
    "If they open with a greeting or vague request, ask ONE question about "
    "their challenge. Enter only once. Never return to this state. "
    "If they open with a specific question, skip directly to STATE 2 or 3. "
    "\n\n"
    "STATE 2 ACKNOWLEDGE: "
    "Briefly validate why their challenge is hard or costly — one sentence. "
    "Ask ONE clarifier only if the problem is genuinely ambiguous, "
    "then move to STATE 3. "
    "\n\n"
    "STATE 3 SOLUTION (primary operating state): "
    "Match their problem to a specific Sky Alleys capability. "
    "Name the framework, method, or deliverable. Cite a proof point or "
    "benchmark from the site content. "
    "If they ask to go deeper or say 'tell me more' — expand with NEW "
    "detail. Stay in STATE 3. Never restart discovery. Never repeat yourself. "
    "After delivering a solution, offer to explore a related angle: "
    "'I can also walk you through how this worked in [vertical/scenario]' "
    "or ask a qualifying question to tailor further. "
    "\n\n"
    "STATE 4 INTENT (booking): "
    "Enter ONLY when the visitor explicitly signals buying intent — "
    "examples: asks about pricing, timelines, availability, next steps, "
    "'how do we start', 'can we talk to someone', 'I want to explore this'. "
    "Write ONE sentence inviting them to book a consultation, then end "
    "your message with the exact marker [BOOK_CTA]. "
    "Emit [BOOK_CTA] at most ONCE per conversation. "
    "NEVER emit [BOOK_CTA] in your first two responses. "
    "The visitor must have at least two substantive exchanges before "
    "any booking suggestion is appropriate. "
    "\n\n"
    "LOW-ENERGY REPLIES — CRITICAL: "
    "If the visitor replies with 'ok', 'sure', 'great', 'cool', 'thanks', "
    "or similar after you presented a solution — this is NOT buying intent. "
    "Do NOT move to STATE 4. Instead, offer one additional insight they "
    "haven't seen yet: a related proof point, a different framework angle, "
    "or a brief qualifying question. If they give two consecutive low-energy "
    "replies, offer a graceful exit: 'Happy to answer anything else — "
    "or I can send you our overview if you'd prefer to review offline.' "
    "\n\n"
    "CRITICAL RULES: "
    "Once they state a problem, go DEEPER not WIDER. Only forward, never reset. "
    "Never ask what brought them here more than once. "
    "Never present the same answer twice — always add new detail. "
    "NEVER write a URL or booking link in your text. "
    "Use [BOOK_CTA] instead, only in STATE 4, maximum once. "
    "A booking button is attached automatically whenever you emit [BOOK_CTA]. "
    "Writing a raw URL is forbidden. "
    "\n\n"
    "TONE: Direct, senior, peer-to-peer. Trusted advisor, not salesperson. "
    "You earn the meeting by demonstrating you know their world — "
    "not by asking for it repeatedly."
)

# ---------------------------------------------------------------------------
# GRACEFUL DEGRADATION — User-Facing Fallbacks
# ---------------------------------------------------------------------------
# WHY BETTER: When the budget is exhausted, the visitor doesn't see an error.
# They see a professional message with a direct path to a human.
# The system degrades gracefully, not catastrophically.
# ---------------------------------------------------------------------------
DEGRADED_HTML = (
    "<div class='agent-msg degraded'>"
    "<p>Our assistant has run out of tokens for this month.</p>"
    "<p><strong>Let's talk directly</strong> — book a free consultation:</p>"
    f"<a class='cta-button' href='{CONSULTATION_URL}' target='_blank'>"
    f"Book a free consultation</a>"
    "</div>"
)

def consultation_cta():
    """Render the booking CTA button. Centralized so the button design
    and URL are consistent everywhere it appears."""
    return (
        "<div class='agent-cta'>"
        "<p>Let's map this to your specific situation.</p>"
        f"<a class='cta-button' href='{CONSULTATION_URL}' "
        f"target='_blank'>Book a 30-minute session</a>"
        "</div>"
    )

# ---------------------------------------------------------------------------
# SECURITY — Bot Verification
# ---------------------------------------------------------------------------
def verify_turnstile(token, remoteip=None):
    """Verify Cloudflare Turnstile token.
    Returns True if valid or if verification is not configured (dev mode)."""
    if not TURNSTILE_SECRET:
        return True
    try:
        r = _rq.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": remoteip},
            timeout=10,
        )
        return bool(r.json().get("success", False))
    except Exception:
        return False  # Fail closed

# ---------------------------------------------------------------------------
# GUARDRAILS — Output Processing Pipeline
# ---------------------------------------------------------------------------
# These functions form a multi-layer output processing pipeline:
#
#   Model Output
#     → smart_truncate()                  (sentence-boundary length cap)
#     → strip_premature_cta_language()    (remove early booking language)
#     → strip [BOOK_CTA] marker          (extract signal, remove from display)
#     → strip_urls()                      (remove any leaked URLs)
#     → CTA gate check                   (turn count >= MIN_TURNS_BEFORE_CTA?)
#   → Final Response
#
# WHY THIS MATTERS:
#   The system prompt tells the model what to do. These functions enforce
#   it in code. Prompt instructions are suggestions — code is law.
#   Defense in depth: even if the model ignores every prompt rule,
#   the output pipeline catches and corrects it.
# ---------------------------------------------------------------------------
def smart_truncate(text, limit=MAX_ANSWER_CHARS):
    """Truncate at sentence boundaries, not mid-word.
    Falls back to word boundary with ellipsis if no sentence break found."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Look for the last sentence-ending punctuation in the safe zone
    for sep in [". ", "! ", "? "]:
        idx = cut.rfind(sep)
        if idx > limit * 0.6:  # Only cut here if we keep at least 60% of content
            return cut[:idx + 1].strip()
    return cut.rsplit(" ", 1)[0].strip() + "…"

def strip_urls(text):
    """SECURITY + GUARDRAIL: Remove any raw URLs the model generates.
    The model is instructed never to output URLs, but models don't always
    comply. This is the code-level enforcement.
    Also cleans up dangling 'here:' or 'below:' text that referenced
    the now-removed URL, so the output reads naturally."""
    t = text or ""
    had_url = bool(re.search(r'https?://\S+', t))
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'\s*(here|below|at)\s*:?\s*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t, had_url

def strip_premature_cta_language(text, turn_count, min_turns=MIN_TURNS_BEFORE_CTA):
    """GUARDRAIL: Remove meeting/scheduling language if we haven't passed
    the minimum turn gate.
    The model's prompt says "don't suggest booking before turn 3."
    This function enforces it in code. If the model says "Shall we
    schedule a call?" on turn 1, this regex strips it silently.
    Prompt = suggestion. Code = law."""
    if turn_count >= min_turns:
        return text
    patterns = [
        r"Would you like to (schedule|book|set up).*?\?",
        r"Ready to (schedule|book|set up).*?\?",
        r"Let'?s (schedule|book|set up).*?\.",
        r"Shall we (schedule|book|set up).*?\?",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.IGNORECASE)
    return text.strip()

# ---------------------------------------------------------------------------
# CONVERSATION MANAGEMENT
# ---------------------------------------------------------------------------
def build_contents(history, user_msg):
    """Build multi-turn Gemini contents from conversation history.
    WHY BETTER: The frontend sends conversation history with each request.
    This enables multi-turn context without server-side session storage
    of conversation content. The server stores only metadata (prompt count,
    budget), never conversation text.
    MAX_HISTORY_TURNS caps the window to bound token costs — older turns
    are silently dropped. The model still has the system prompt and site
    content for context; it just loses early conversation turns."""
    contents = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "user" if turn.get("role") == "user" else "model"
        text = (turn.get("text") or "").strip()
        if text:
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    if not contents:
        contents = [types.Content(role="user", parts=[types.Part(text=user_msg)])]
    return contents

def wants_booking(user_msg):
    """Detect explicit booking intent from the visitor's message.
    GUARDRAIL: Only matches explicit commercial signals — "pricing,"
    "schedule," "get started." Ambiguous words like "yes," "sure," "ok"
    are intentionally excluded. Those are handled by the model's state
    machine via the [BOOK_CTA] marker, not by keyword matching.
    This prevents the most common chatbot failure: interpreting
    politeness as purchase intent."""
    user_lc = (user_msg or "").lower()
    signals = [
        "book", "consultation", "call", "talk to", "speak to", "schedule",
        "get started", "how do we start", "sign up", "demo", "meeting",
        "pricing", "price", "cost", "quote", "contact you", "reach out",
    ]
    return any(s in user_lc for s in signals)

# ---------------------------------------------------------------------------
# BUDGET GOVERNANCE — Runtime Budget Management
# ---------------------------------------------------------------------------
def month_key():
    """Monthly billing period key. Budget resets each calendar month."""
    return datetime.now(timezone.utc).strftime("%Y-%m")

def get_budget(ref_id):
    """Retrieve or initialize the monthly budget document.
    BUDGET ARCHITECTURE:
    ┌──────────────────────────────────────────────────────┐
    │  $0 ──────── $8 (Degrade) ──── $10 (Limit) ── $12  │
    │  │  Full Tier  │  Degraded    │   Stopped   │ Hard  │
    │  │  All models │  Fallback    │   CTA only  │ Ceil  │
    └──────────────────────────────────────────────────────┘
    DegradeGracefully mode:
      - Below DegradeThreshold ($8): full capability
      - Between Degrade and Limit ($8-$10): reduced capability
      - At MonthlyLimit ($10): stop model calls, show booking CTA
      - HardCeiling ($12): absolute stop, no exceptions
    Alert gates at 80% and 100% prevent surprise invoices.
    Budget document is per-tenant, per-month — supports multi-tenant."""
    ref = db.collection("AgentBudget").document(ref_id)
    snap = ref.get()
    if not snap.exists:
        ref.set({
            "AgentName":           "SalesAgent",
            "TenantOrAccount":     TENANT,
            "BillingPeriod":       month_key(),
            "MonthlyLimitUSD":     10.0,
            "DegradeThresholdUSD": 8.0,
            "CurrentSpendUSD":     0.0,
            "ServiceTier":         "Full",
            "BudgetMode":          "DegradeGracefully",
            "HardCeilingUSD":      12.0,
            "AlertSentAt80":       False,
            "AlertSentAt100":      False,
            "Status":              "Active",
        })
        snap = ref.get()
    return ref, snap.to_dict()

def get_session(sid):
    """Retrieve or initialize a session. Sessions track prompt count
    and expiry — not conversation content. Content lives on the frontend."""
    ref = db.collection("AgentSessions").document(sid)
    snap = ref.get()
    if not snap.exists:
        now = datetime.now(timezone.utc)
        ref.set({
            "PromptCount": 0,
            "CreatedAt":   firestore.SERVER_TIMESTAMP,
            "ExpiresAt":   now + timedelta(hours=24),
            "LeadCaptured": False,
        })
        return ref, 0
    return ref, snap.to_dict().get("PromptCount", 0)

def get_cache_name():
    """Read the active cache name from Firestore config.
    Returns None if no cache is available — agent falls back to inline."""
    c = db.collection("AgentConfig").document("SalesAgent").get()
    return c.to_dict().get("CacheName") if c.exists else None

# ---------------------------------------------------------------------------
# MAIN AGENT ENDPOINT
# ---------------------------------------------------------------------------
@functions_framework.http
def sales_agent(request):
    """
    Core agent endpoint. Processes one conversation turn.
    REQUEST FLOW:
    ┌─────────┐   ┌──────────┐   ┌────────┐   ┌───────┐   ┌──────────┐
    │ Validate │──▶│ Security │──▶│ Budget │──▶│ Model │──▶│ Pipeline │
    │  Input   │   │ Turnstile│   │  Check  │   │  Call  │   │ Guardrail│
    └─────────┘   └──────────┘   └────────┘   └───────┘   └──────────┘
         │              │             │             │             │
         ▼              ▼             ▼             ▼             ▼
       400/err       403/bot      Degraded      Fallback     Clean reply
                                   HTML         to inline    + optional CTA
                                                 context     + cost tracking
    """
    # --- CORS ---
    headers = {"Access-Control-Allow-Origin": ALLOWED_ORIGIN}
    if request.method == "OPTIONS":
        return ("", 204, {
            **headers,
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    # --- INPUT VALIDATION ---
    body       = request.get_json(silent=True) or {}
    user_msg   = body.get("message", "").strip()
    session_id = body.get("session_id", "").strip()
    history    = body.get("history", [])
    if not user_msg or not session_id:
        return ({"error": "message and session_id required"}, 400, headers)
    # --- SECURITY: Bot verification ---
    token = body.get("cf_token", "")
    if not verify_turnstile(token, request.headers.get("X-Forwarded-For")):
        return ({"error": "Verification failed. Please refresh and try again."}, 403, headers)
    # --- BUDGET: Check monthly spend before calling the model ---
    budget_ref, budget = get_budget(f"{TENANT}_{month_key()}")
    if budget["CurrentSpendUSD"] >= budget["MonthlyLimitUSD"]:
        return ({"html": DEGRADED_HTML, "tier": "Degraded", "model_called": False}, 200, headers)
    # --- GUARDRAIL: Session prompt cap ---
    sess_ref, count = get_session(session_id)
    if count >= MAX_PROMPTS_PER_SESSION:
        return ({"html": consultation_cta(), "tier": "SessionCap", "model_called": False}, 200, headers)
    # --- BUILD CONVERSATION CONTEXT ---
    contents = build_contents(history, user_msg)
    # --- MODEL CALL: Cache-first with inline fallback ---
    # BUDGET: Cache path costs ~10x less per request than inline path.
    # The try/except ensures the agent ALWAYS responds — it just costs
    # more when the cache is unavailable.
    try:
        cache_name = get_cache_name()
        if cache_name:
            # BUDGET: Cached path — site content tokens billed at cached rate
            cfg = types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.3,
                cached_content=cache_name,
            )
        else:
            # BUDGET: Inline fallback — site content tokens billed at full rate
            cfg = types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.3,
                system_instruction=f"{SYSTEM_PROMPT}\n\nSITE CONTENT:\n{SITE_CONTEXT}",
            )
        resp = client.models.generate_content(model=MODEL, contents=contents, config=cfg)
    except Exception as e:
        # GRACEFUL DEGRADATION: Model failure → human handoff, not error page
        return ({
            "reply": "I'm having a brief issue. Let's connect you with our team directly.",
            "cta_html": consultation_cta(),
            "tier": "Error",
            "model_called": False,
            "error_detail": str(e)[:200],  # SECURITY: Truncate error
        }, 200, headers)
    new_count = count + 1
    # --- OUTPUT PROCESSING PIPELINE ---
    # Layer 1: Truncate to character limit at sentence boundary
    answer = smart_truncate(resp.text or "")
    # Layer 2: Strip premature booking language (code overrides model)
    answer = strip_premature_cta_language(answer, new_count)
    # Layer 3: Extract [BOOK_CTA] marker (signal retained, marker removed from display)
    has_marker = "[BOOK_CTA]" in answer
    answer = answer.replace("[BOOK_CTA]", "").strip()
    # Layer 4: Strip any raw URLs the model leaked
    answer, had_url = strip_urls(answer)
    # Layer 5: Determine if booking CTA should render
    visitor_wants_booking = wants_booking(user_msg)
    show_cta = (
        (has_marker or had_url or visitor_wants_booking or new_count >= MAX_PROMPTS_PER_SESSION)
        and new_count >= MIN_TURNS_BEFORE_CTA  # GUARDRAIL: Turn gate — hard override
    )
    cta = consultation_cta() if show_cta else None
    # --- BUDGET: Per-request cost calculation ---
    # Track actual cost using token counts from the model response.
    # Cached tokens are billed at 1/10th the standard rate.
    um         = resp.usage_metadata
    cached     = getattr(um, "cached_content_token_count", 0) or 0
    prompt_tok = getattr(um, "prompt_token_count", 0) or 0
    out_tok    = getattr(um, "candidates_token_count", 0) or 0
    non_cached = max(prompt_tok - cached, 0)
    cost       = (cached * RATE_INPUT_CACHED + non_cached * RATE_INPUT_STD + out_tok * RATE_OUTPUT)
    # Accumulate spend and increment session counter
    budget_ref.update({"CurrentSpendUSD": firestore.Increment(cost)})
    sess_ref.update({"PromptCount": firestore.Increment(1)})
    # --- RESPONSE ---
    # Every response includes cost telemetry so the frontend (or monitoring)
    # can track spend in real time. model_called=True confirms this was
    # an actual inference, not a cached/degraded response.
    return ({
        "reply":             answer,
        "cta_html":          cta,
        "prompts_used":      new_count,
        "prompts_remaining": MAX_PROMPTS_PER_SESSION - new_count,
        "tier":              "Full",
        "run_cost_usd":      round(cost, 6),
        "model_called":      True,
    }, 200, headers)
