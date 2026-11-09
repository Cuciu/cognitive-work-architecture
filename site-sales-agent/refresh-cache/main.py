"""
Cognitive Work Architecture — Site Sales Agent — Cache Refresh
==============================================================
Refreshes the cached context for the sales agent, enabling
cost-governed inference by paying for site content ingestion
ONCE instead of on every request.

This is the BUDGET GOVERNANCE layer of the agent architecture.

Repo:   cognitive-work-architecture/site-sales-agent
Module: refresh-cache
License: MIT

COST IMPACT:
  Without cache: site content sent with every request → full input token cost
  With cache:    site content cached for 25hrs → ~75% reduction in input costs
  At 1,000 conversations/day, this is the difference between viable and bankrupt.
"""

import os
import functions_framework
from google import genai
from google.genai import types
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration
# BUDGET: Model selection is a cost decision, not just a capability decision.
# flash-lite is chosen deliberately — it handles sales conversations with
# sufficient quality at a fraction of the cost of larger models.
# ---------------------------------------------------------------------------
PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION   = os.environ.get("REGION", "global")
MODEL      = os.environ.get("MODEL", "gemini-3.5-flash-lite")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db     = firestore.Client()

# ---------------------------------------------------------------------------
# SYSTEM PROMPT — Agent Behavioral Architecture
# ---------------------------------------------------------------------------
# GUARDRAILS: This prompt is NOT a suggestion to the model. It is a
# constraint system. Every rule exists because of a specific failure mode
# observed in production:
#
#   - "Answer ONLY from provided content"  → prevents hallucinated capabilities
#   - "Never write a URL"                  → prevents hallucinated/broken links
#   - "[BOOK_CTA] at most ONCE"            → prevents aggressive sales behavior
#   - "Not in first two responses"         → forces value-first conversation
#   - "Low-energy reply handling"          → prevents misreading politeness as intent
#   - "4-state machine"                    → prevents conversation loops/resets
#
# WHY BETTER: Most chatbots are stateless — they treat every message in
# isolation. This agent tracks conversation state with explicit transition
# rules, forbidden transitions, and graceful exits. The result: conversations
# that feel like talking to a senior advisor, not a FAQ bot.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    # --- IDENTITY & TRUST ANCHOR ---
    "You are Sky Alleys' senior sales advisor — a direct, technically credible "
    "peer who earns trust by demonstrating expertise, not by pushing meetings. "
    "\n\n"

    # --- ANSWER GUARDRAILS ---
    # GUARDRAIL: Hard boundary on knowledge source. The agent cannot invent
    # capabilities the company doesn't have. This is the #1 failure mode
    # in sales chatbots — promising things that don't exist.
    "ANSWER RULES: "
    "Answer ONLY from the provided site content. Never invent capabilities. "
    "Keep responses concise but complete — 2 to 5 sentences depending on "
    "question complexity. Technical questions deserve technical depth. "
    "This is a small chat window: use short paragraphs, never walls of text. "
    "Finish every thought with a full sentence. "
    "Vary your phrasing — never repeat the same opener or closer twice. "
    "\n\n"

    # --- CONVERSATION STATE MACHINE ---
    # GUARDRAIL: Explicit states prevent the agent from looping, resetting,
    # or jumping to a booking CTA before earning the right to ask.
    "CONVERSATION STATES — track which state you are in: "
    "\n\n"

    # STATE 1: Entry point — used once, never revisited
    "STATE 1 DISCOVERY: "
    "If they open with a greeting or vague request, ask ONE question about "
    "their challenge. Enter only once. Never return to this state. "
    "If they open with a specific question, skip directly to STATE 2 or 3. "
    "\n\n"

    # STATE 2: Brief validation — builds empathy without stalling
    "STATE 2 ACKNOWLEDGE: "
    "Briefly validate why their challenge is hard or costly — one sentence. "
    "Ask ONE clarifier only if the problem is genuinely ambiguous, "
    "then move to STATE 3. "
    "\n\n"

    # STATE 3: Primary operating state — where value is delivered
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

    # STATE 4: Booking — only on explicit buying signals
    # GUARDRAIL: The agent earns the meeting by demonstrating value.
    # It never asks for a meeting preemptively.
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

    # GUARDRAIL: Low-energy reply trap — the most common chatbot failure.
    # When a visitor says "ok" or "thanks," most bots interpret it as
    # engagement and push for a meeting. This rule prevents that.
    "LOW-ENERGY REPLIES — CRITICAL: "
    "If the visitor replies with 'ok', 'sure', 'great', 'cool', 'thanks', "
    "or similar after you presented a solution — this is NOT buying intent. "
    "Do NOT move to STATE 4. Instead, offer one additional insight they "
    "haven't seen yet: a related proof point, a different framework angle, "
    "or a brief qualifying question. If they give two consecutive low-energy "
    "replies, offer a graceful exit: 'Happy to answer anything else — "
    "or I can send you our overview if you'd prefer to review offline.' "
    "\n\n"

    # --- HARD RULES ---
    "CRITICAL RULES: "
    "Once they state a problem, go DEEPER not WIDER. Only forward, never reset. "
    "Never ask what brought them here more than once. "
    "Never present the same answer twice — always add new detail. "
    "NEVER write a URL or booking link in your text. "
    "Use [BOOK_CTA] instead, only in STATE 4, maximum once. "
    "A booking button is attached automatically whenever you emit [BOOK_CTA]. "
    "Writing a raw URL is forbidden. "
    "\n\n"

    # --- TONE ---
    "TONE: Direct, senior, peer-to-peer. Trusted advisor, not salesperson. "
    "You earn the meeting by demonstrating you know their world — "
    "not by asking for it repeatedly."
)


@functions_framework.cloud_event
def create_site_cache(cloud_event):
    """
    Creates/refreshes the cached context for the sales agent.

    BUDGET GOVERNANCE — why this function exists:
    ─────────────────────────────────────────────
    The site content (~15-30K tokens) is the agent's knowledge base.
    Without caching, these tokens are sent with EVERY conversation turn.

      Without cache:  input_tokens = site_content + conversation_history + user_message
      With cache:     input_tokens = conversation_history + user_message (site = cached)

    At scale, this reduces input token costs by ~60-75%.

    ARCHITECTURE:
    - Cache is created with a 25-hour TTL (90000s)
    - Cache name is stored in Firestore → agent reads it at runtime
    - This enables zero-downtime cache rotation:
      1. New cache created with updated site content
      2. Firestore config updated atomically
      3. Next agent request picks up new cache
      4. Old cache expires naturally

    GRACEFUL DEGRADATION:
    - If cache creation fails, the agent falls back to inline context
    - The agent still works — it just costs more per request
    - Failure is logged loudly so it's caught in monitoring
    """
    try:
        with open("site_content_full.txt") as f:
            site = f.read()

        cache = client.caches.create(
            model=MODEL,
            config=types.CreateCachedContentConfig(
                display_name="skyalleys_sales_site_context",
                system_instruction=SYSTEM_PROMPT,
                contents=[site],
                ttl="90000s",  # BUDGET: 25 hours — covers daily refresh cycle with 1hr buffer
            ),
        )

        # WHY BETTER: Cache name stored in Firestore, not hardcoded.
        # This decouples cache lifecycle from agent deployment.
        # Update the cache without redeploying the agent.
        db.collection("AgentConfig").document("SalesAgent").set(
            {"CacheName": cache.name}, merge=True
        )
        print(f"[refresh-cache] Cache created: {cache.name}")

    except Exception as e:
        # GUARDRAIL: Truncate error — never log full stack traces that might
        # contain project IDs, paths, or credentials in cloud logging.
        msg = str(e)[:300]

        # BUDGET: This log message explicitly names the cost impact of failure.
        # "Full-price context" tells the on-call engineer exactly what's at stake.
        print(
            f"[refresh-cache] CACHE CREATION FAILED — agent falls back to "
            f"inline (full-price) context. Reason: {msg}"
        )
