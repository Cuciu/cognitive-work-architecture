import os
import functions_framework
from google import genai
from google.genai import types
from google.cloud import firestore

PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION = os.environ.get("REGION", "global")
ROUTER_MODEL = os.environ.get("MODEL", "gemini-3.5-flash-lite")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db = firestore.Client()

# ---------------------------------------------------------------------------
# refresh-cache does ONE job: cache global.md (the router/voice).
# ---------------------------------------------------------------------------
# global.md = router + master index + voice/rules. Used on EVERY turn, small,
# stable -> cached WHOLE (never chunked). This is a genuine cache win.
#
# Knowledge retrieval (the practice KBs) is NOT handled here anymore. The
# corpus lives in sales-agent/knowledge and is vector-indexed + queried there.
# No more per-topic context caches of full KBs (that was the token-hour leak).
#
# refresh-cache/knowledge/ holds ONLY global.md.
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.environ.get("KB_DIR", os.path.join(_HERE, "knowledge"))
GLOBAL_FILE = os.environ.get("GLOBAL_FILE", "global.md")
ROUTER_CACHE_TTL = os.environ.get("ROUTER_CACHE_TTL", "3024000s")  # 35 days (31d cycle + buffer)


def _load_global() -> str:
    with open(os.path.join(KB_DIR, GLOBAL_FILE), encoding="utf-8") as f:
        return f.read()


@functions_framework.cloud_event
def create_site_cache(cloud_event):
    """
    Scheduled refresh of the ROUTER cache: global.md cached whole.
    Writes the cache name to Firestore (AgentConfig/SalesAgent.RouterCache) so
    the agent injects it at call time. Function name kept for trigger compat.
    """
    print(f"[refresh-cache] Refreshing router cache from "
          f"KB_DIR='{KB_DIR}', file='{GLOBAL_FILE}'")

    try:
        global_md = _load_global()
    except FileNotFoundError:
        print(f"[refresh-cache] ❌ '{GLOBAL_FILE}' not found in '{KB_DIR}' — "
              f"router cache NOT refreshed. Agent must inline global.md.")
        return

    if not global_md.strip():
        print(f"[refresh-cache] ❌ '{GLOBAL_FILE}' is EMPTY — skipping.")
        return

    try:
        cache = client.caches.create(
            model=ROUTER_MODEL,
            config=types.CreateCachedContentConfig(
                display_name="global_router_cache",
                contents=[global_md],   # WHOLE router/voice — never chunked
                ttl=ROUTER_CACHE_TTL,
            ),
        )
    except Exception as e:
        print(f"[refresh-cache] ❌ ROUTER CACHE FAILED — {str(e)[:300]}")
        return

    size_kb = round(len(global_md.encode("utf-8")) / 1024, 1)
    db.collection("AgentConfig").document("SalesAgent").set(
        {
            "RouterCache": {
                "name": cache.name,
                "source": GLOBAL_FILE,
                "model": ROUTER_MODEL,
                "lastRefresh": firestore.SERVER_TIMESTAMP,
            }
        },
        merge=True,
    )
    print(f"[refresh-cache] ✅ Router cache refreshed: {cache.name} "
          f"(global.md {size_kb} KB — whole, not chunked)")
