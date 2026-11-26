import os, re, functions_framework
import requests as _rq
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types
from google.cloud import firestore
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION = os.environ.get("REGION", "us-central1")
MODEL = os.environ.get("MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-005")
TENANT = "default"
CONSULTATION_URL = os.environ["CONSULTATION_URL"]
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")

MAX_PROMPTS_PER_SESSION = 6
MAX_ANSWER_CHARS   = 700
MAX_OUTPUT_TOKENS  = 250
MAX_HISTORY_TURNS  = 10
MIN_TURNS_BEFORE_CTA = 3

RATE_INPUT_CACHED  = 0.15 / 1_000_000
RATE_INPUT_STD     = 1.50 / 1_000_000
RATE_OUTPUT        = 7.50 / 1_000_000

# === Vector retrieval controls ===
VECTOR_COLLECTION = os.environ.get("VECTOR_COLLECTION", "KBVectors")
RETRIEVE_TOP_K = int(os.environ.get("RETRIEVE_TOP_K", "4"))

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db = firestore.Client()

# ---------------------------------------------------------------------------
# Global fallback: loaded from knowledge/global.md (router + voice/rules).
# Used for GENERAL (greeter) and as safety net when retrieval returns nothing.
# ---------------------------------------------------------------------------
KB_DIR = os.environ.get("KB_DIR", "knowledge")


def _load_global_fallback():
    for path in (os.path.join(KB_DIR, "global.md"), "global.md"):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            continue
    return ""


GLOBAL_FALLBACK = _load_global_fallback()

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------
# This is a sample prompt demonstrating the state-machine pattern.
# Replace with your own company name, proof points, and domain expertise.
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are [Company]'s senior sales advisor — a direct, technically credible "
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
    "PROOF POINT GOVERNANCE: "
    "Never cite the same case study, benchmark, or metric more than once "
    "per conversation. Track which proof points you have already used. "
    "When multiple proof points support the same claim, choose the one most "
    "relevant to the visitor's question and implied role. "
    "If the visitor asks a follow-up on the same topic, deepen the existing "
    "proof point with mechanism detail rather than repeating the headline number. "
    "\n\n"
    "DEPTH OVER BREADTH: "
    "When the visitor asks about a specific capability, name the exact "
    "framework, method, deliverable, or module — not a category summary. "
    "The knowledge base contains implementation-level detail — use it. "
    "Specificity builds credibility. "
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
    "Match their problem to a specific capability from the site content. "
    "Name the framework, method, or deliverable. Cite a proof point or "
    "benchmark — one you have not used yet in this conversation. "
    "If they ask to go deeper or say 'tell me more' — expand with NEW "
    "detail and a DIFFERENT proof point. Stay in STATE 3. "
    "Never restart discovery. Never repeat yourself. "
    "After delivering a solution, offer to explore a related angle "
    "or ask a qualifying question to tailor further. "
    "\n\n"
    "STATE 4 INTENT (booking): "
    "Enter ONLY when the visitor explicitly signals buying intent — "
    "examples: asks about pricing, timelines, availability, next steps. "
    "Write ONE sentence inviting them to book a consultation, then end "
    "your message with the exact marker [BOOK_CTA]. "
    "Emit [BOOK_CTA] at most ONCE per conversation. "
    "NEVER emit [BOOK_CTA] in your first two responses. "
    "\n\n"
    "LOW-ENERGY REPLIES — CRITICAL: "
    "If the visitor replies with 'ok', 'sure', 'great', 'cool', 'thanks', "
    "or similar after you presented a solution — this is NOT buying intent. "
    "Do NOT move to STATE 4. Instead, offer one additional insight they "
    "haven't seen yet. If they give two consecutive low-energy replies, "
    "offer a graceful exit: 'Happy to answer anything else — "
    "or I can send you our overview if you'd prefer to review offline.' "
    "\n\n"
    "CRITICAL RULES: "
    "Once they state a problem, go DEEPER not WIDER. Only forward, never reset. "
    "Never ask what brought them here more than once. "
    "Never present the same answer twice — always add new detail. "
    "Never cite the same proof point twice — always rotate to a fresh one. "
    "NEVER write a URL or booking link in your text. "
    "Use [BOOK_CTA] instead, only in STATE 4, maximum once. "
    "\n\n"
    "TONE: Direct, senior, peer-to-peer. Trusted advisor, not salesperson. "
    "You earn the meeting by demonstrating you know their world — "
    "not by asking for it repeatedly."
)

# ---------------------------------------------------------------------------
# Intent classifier
# ---------------------------------------------------------------------------
# Flow names MUST match the indexer's TOPIC_FILES keys (vector namespaces).
# Replace these with your own service areas / topic taxonomy.
# ---------------------------------------------------------------------------
VALID_FLOWS = {
    "TOPIC_A", "TOPIC_B", "TOPIC_C", "TOPIC_D",
    # Add your topic namespaces here — must match cache_setup.py TOPIC_FILES
}

CLASSIFIER_PROMPT = (
    "Classify the user's intent into exactly ONE category. "
    "Respond with ONLY the category name, nothing else.\n"
    "- TOPIC_A: [describe what queries match topic A]\n"
    "- TOPIC_B: [describe what queries match topic B]\n"
    "- TOPIC_C: [describe what queries match topic C]\n"
    "- TOPIC_D: [describe what queries match topic D]\n"
    "- GENERAL: unclear, greeting, or broad 'what do you do'"
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


def _embed_query(text):
    """Embed the user query for vector search. Returns list[float] or None."""
    try:
        resp = client.models.embed_content(model=EMBED_MODEL, contents=[text])
        return list(resp.embeddings[0].values)
    except Exception:
        return None


def retrieve_kb(topic, query, top_k=RETRIEVE_TOP_K):
    """
    Topic-filtered vector search over KBVectors.
    Returns assembled KB text for the classified flow, or "" on miss/GENERAL.
    Cost tracks USAGE: we only embed + retrieve when there is a real query.
    """
    if topic not in VALID_FLOWS:
        return ""

    qvec = _embed_query(query)
    if qvec is None:
        return ""

    try:
        col = db.collection(VECTOR_COLLECTION)
        results = (
            col.where(filter=firestore.FieldFilter("topic", "==", topic))
               .find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(qvec),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=top_k,
                )
               .stream()
        )
        chunks = []
        for snap in results:
            d = snap.to_dict() or {}
            content = (d.get("content") or "").strip()
            if content:
                chunks.append(content)
        return "\n\n---\n\n".join(chunks)
    except Exception as e:
        print(f"[sales-agent] retrieval FAILED for topic={topic}: {str(e)[:200]}")
        return ""


# ---------------------------------------------------------------------------
# CTA and utility functions
# ---------------------------------------------------------------------------
DEGRADED_HTML = (
    "<div class='agent-msg degraded'>"
    "<p>Our assistant has reached its usage limit for this period.</p>"
    "<p><strong>Let's talk directly</strong> — book a free consultation:</p>"
    f"<a class='cta-button' href='{CONSULTATION_URL}' target='_blank'>"
    f"Book a free consultation</a>"
    "</div>"
)


def consultation_cta():
    return (
        "<div class='agent-cta'>"
        "<p>Let's map this to your specific situation.</p>"
        f"<a class='cta-button' href='{CONSULTATION_URL}' "
        f"target='_blank'>Book a 30-minute session</a>"
        "</div>"
    )


def verify_turnstile(token, remoteip=None):
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


def smart_truncate(text, limit=MAX_ANSWER_CHARS):
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
    t = text or ""
    had_url = bool(re.search(r'https?://\S+', t))
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'\s*(here|below|at)\s*:?\s*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t, had_url


def strip_premature_cta_language(text, turn_count, min_turns=MIN_TURNS_BEFORE_CTA):
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
    user_lc = (user_msg or "").lower()
    signals = [
        "book", "consultation", "call", "talk to", "speak to", "schedule",
        "get started", "how do we start", "sign up", "demo", "meeting",
        "pricing", "price", "cost", "quote", "contact you", "reach out"
    ]
    return any(s in user_lc for s in signals)


# ---------------------------------------------------------------------------
# Budget + session management
# ---------------------------------------------------------------------------
def month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_budget(ref_id):
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
    headers = {"Access-Control-Allow-Origin": ALLOWED_ORIGIN}
    if request.method == "OPTIONS":
        return ("", 204, {**headers,
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type"})

    body = request.get_json(silent=True) or {}
    user_msg = body.get("message", "").strip()
    session_id = body.get("session_id", "").strip()
    history = body.get("history", [])

    if not user_msg or not session_id:
        return ({"error": "message and session_id required"}, 400, headers)

    token = body.get("cf_token", "")
    if not verify_turnstile(token, request.headers.get("X-Forwarded-For")):
        return ({"error": "Verification failed. Please refresh and try again."},
                403, headers)

    budget_ref, budget = get_budget(f"{TENANT}_{month_key()}")
    if budget["CurrentSpendUSD"] >= budget["MonthlyLimitUSD"]:
        return ({"html": DEGRADED_HTML, "tier": "Degraded", "model_called": False},
                200, headers)

    sess_ref, count, active_flow = get_session(session_id)
    if count >= MAX_PROMPTS_PER_SESSION:
        return ({"html": consultation_cta(), "tier": "SessionCap",
                 "model_called": False}, 200, headers)

    # Classify intent once, then lock it for the session
    if active_flow is None:
        active_flow = classify_intent(user_msg)
        sess_ref.update({"ActiveFlow": active_flow})

    contents = build_contents(history, user_msg)

    try:
        # Topic-filtered vector retrieval; inject KB as SITE CONTENT
        kb_text = retrieve_kb(active_flow, user_msg)

        if kb_text:
            system = (
                f"{SYSTEM_PROMPT}\n\n"
                f"SITE CONTENT (topic: {active_flow}):\n{kb_text}"
            )
        else:
            # Greeter (GENERAL) or retrieval miss: run on global voice/rules
            system = f"{SYSTEM_PROMPT}\n\nSITE CONTENT:\n{GLOBAL_FALLBACK}"

        cfg = types.GenerateContentConfig(
            max_output_tokens=MAX_OUTPUT_TOKENS, temperature=0.3,
            system_instruction=system)
        resp = client.models.generate_content(
            model=MODEL, contents=contents, config=cfg)

    except Exception as e:
        return ({"reply": "I'm having a brief issue. Let's connect you with "
                          "our team directly.",
                 "cta_html": consultation_cta(), "tier": "Error",
                 "model_called": False, "error_detail": str(e)[:200]},
                200, headers)

    new_count = count + 1
    answer = smart_truncate(resp.text or "")
    answer = strip_premature_cta_language(answer, new_count)

    has_marker = "[BOOK_CTA]" in answer
    answer = answer.replace("[BOOK_CTA]", "").strip()
    answer, had_url = strip_urls(answer)

    visitor_wants_booking = wants_booking(user_msg)
    show_cta = (
        (has_marker or had_url or visitor_wants_booking
         or (new_count >= MAX_PROMPTS_PER_SESSION))
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

    budget_ref.update({"CurrentSpendUSD": firestore.Increment(cost)})
    sess_ref.update({"PromptCount": firestore.Increment(1)})

    return ({"reply": answer,
             "cta_html": cta,
             "active_flow": active_flow,
             "prompts_used": new_count,
             "prompts_remaining": MAX_PROMPTS_PER_SESSION - new_count,
             "tier": "Full",
             "run_cost_usd": round(cost, 6),
             "model_called": True}, 200, headers)
