"""
Cloud Function: refresh-cache
Scheduled rebuild of per-topic Vertex AI content caches.

Architecture:
  - System prompt lives in the agent (sales-agent/main.py) and is
    injected at call time via system_instruction.
  - These caches hold ONLY topic-scoped knowledge base content.
  - Global voice/rules are NOT duplicated into caches.
  - CacheMap is written to Firestore for the agent to consume.
  - Topics without a valid KB file fall back to inline context.
"""

import os
import functions_framework
from google import genai
from google.genai import types
from google.cloud import firestore

PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION = os.environ.get("REGION", "global")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash-lite")
CACHE_DISPLAY_PREFIX = os.environ.get("CACHE_PREFIX", "agent")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db = firestore.Client()

# Resolve KB_DIR relative to THIS file so it works regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.environ.get("KB_DIR", os.path.join(_HERE, "knowledge"))

# ---------------------------------------------------------------------------
# Topic → KB filename mapping
# Customize with your own service lines / knowledge domains.
# Each topic gets its own cache. GENERAL has no KB cache:
# the greeter runs on system_instruction alone.
# ---------------------------------------------------------------------------
TOPIC_FILES = {
    "TOPIC_A": "topic_a.md",
    "TOPIC_B": "topic_b.md",
    "TOPIC_C": "topic_c.md",
    "TOPIC_D": "topic_d.md",
    "TOPIC_E": "topic_e.md",
    "TOPIC_F": "topic_f.md",
    "TOPIC_G": "topic_g.md",
}

# 35 days (covers 31-day billing cycle + buffer)
CACHE_TTL = os.environ.get("CACHE_TTL", "3024000s")


def _load_kb(filename: str) -> str:
    """Load a knowledge base markdown file."""
    path = os.path.join(KB_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def create_topic_cache(topic: str, filename: str) -> str | None:
    """Create a KB-only Vertex AI content cache for a single topic.

    Returns cache resource name or None on failure.
    """
    try:
        kb = _load_kb(filename)
    except FileNotFoundError:
        print(f"[refresh-cache] {topic}: KB file '{filename}' not found "
              f"in '{KB_DIR}' — skipping.")
        return None

    if not kb.strip():
        print(f"[refresh-cache] {topic}: KB file '{filename}' is EMPTY — skipping.")
        return None

    try:
        cache = client.caches.create(
            model=MODEL,
            config=types.CreateCachedContentConfig(
                display_name=f"{CACHE_DISPLAY_PREFIX}_{topic.lower()}_kb",
                # KB content only — no system_instruction in cache
                contents=[kb],
                ttl=CACHE_TTL,
            ),
        )
        size_kb = round(len(kb.encode("utf-8")) / 1024, 1)
        print(f"[refresh-cache] {topic} cache created: {cache.name} "
              f"(KB size: {size_kb} KB)")
        return cache.name
    except Exception as e:
        print(f"[refresh-cache] {topic} CACHE FAILED — reason: {str(e)[:300]}")
        return None


@functions_framework.cloud_event
def create_site_cache(cloud_event):
    """Scheduled rebuild of all per-topic caches; writes CacheMap to Firestore."""
    print(f"[refresh-cache] Rebuilding caches from KB_DIR='{KB_DIR}' "
          f"for topics: {list(TOPIC_FILES.keys())}")

    cache_map = {}
    for topic, filename in TOPIC_FILES.items():
        name = create_topic_cache(topic, filename)
        if name:
            cache_map[topic] = name

    if cache_map:
        db.collection("AgentConfig").document("SalesAgent").set(
            {"CacheMap": cache_map}, merge=True
        )
        print(f"[refresh-cache] Stored CacheMap with {len(cache_map)}/"
              f"{len(TOPIC_FILES)} topic caches: {list(cache_map.keys())}")

        missing = [t for t in TOPIC_FILES if t not in cache_map]
        if missing:
            print(f"[refresh-cache] WARNING: {len(missing)} topic(s) NOT cached: "
                  f"{missing} — agent falls back to inline for those flows.")
    else:
        print("[refresh-cache] CACHE CREATION FAILED for ALL topics — "
              "agent falls back to inline (full-price) context.")
