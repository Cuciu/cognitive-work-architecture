"""
Cache Setup: Manual / CLI cache builder
Builds per-topic Vertex AI content caches and writes CacheMap to Firestore.

Architecture:
  - System prompt lives in the agent (main.py) and is injected at call time
    via system_instruction — it is NOT cached.
  - Caches hold ONLY topic-scoped KB content, keeping them lean and cheap.
  - Adding a new topic = add one KB file + one TOPIC_FILES entry.

Usage:
  python cache_setup.py

For scheduled rebuilds, use the refresh-cache Cloud Function instead.
"""

import os
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
        print(f"[cache_setup] {topic}: KB file '{filename}' not found "
              f"in '{KB_DIR}' — skipping.")
        return None

    if not kb.strip():
        print(f"[cache_setup] {topic}: KB file '{filename}' is EMPTY — skipping.")
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
        print(f"[cache_setup] {topic} cache created: {cache.name} "
              f"(KB size: {size_kb} KB)")
        return cache.name
    except Exception as e:
        print(f"[cache_setup] {topic} CACHE FAILED — reason: {str(e)[:300]}")
        return None


def create_site_cache(event=None, context=None):
    """Build all per-topic caches and store the CacheMap in Firestore."""
    print(f"[cache_setup] Building caches from KB_DIR='{KB_DIR}' "
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
        print(f"[cache_setup] Stored CacheMap with {len(cache_map)}/"
              f"{len(TOPIC_FILES)} topic caches: {list(cache_map.keys())}")

        missing = [t for t in TOPIC_FILES if t not in cache_map]
        if missing:
            print(f"[cache_setup] WARNING: {len(missing)} topic(s) NOT cached: "
                  f"{missing} — check filenames in '{KB_DIR}'.")
    else:
        print("[cache_setup] No caches created — agent falls back to inline context.")

    return cache_map


if __name__ == "__main__":
    create_site_cache()
