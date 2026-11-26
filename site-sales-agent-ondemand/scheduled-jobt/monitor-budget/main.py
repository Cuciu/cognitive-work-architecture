import os, functions_framework
from datetime import datetime, timezone
from google.cloud import firestore

db = firestore.Client()
TENANT = "default"
ALERT_WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL")


def month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


@functions_framework.cloud_event
def monitor_budget(cloud_event):
    """Triggered every 4h via Pub/Sub. Checks spend, fires 80%/100% alerts, sets tier."""
    ref_id = f"{TENANT}_{month_key()}"
    ref = db.collection("AgentBudget").document(ref_id)
    snap = ref.get()
    if not snap.exists:
        print(f"[monitor] No budget doc {ref_id} yet - nothing to monitor.")
        return

    b = snap.to_dict()
    spend = b.get("CurrentSpendUSD", 0.0)
    limit = b.get("MonthlyLimitUSD", 10.0)
    degrade_at = b.get("DegradeThresholdUSD", 8.0)
    pct = (spend / limit * 100) if limit else 0

    updates = {}

    if spend >= degrade_at and not b.get("AlertSentAt80", False):
        updates["AlertSentAt80"] = True
        _alert(f"WARNING: SalesAgent budget at {pct:.0f}% "
               f"(${spend:.2f}/${limit:.2f}) - degrade threshold reached.")

    if spend >= limit and not b.get("AlertSentAt100", False):
        updates["AlertSentAt100"] = True
        updates["ServiceTier"] = "Degraded"
        _alert(f"CRITICAL: SalesAgent budget EXHAUSTED "
               f"(${spend:.2f}/${limit:.2f}) - tier set to Degraded.")

    if spend < limit and b.get("ServiceTier") == "Degraded":
        updates["ServiceTier"] = "Full"

    if updates:
        ref.update(updates)
        print(f"[monitor] {ref_id} spend=${spend:.4f} ({pct:.1f}%) updates={updates}")
    else:
        print(f"[monitor] {ref_id} spend=${spend:.4f} ({pct:.1f}%) - no action.")


def _alert(msg):
    print(msg)
    if ALERT_WEBHOOK:
        try:
            import requests
            requests.post(ALERT_WEBHOOK, json={"text": msg}, timeout=10)
        except Exception:
            pass
