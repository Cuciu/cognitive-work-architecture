"""
Scheduled Job: monitor-budget
Runs every 4 hours via Pub/Sub. Checks agent spend against budget
thresholds and fires alerts at 80% and 100%. Sets service tier to
Degraded when budget is exhausted.

Budget Protection Flow:
  - 80% threshold → warning alert
  - 100% threshold → critical alert + tier set to Degraded
  - Spend drops below limit → tier restored to Full
"""

import os
import functions_framework
import requests as _rq
from datetime import datetime, timezone
from google.cloud import firestore

db = firestore.Client()
TENANT = os.environ.get("TENANT", "default")
ALERT_WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL")


def month_key():
    """Current billing period key (YYYY-MM)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _alert(msg):
    """Log alert and optionally forward to webhook (Slack, Teams, etc.)."""
    print(msg)
    if ALERT_WEBHOOK:
        try:
            _rq.post(ALERT_WEBHOOK, json={"text": msg}, timeout=10)
        except Exception:
            pass


@functions_framework.cloud_event
def monitor_budget(cloud_event):
    """Check spend against thresholds and update service tier."""
    ref_id = f"{TENANT}_{month_key()}"
    ref = db.collection("AgentBudget").document(ref_id)
    snap = ref.get()

    if not snap.exists:
        print(f"[monitor] No budget doc {ref_id} yet — nothing to monitor.")
        return

    b = snap.to_dict()
    spend = b.get("CurrentSpendUSD", 0.0)
    limit = b.get("MonthlyLimitUSD", 10.0)
    degrade_at = b.get("DegradeThresholdUSD", 8.0)
    pct = (spend / limit * 100) if limit else 0

    updates = {}

    # 80% threshold — warning
    if spend >= degrade_at and not b.get("AlertSentAt80", False):
        updates["AlertSentAt80"] = True
        _alert(f"WARNING: Agent budget at {pct:.0f}% "
               f"(${spend:.2f}/${limit:.2f}) — degrade threshold reached.")

    # 100% threshold — critical, degrade service
    if spend >= limit and not b.get("AlertSentAt100", False):
        updates["AlertSentAt100"] = True
        updates["ServiceTier"] = "Degraded"
        _alert(f"CRITICAL: Agent budget EXHAUSTED "
               f"(${spend:.2f}/${limit:.2f}) — tier set to Degraded.")

    # Auto-recover if spend drops below limit (e.g., after budget increase)
    if spend < limit and b.get("ServiceTier") == "Degraded":
        updates["ServiceTier"] = "Full"

    if updates:
        ref.update(updates)
        print(f"[monitor] {ref_id} spend=${spend:.4f} ({pct:.1f}%) updates={updates}")
    else:
        print(f"[monitor] {ref_id} spend=${spend:.4f} ({pct:.1f}%) — no action.")
