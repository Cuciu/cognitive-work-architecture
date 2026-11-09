"""
Cognitive Work Architecture — Site Sales Agent — Budget Reset
==============================================================
Monthly scheduled job that initializes a fresh budget document
for each billing period.

This completes the budget governance lifecycle:

  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │  TRACK   │──▶│ MONITOR  │──▶│ ENFORCE  │──▶│  RESET   │
  │ per-req  │   │ every 4h │   │ degrade  │   │ monthly  │
  │ main.py  │   │ monitor  │   │ auto     │   │ THIS     │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘
       │                                             │
       └─────────── new month starts ────────────────┘

Schedule: 1st of each month via Cloud Scheduler → Pub/Sub → this function.

WHY A SEPARATE FUNCTION (not inline in monitor-budget)?
  - Separation of concerns: monitoring ≠ resetting
  - Different schedules: monitor runs every 4h, reset runs monthly
  - Auditability: reset events are distinct in Cloud Logging
  - Safety: a bug in monitoring logic can't accidentally reset spend

Repo:   cognitive-work-architecture/site-sales-agent
Module: reset-budget
License: MIT
"""

import functions_framework
from datetime import datetime, timezone
from google.cloud import firestore

db     = firestore.Client()
TENANT = "default"


def month_key():
    """Monthly billing period key — matches all other modules."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


@functions_framework.cloud_event
def reset_budget(cloud_event):
    """
    Initialize or reset the budget document for the current month.

    TWO MODES:
      1. Document exists (e.g., mid-month manual trigger or re-run):
         Reset spend counters and alert flags.
         PRESERVE budget limits — if someone manually adjusted
         MonthlyLimitUSD or DegradeThresholdUSD, those survive the reset.

      2. Document doesn't exist (new month, first run):
         Create with defaults. These defaults match get_budget()
         in sales-agent/main.py — if you change defaults here,
         change them there too.

    GUARDRAIL: The exists-check prevents accidental overwrites.
    If the doc already exists with custom limits (e.g., you raised
    MonthlyLimitUSD to $20 mid-month), a reset only clears the
    counters — it doesn't stomp your configuration.
    """
    ref_id = f"{TENANT}_{month_key()}"
    ref = db.collection("AgentBudget").document(ref_id)

    if ref.get().exists:
        # EXISTING DOC: Reset counters only. Preserve limits and config.
        # This is intentional — update() leaves unmentioned fields untouched.
        ref.update({
            "CurrentSpendUSD": 0.0,
            "ServiceTier":     "Full",
            "AlertSentAt80":   False,
            "AlertSentAt100":  False,
            "Status":          "Active",
        })
        print(f"[reset] Reset existing budget doc {ref_id} to fresh state.")
        return

    # NEW DOC: Create with defaults for the new billing period.
    ref.set({
        "AgentName":           "SalesAgent",
        "TenantOrAccount":     TENANT,
        "BillingPeriod":       month_key(),
        "MonthlyLimitUSD":     10.0,
        "DegradeThresholdUSD": 8.0,
        "CurrentSpendUSD":     0.0,
        "ServiceTier":         "Full",
        "BudgetMode":          "DegradeGracefully",
        "HardCeilingUSD":      12.0,
        "AlertSentAt80":       False,
        "AlertSentAt100":      False,
        "Status":              "Active",
    })
    print(f"[reset] Created fresh budget doc {ref_id} for new month.")
