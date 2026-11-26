import functions_framework
from datetime import datetime, timezone
from google.cloud import firestore

db = firestore.Client()
TENANT = "default"


def month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


@functions_framework.cloud_event
def reset_budget(cloud_event):
    """Triggered monthly via Pub/Sub. Initializes a fresh budget doc for the new period."""
    ref_id = f"{TENANT}_{month_key()}"
    ref = db.collection("AgentBudget").document(ref_id)

    if ref.get().exists:
        ref.update({
            "CurrentSpendUSD": 0.0,
            "ServiceTier": "Full",
            "AlertSentAt80": False,
            "AlertSentAt100": False,
            "Status": "Active",
        })
        print(f"[reset] Reset existing budget doc {ref_id} to fresh state.")
        return

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
    print(f"[reset] Created fresh budget doc {ref_id} for new month.")
