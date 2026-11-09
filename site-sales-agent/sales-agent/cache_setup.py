"""
Cognitive Work Architecture — Site Sales Agent — Cache Setup (CLI)
==================================================================
Manual / CLI companion to the automated refresh-cache Cloud Function.

Use this for:
  - First-time cache creation during initial deployment
  - Debugging cache issues in development
  - Manual cache rotation when you don't want to wait for the scheduler

Production cache refresh is handled by refresh-cache/main.py
(Cloud Function on a schedule). This script exists for operational
flexibility — you should never NEED it in steady state, but you
will be glad it exists at 2am when something breaks.

Repo:   cognitive-work-architecture/site-sales-agent
Module: sales-agent/cache_setup.py
License: MIT

Usage:
  $ export GCP_PROJECT=your-project-id
  $ python cache_setup.py
"""

import os
from google import genai
from google.genai import types
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration — mirrors refresh-cache/main.py
# ---------------------------------------------------------------------------
PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION   = os.environ.get("REGION", "global")
MODEL      = os.environ.get("MODEL", "gemini-3.5-flash-lite")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db     = firestore.Client()

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------
# ⚠️  ARCHITECTURE DECISION: Prompt Duplication
#
# This prompt is intentionally duplicated across three files:
#   1. sales-agent/main.py        — runtime agent
#   2. sales-agent/cache_setup.py — manual cache creation (this file)
#   3. refresh-cache/main.py      — automated cache refresh
#
# WHY duplicate instead of importing?
#   - Each module deploys independently (separate Cloud Functions)
#   - Shared imports create deployment coupling and versioning risk
#   - A cache created with prompt version A must match the agent
#     running prompt version A — import chains make this harder to verify
#
# TRADE-OFF: If you update the prompt, you must update all three files.
# Mitigation: the comment "MUST be identical" is a manual contract.
#
# ALTERNATIVE (not implemented): Store the prompt in Firestore/GCS
# and load at runtime. This adds a network dependency to cold start
# but eliminates duplication. Choose based on your latency tolerance.
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


def create_site_cache(event=None, context=None):
    """
    Create the sales agent context cache and store its name in Firestore.

    Returns the cache resource name for verification/logging.

    Unlike refresh-cache/main.py (which runs on a schedule with error
    handling and graceful degradation), this script is designed to fail
    loudly. If something is wrong, you want the stack trace immediately.
    """
    with open("site_content_full.txt") as f:
        site = f.read()

    cache = client.caches.create(
        model=MODEL,
        config=types.CreateCachedContentConfig(
            display_name="skyalleys_sales_site_context",
            system_instruction=SYSTEM_PROMPT,
            contents=[site],
            ttl="90000s",  # 25 hours
        ),
    )

    db.collection("AgentConfig").document("SalesAgent").set(
        {"CacheName": cache.name}, merge=True
    )
    print(f"Cache created: {cache.name}")
    return cache.name


# ---------------------------------------------------------------------------
# CLI entry point — run directly for manual cache creation
#   $ python cache_setup.py
#
# No arguments needed. Reads site_content_full.txt from current directory.
# Requires GCP_PROJECT environment variable and authenticated gcloud CLI.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    create_site_cache()
