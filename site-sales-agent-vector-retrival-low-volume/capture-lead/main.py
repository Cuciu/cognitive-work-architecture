import os, re, functions_framework
import requests as _rq
from datetime import datetime, timezone
from google.cloud import firestore

db = firestore.Client()
CRM_WEBHOOK = os.environ.get("CRM_WEBHOOK_URL")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


@functions_framework.http
def capture_lead(request):
    headers = {"Access-Control-Allow-Origin": ALLOWED_ORIGIN}
    if request.method == "OPTIONS":
        return ("", 204, {**headers,
                "Access-Control-Allow-Methods": "POST",
                "Access-Control-Allow-Headers": "Content-Type"})

    b = request.get_json(silent=True) or {}
    email = b.get("email", "").strip()
    sid = b.get("session_id", "").strip()

    if not email or not EMAIL_RE.match(email):
        return ({"error": "valid email required"}, 400, headers)

    token = b.get("cf_token", "")
    if not verify_turnstile(token, request.headers.get("X-Forwarded-For")):
        return ({"error": "Verification failed. Please refresh and try again."}, 403, headers)

    now = datetime.now(timezone.utc)
    lead = {
        "email": email, "session_id": sid,
        "company": b.get("company", "").strip(),
        "source": b.get("source", "consultation_cta"),
        "tier_at_capture": b.get("tier", ""),
        "capturedAt": now,
    }
    try:
        db.collection("Leads").add(lead)
    except Exception as e:
        return ({"error": "failed to save lead", "detail": str(e)[:200]}, 500, headers)

    if sid:
        db.collection("AgentSessions").document(sid).set({"LeadCaptured": True}, merge=True)

    if CRM_WEBHOOK:
        crm_payload = {**lead, "capturedAt": now.isoformat()}
        try:
            _rq.post(CRM_WEBHOOK, json=crm_payload, timeout=10)
        except Exception:
            pass

    return ({"status": "captured"}, 200, headers
