"""
Cloud Function: sales-agent
Core conversational sales agent with:
  - Vertex AI content caching (per-topic KB)
  - Intent classification and flow locking
  - Budget protection with graceful degradation
  - Session management with prompt caps
  - CTA governance (minimum turns before booking suggestion)
  - Cloudflare Turnstile bot protection
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
PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION = os.environ.get("REGION", "us-central1")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash-lite")
TENANT = os.environ.get("TENANT", "default")
CONSULTATION_URL = os.environ["CONSULTATION_URL"]
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")

# ---------------------------------------------------------------------------
# Agent guardrails
# ---------------------------------------------------------------------------
MAX_PROMPTS_PER_SESSION = int(os.environ.get("MAX_PROMPTS", "6"))
MAX_ANSWER_CHARS   = 700
MAX_OUTPUT_TOKENS  = 250
MAX_HISTORY_TURNS  = 10
MIN_TURNS_BEFORE_CTA = 3

# ---------------------------------------------------------------------------
# Vertex AI pricing (per-token) — used for real-time cost tracking
# Update these when model pricing changes
# ---------------------------------------------------------------------------
RATE_INPUT_CACHED  = 0.15 / 1_000_000
RATE_INPUT_STD     = 1.50 / 1_000_000
RATE_OUTPUT        = 7.50 / 1_000_000

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db = firestore.Client()

# ---------------------------------------------------------------------------
# Knowledge base fallback
# Used when a topic cache is unavailable (greeter/GENERAL or cache miss)
# ---------------------------------------------------------------------------
KB_DIR = os.environ.get("KB_DIR", "knowledge")


def _load_global_fallback():
    """Load global.md as inline fallback when no topic cache is available."""
    for path in (os.path.join(KB_DIR, "global.md"), "global.md"):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            continue
    return ""


GLOBAL_FALLBACK = _load_global_fallback()

# ---------------------------------------------------------------------------
# System prompt
# Loaded from file to keep proprietary prompt logic outside source control.
# See system_prompt.example.md for the expected structure.
# ---------------------------------------------------------------------------
_PROMPT_PATH = os.environ.get("SYSTEM_PROMPT_PATH", "system_prompt.md")


def _load_system_prompt():
    """Load system prompt from file. Falls back to minimal default."""
    try:
        with open(_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "You are a senior sales advisor. Answer only from the provided "
            "site content. Keep responses concise. Never invent capabilities."
        )


SYSTEM_PROMPT = _load_system_prompt()

# ---------------------------------------------------------------------------
# Intent classification
# Runs once per session, then locks the flow.
# ---------------------------------------------------------------------------
VALID_FLOWS = {"TOPIC_A", "TOPIC_B", "TOPIC_C", "TOPIC_D",
               "TOPIC_E", "TOPIC_F", "TOPIC_G"}

CLASSIFIER_PROMPT = (
    "Classify the user's intent into exactly ONE category. "
    "Respond with ONLY the category name, nothing else.\n"
    "- TOPIC_A: [describe topic A scope]\n"
    "- TOPIC_B: [describe topic B scope]\n"
    "- TOPIC_C: [describe topic C scope]\n"
    "- TOPIC_D: [describe topic D scope]\n"
    "- TOPIC_E: [describe topic E scope]\n"
    "- TOPIC_F: [describe topic F scope]\n"
    "- TOPIC_G: [describe topic G scope]\n"
    "- GENERAL: unclear, greeting, or broad question"
)


def classify_intent(user_msg):
    """Return one of VALID_FLOWS or 'GENERAL'. Fails safe to GENERAL."""
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=user_msg,
            config=types.GenerateContentConfig(
                max_output_tokens=5, temperature=0.0,
                system_instruction=CLASSIFIER_PROMPT),
        )
        topic = (resp.text or "").strip().upper()
        return topic if topic in VALID_FLOWS else "GENERAL"
    except Exception:
        return "GENERAL"


def get_cache_map():
    """Return the per-topic CacheMap from Firestore."""
    c = db.collection("AgentConfig").document("SalesAgent").get()
    if not c.exists:
        return {}
    return c.to_dict().get("CacheMap", {}) or {}


# ---------------------------------------------------------------------------
# Degraded mode HTML (budget exhausted)
# ---------------------------------------------------------------------------
DEGRADED_HTML = (
    "<div class='agent-msg degraded'>"
    "<p>Our assistant has reached its limit for this period.</p>"
    "<p><strong>Let's talk directly</strong>:</p>"
    f"<a class='cta-button' href='{CONSULTATION_URL}' "
    f"target='_blank'>Book a free consultation</a>"
    "</div>"
)


def consultation_cta():
    """Generate the booking CTA HTML."""
    return (
        "<div class='agent-cta'>"
        "<p>Let's map this to your specific situation.</p>"
        f"<a class='cta-button' href='{CONSULTATION_URL}' "
        f"target='_blank'>Book a 30-minute session</a>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Security: Cloudflare Turnstile verification
# ---------------------------------------------------------------------------
def verify_turnstile(token, remoteip=None):
    """Verify Cloudflare Turnstile token. Returns True if disabled or valid."""
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
        return False


# ---------------------------------------------------------------------------
# Response processing
# ---------------------------------------------------------------------------
def smart_truncate(text, limit=MAX_ANSWER_CHARS):
    """Truncate at sentence boundary to stay within character limit."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in [". ", "! ", "? "]:
        idx = cut.rfind(sep)
        if idx > limit * 0.6:
            return cut[:idx + 1].strip()
    return cut.rsplit(" ", 1)[0].strip() + "…"


def strip_urls(text):
    """Remove any URLs the model may have hallucinated."""
    t = text or ""
    had_url = bool(re.search(r'https?://\S+', t))
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'\s*(here|below|at)\s*:?\s*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t, had_url


def strip_premature_cta_language(text, turn_count, min_turns=MIN_TURNS_BEFORE_CTA):
    """Remove booking language if the conversation hasn't earned it yet."""
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


def build_contents(history, user_msg):
    """Build Vertex AI contents array from conversation history."""
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
    """Detect explicit booking intent from user message."""
    user_lc = (user_msg or "").lower()
    signals = [
        "book", "consultation", "call", "talk to", "speak to", "schedule",
        "get started", "how do we start", "sign up", "demo", "meeting",
        "pricing", "price", "cost", "quote", "contact you", "reach out",
    ]
    return any(s in user_lc for s in signals)


# ---------------------------------------------------------------------------
# Budget management
# ---------------------------------------------------------------------------
def month_key():
    """Current billing period key (YYYY-MM)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_budget(ref_id):
    """Get or initialize monthly budget document."""
    ref = db.collection("AgentBudget").document(ref_id)
    snap = ref.get()
    if not snap.exists:
        ref.set({
            "AgentName": "SalesAgent",
            "TenantOrAccount": TENANT,
            "BillingPeriod": month_key(),
            "MonthlyLimitUSD": 10.0,
            "DegradeThresholdUSD": 8.0,
            "CurrentSpendUSD": 0.0,
            "ServiceTier": "Full",
            "BudgetMode": "DegradeGracefully",
            "HardCeilingUSD": 12.0,
            "AlertSentAt80": False,
            "AlertSentAt100": False,
            "Status": "Active",
        })
        snap = ref.get()
    return ref, snap.to_dict()


def get_session(sid):
    """Get or initialize session document. Returns (ref, count, active_flow)."""
    ref = db.collection("AgentSessions").document(sid)
    snap = ref.get()
    if not snap.exists:
        now = datetime.now(timezone.utc)
        ref.set({
            "PromptCount": 0,
            "CreatedAt": firestore.SERVER_TIMESTAMP,
            "ExpiresAt": now + timedelta(hours=24),
            "LeadCaptured": False,
            "ActiveFlow": None,
        })
        return ref, 0, None
    data = snap.to_dict()
    return ref, data.get("PromptCount", 0), data.get("ActiveFlow")


# ---------------------------------------------------------------------------
# Main HTTP handler
# ---------------------------------------------------------------------------
@functions_framework.http
def sales_agent(request):
    """HTTP entry point for the sales agent."""
    headers = {"Access-Control-Allow-Origin": ALLOWED_ORIGIN}

    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204, {
            **headers,
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
        })

    body = request.get_json(silent=True) or {}
    user_msg = body.get("message", "").strip()
    session_id = body.get("session_id", "").strip()
    history = body.get("history", [])

    if not user_msg or not session_id:
        return ({"error": "message and session_id required"}, 400, headers)

    # Bot verification
    token = body.get("cf_token", "")
    if not verify_turnstile(token, request.headers.get("X-Forwarded-For")):
        return ({"error": "Verification failed. Please refresh and try again."}, 403, headers)

    # Budget check
    budget_ref, budget = get_budget(f"{TENANT}_{month_key()}")
    if budget["CurrentSpendUSD"] >= budget["MonthlyLimitUSD"]:
        return ({"html": DEGRADED_HTML, "tier": "Degraded", "model_called": False}, 200, headers)

    # Session check
    sess_ref, count, active_flow = get_session(session_id)
    if count >= MAX_PROMPTS_PER_SESSION:
        return ({"html": consultation_cta(), "tier": "SessionCap", "model_called": False}, 200, headers)

    # Classify intent once, then lock for session
    if active_flow is None:
        active_flow = classify_intent(user_msg)
        sess_ref.update({"ActiveFlow": active_flow})

    contents = build_contents(history, user_msg)

    # Generate response with appropriate cache/fallback strategy
    try:
        cache_map = get_cache_map()
        topic_cache = cache_map.get(active_flow)

        if topic_cache:
            # Scoped KB in cache + system prompt at call time
            cfg = types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS, temperature=0.3,
                system_instruction=SYSTEM_PROMPT,
                cached_content=topic_cache,
            )
        else:
            # GENERAL flow or cache miss: system prompt + inline global KB
            cfg = types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS, temperature=0.3,
                system_instruction=f"{SYSTEM_PROMPT}\n\nSITE CONTENT:\n{GLOBAL_FALLBACK}",
            )

        resp = client.models.generate_content(
            model=MODEL, contents=contents, config=cfg,
        )
    except Exception as e:
        return ({
            "reply": "I'm having a brief issue. Let's connect you with our team directly.",
            "cta_html": consultation_cta(),
            "tier": "Error",
            "model_called": False,
            "error_detail": str(e)[:200],
        }, 200, headers)

    new_count = count + 1

    # Post-process response
    answer = smart_truncate(resp.text or "")
    answer = strip_premature_cta_language(answer, new_count)

    # CTA marker handling
    has_marker = "[BOOK_CTA]" in answer
    answer = answer.replace("[BOOK_CTA]", "").strip()
    answer, had_url = strip_urls(answer)

    visitor_wants_booking = wants_booking(user_msg)

    show_cta = (
        (has_marker or had_url or visitor_wants_booking
         or new_count >= MAX_PROMPTS_PER_SESSION)
        and new_count >= MIN_TURNS_BEFORE_CTA
    )
    cta = consultation_cta() if show_cta else None

    # Cost tracking
    um = resp.usage_metadata
    cached = getattr(um, "cached_content_token_count", 0) or 0
    prompt_tok = getattr(um, "prompt_token_count", 0) or 0
    out_tok = getattr(um, "candidates_token_count", 0) or 0
    non_cached = max(prompt_tok - cached, 0)
    cost = (cached * RATE_INPUT_CACHED
            + non_cached * RATE_INPUT_STD
            + out_tok * RATE_OUTPUT)

    # Persist usage
    budget_ref.update({"CurrentSpendUSD": firestore.Increment(cost)})
    sess_ref.update({"PromptCount": firestore.Increment(1)})

    return ({
        "reply": answer,
        "cta_html": cta,
        "active_flow": active_flow,
        "prompts_used": new_count,
        "prompts_remaining": MAX_PROMPTS_PER_SESSION - new_count,
        "tier": "Full",
        "run_cost_usd": round(cost, 6),
        "model_called": True,
    }, 200, headers)
