"""
Scheduled Job: reset-budget
Triggered monthly via Pub/Sub. Resets the agent budget document
for the new billing period or creates one if it doesn't exist.

Budget defaults:
  - MonthlyLimitUSD: 10.00
  - DegradeThresholdUSD: 8.00 (80%)
  - HardCeilingUSD: 12.00
  - BudgetMode: DegradeGracefully
"""

import os
import functions_framework
from datetime import datetime, timezone
from google.cloud import firestore

db = firestore.Client()
TENANT = os.environ.get("TENANT", "default")

# Budget defaults — customize per deployment
MONTHLY_LIMIT = float(os.environ.get("MONTHLY_LIMIT_USD", "10.0"))
DEGRADE_THRESHOLD = float(os.environ.get("DEGRADE_THRESHOLD_USD", "8.0"))
HARD_CEILING = float(os.environ.get("HARD_CEILING_USD", "12.0"))


def month_key():
    """Current billing period key (YYYY-MM)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


@functions_framework.cloud_event
def reset_budget(cloud_event):
    """Reset or initialize the budget document for the current billing period."""
    ref_id = f"{TENANT}_{month_key()}"
    ref = db.collection("AgentBudget").document(ref_id)

    if ref.get().exists:
        # Reset existing document
        ref.update({
            "CurrentSpendUSD": 0.0,
            "ServiceTier": "Full",
            "AlertSentAt80": False,
            "AlertSentAt100": False,
            "Status": "Active",
        })
        print(f"[reset] Reset existing budget doc {ref_id} to fresh state.")
        return

    # Create new document for the billing period
    ref.set({
        "AgentName": "SalesAgent",
        "TenantOrAccount": TENANT,
        "BillingPeriod": month_key(),
        "MonthlyLimitUSD": MONTHLY_LIMIT,
        "DegradeThresholdUSD": DEGRADE_THRESHOLD,
        "CurrentSpendUSD": 0.0,
        "ServiceTier": "Full",
        "BudgetMode": "DegradeGracefully",
        "HardCeilingUSD": HARD_CEILING,
        "AlertSentAt80": False,
        "AlertSentAt100": False,
        "Status": "Active",
    })
    print(f"[reset] Created fresh budget doc {ref_id} for new month.")
