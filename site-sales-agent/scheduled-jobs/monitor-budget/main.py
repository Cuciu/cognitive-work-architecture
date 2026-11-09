"""
Cognitive Work Architecture — Site Sales Agent — Budget Monitor
================================================================
Scheduled watchdog that monitors agent spend against monthly budget
thresholds and enforces tier transitions.

This is the ENFORCEMENT LAYER of the budget governance architecture:

  ┌──────────────────────────────────────────────────────────────┐
  │  BUDGET GOVERNANCE — Three Layers                            │
  ├──────────────────────────────────────────────────────────────┤
  │  1. sales-agent/main.py     → Tracks cost PER REQUEST        │
  │  2. monitor-budget/main.py  → Enforces thresholds + alerts   │
  │  3. Firestore budget doc    → Single source of truth          │
  └──────────────────────────────────────────────────────────────┘

Why three layers?
  - The agent tracks cost in real-time but can't alert or change its
    own tier (it only reads the budget, doesn't govern it)
  - The monitor runs on a schedule, checks thresholds, fires alerts,
    and sets the tier — it's the financial controller
  - Firestore is the shared state — both layers read/write the same doc

Schedule: Every 4 hours via Cloud Scheduler → Pub/Sub → this function.
  4 hours is a balance between cost awareness and invocation cost.
  At $10/month budget, checking more frequently costs more than it saves.

Repo:   cognitive-work-architecture/site-sales-agent
Module: monitor-budget
License: MIT
"""

import os
import functions_framework
from datetime import datetime, timezone
from google.cloud import firestore

db     = firestore.Client()
TENANT = "default"

# SECURITY: Alert webhook URL via environment variable.
# Supports Slack, Teams, PagerDuty, or any webhook-compatible endpoint.
ALERT_WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL")


def month_key():
    """Monthly billing period key — matches sales-agent/main.py."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


@functions_framework.cloud_event
def monitor_budget(cloud_event):
    """
    Budget enforcement watchdog. Triggered every 4h via Pub/Sub.

    WHAT IT CHECKS:
    ┌─────────────────────────────────────────────────────────┐
    │  Spend vs. Thresholds                                    │
    │                                                          │
    │  $0 ─────── $8 (80%) ──── $10 (100%) ──── $12 (Hard)   │
    │  │   Full    │  ⚠️ Alert   │  🛑 Degrade  │  Ceiling   │
    │  │           │  (once)      │  (once)       │            │
    └─────────────────────────────────────────────────────────┘

    BEHAVIORS:
      - At 80% (DegradeThreshold): Fires warning alert ONCE
      - At 100% (MonthlyLimit): Fires critical alert ONCE,
        sets ServiceTier to "Degraded"
      - Below limit after being Degraded: Auto-recovers to "Full"
        (handles manual budget increases or new billing periods)

    GUARDRAIL: Alert deduplication via AlertSentAt80/AlertSentAt100
    flags. Without this, you'd get an alert every 4 hours once you
    cross a threshold — that's noise, not governance.
    """
    ref_id = f"{TENANT}_{month_key()}"
    ref = db.collection("AgentBudget").document(ref_id)
    snap = ref.get()

    if not snap.exists:
        print(f"[monitor] No budget doc {ref_id} yet — nothing to monitor.")
        return

    b         = snap.to_dict()
    spend     = b.get("CurrentSpendUSD", 0.0)
    limit     = b.get("MonthlyLimitUSD", 10.0)
    degrade_at = b.get("DegradeThresholdUSD", 8.0)
    pct       = (spend / limit * 100) if limit else 0

    updates = {}

    # --- 80% WARNING GATE ---
    # BUDGET: Early warning before you hit the wall.
    # Fires once (AlertSentAt80 flag prevents repeat alerts).
    if spend >= degrade_at and not b.get("AlertSentAt80", False):
        updates["AlertSentAt80"] = True
        _alert(
            f"⚠️ WARNING: SalesAgent budget at {pct:.0f}% "
            f"(${spend:.2f}/${limit:.2f}) — degrade threshold reached."
        )

    # --- 100% CRITICAL GATE ---
    # BUDGET: Hard stop. Sets tier to "Degraded" so the agent
    # stops calling the model and shows the fallback CTA instead.
    if spend >= limit and not b.get("AlertSentAt100", False):
        updates["AlertSentAt100"] = True
        updates["ServiceTier"] = "Degraded"
        _alert(
            f"🛑 CRITICAL: SalesAgent budget EXHAUSTED "
            f"(${spend:.2f}/${limit:.2f}) — tier set to Degraded."
        )

    # --- AUTO-RECOVERY ---
    # WHY BETTER: If you manually increase the budget or a new month
    # starts, the tier automatically recovers to "Full" without
    # requiring a manual reset. Most systems require human intervention
    # to un-degrade — this one self-heals.
    if spend < limit and b.get("ServiceTier") == "Degraded":
        updates["ServiceTier"] = "Full"

    # --- APPLY UPDATES ---
    if updates:
        ref.update(updates)
        print(f"[monitor] {ref_id} spend=${spend:.4f} ({pct:.1f}%) updates={updates}")
    else:
        print(f"[monitor] {ref_id} spend=${spend:.4f} ({pct:.1f}%) — no action.")


def _alert(msg):
    """
    Send alert to webhook (Slack, Teams, PagerDuty, etc.)

    GUARDRAIL: Alert delivery is fire-and-forget. If the webhook
    fails, the alert is still logged to stdout (captured by Cloud
    Logging). Budget enforcement does NOT depend on alert delivery —
    the tier change happens regardless of whether the alert sends.

    This is important: alerting is informational. Tier enforcement
    is the actual control. They are intentionally decoupled.
    """
    print(msg)  # Always log — Cloud Logging captures this even if webhook fails
    if ALERT_WEBHOOK:
        try:
            import requests
            requests.post(ALERT_WEBHOOK, json={"text": msg}, timeout=10)
        except Exception:
            pass  # Alert failure must never block budget enforcement
