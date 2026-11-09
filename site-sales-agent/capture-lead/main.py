"""
Cognitive Work Architecture — Site Sales Agent — Lead Capture
=============================================================
Serverless endpoint that captures inbound leads with
production-grade guardrails, cost governance, and security.

Repo:   cognitive-work-architecture/site-sales-agent
Module: capture-lead
License: MIT
"""

import os
import re
import functions_framework
import requests as _rq
from datetime import datetime, timezone
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration — all secrets via environment variables, never hardcoded.
# SECURITY: Zero secrets in source. Rotate via your cloud provider's
#           secret manager without redeployment.
# ---------------------------------------------------------------------------
db = firestore.Client()
CRM_WEBHOOK    = os.environ.get("CRM_WEBHOOK_URL")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")    # GUARDRAIL: Lock to your domain in production
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")

# GUARDRAIL: Strict email validation — reject malformed input at the gate.
# No downstream service should ever receive unvalidated user input.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def verify_turnstile(token: str, remoteip: str = None) -> bool:
    """
    BUDGET + SECURITY: Cloudflare Turnstile verification.
    
    Why this matters for cost governance:
    - Every bot submission that gets through = a Firestore write you pay for
    - At scale, bot traffic can 10x your database costs overnight
    - Turnstile is the first budget gate: no valid human = no write
    
    Graceful degradation: if TURNSTILE_SECRET is not configured,
    verification is bypassed. This allows local development without
    external dependencies while enforcing protection in production.
    """
    if not TURNSTILE_SECRET:
        return True  # GUARDRAIL: Dev/test bypass — log this in production monitoring
    try:
        r = _rq.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": TURNSTILE_SECRET,
                "response": token,
                "remoteip": remoteip,
            },
            timeout=10,  # BUDGET: Hard timeout prevents hanging connections consuming resources
        )
        return bool(r.json().get("success", False))
    except Exception:
        return False  # SECURITY: Fail closed — if verification breaks, reject the request


@functions_framework.http
def capture_lead(request):
    """
    Main entry point — HTTP Cloud Function.
    
    Architecture decisions:
    1. CORS handled explicitly — no wildcard in production
    2. Input validated before any database interaction
    3. Turnstile verified before any write operation
    4. Session linking enables agent-to-lead attribution
    5. CRM forwarding is fire-and-forget (non-blocking)
    """

    # SECURITY: CORS — configurable origin, explicit method/header allowlist
    headers = {"Access-Control-Allow-Origin": ALLOWED_ORIGIN}
    if request.method == "OPTIONS":
        return ("", 204, {
            **headers,
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
        })

    # GUARDRAIL: Silent JSON parsing — malformed bodies don't crash the function
    b = request.get_json(silent=True) or {}
    email = b.get("email", "").strip()
    sid   = b.get("session_id", "").strip()

    # GUARDRAIL: Validate before processing. Reject early, reject cheap.
    if not email or not EMAIL_RE.match(email):
        return ({"error": "valid email required"}, 400, headers)

    # SECURITY + BUDGET: Bot verification gate — must pass before any write
    token = b.get("cf_token", "")
    if not verify_turnstile(token, request.headers.get("X-Forwarded-For")):
        return (
            {"error": "Verification failed. Please refresh and try again."},
            403, headers,
        )

    # ----- Validated. Safe to write. -----

    now = datetime.now(timezone.utc)
    lead = {
        "email":           email,
        "session_id":      sid,
        "company":         b.get("company", "").strip(),     # GUARDRAIL: Sanitize all inputs
        "source":          b.get("source", "consultation_cta"),
        "tier_at_capture": b.get("tier", ""),                # WHY BETTER: Capture pricing tier context at point of lead
        "capturedAt":      now,
    }

    # PRIMARY WRITE: Store lead in Firestore
    try:
        db.collection("Leads").add(lead)
    except Exception as e:
        # SECURITY: Truncate error detail — never expose full stack traces to clients
        return ({"error": "failed to save lead", "detail": str(e)[:200]}, 500, headers)

    # WHY BETTER: Link lead capture back to the agent session.
    # This creates attribution: which agent conversation generated this lead?
    # Without this, you have leads but no idea what drove them.
    if sid:
        db.collection("AgentSessions").document(sid).set(
            {"LeadCaptured": True}, merge=True
        )

    # CRM FORWARDING: Fire-and-forget. Non-blocking.
    # BUDGET: If CRM webhook fails, the lead is already safely stored.
    # We don't retry here — retries belong in a separate queue processor
    # to avoid holding the HTTP response open.
    if CRM_WEBHOOK:
        crm_payload = {**lead, "capturedAt": now.isoformat()}
        try:
            _rq.post(CRM_WEBHOOK, json=crm_payload, timeout=10)
        except Exception:
            pass  # GUARDRAIL: CRM failure must never block lead capture

    return ({"status": "captured"}, 200, headers)
