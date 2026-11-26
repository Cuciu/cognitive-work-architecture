import os
import hashlib
from google import genai
from google.genai import types
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector

PROJECT_ID = os.environ["GCP_PROJECT"]
LOCATION = os.environ.get("REGION", "global")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-005")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
db = firestore.Client()

# ---------------------------------------------------------------------------
# ARCHITECTURE: topic-router + vector search
# ---------------------------------------------------------------------------
# WHY THIS DESIGN (vs per-topic context caches):
#   OLD: client.caches.create(contents=[kb], ttl=35d) per topic.
#        -> billed as "Text Input Caching Storage" by the TOKEN-HOUR, 24x7,
#           independent of query volume. Flat cost that grows with corpus size.
#   NEW: build a topic-namespaced VECTOR INDEX in Firestore from the KB files.
#        -> NO context caches. NO token-hour storage. Cost tracks USAGE:
#           embeddings only for NEW/CHANGED chunks (hash-keyed, incremental);
#           retrieval paid per query by the agent at CALL TIME.
#
# global.md is NOT indexed here. It is the ROUTER + voice/rules, cached WHOLE
# by the refresh-cache service and injected at call time via system_instruction.
# Chunking global.md would fragment the persona-routing table -> never do it.
#
# TOPIC_FILES keys MUST match the agent's router namespaces 1:1.
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.environ.get("KB_DIR", os.path.join(_HERE, "knowledge"))

# topic (namespace) -> KB filename. Practice KBs ONLY — global.md excluded.
# GENERAL has no KB: the greeter runs on system_instruction (global) alone.
#
# Replace these with your own practice areas and knowledge files.
# Keys must match the router topics defined in main.py's TOPIC_ROUTE.
TOPIC_FILES = {
    "TOPIC_A":  "topic-a.md",       # e.g., cloud strategy
    "TOPIC_B":  "topic-b.md",       # e.g., AI governance
    "TOPIC_C":  "topic-c.md",       # e.g., cost optimisation
    "TOPIC_D":  "topic-d.md",       # e.g., advisory services
    # Add as many topic -> file mappings as your agent needs.
    # Each file lives in the knowledge/ folder and is vector-indexed below.
}

# Firestore collection holding vector chunks. Each doc = one chunk.
VECTOR_COLLECTION = os.environ.get("VECTOR_COLLECTION", "KBVectors")

# Chunking controls (characters; simple + deterministic for reproducible IDs).
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "200"))


def _load_kb(filename: str) -> str:
    path = os.path.join(KB_DIR, filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _chunk_text(text: str) -> list[str]:
    """Deterministic, overlapping character chunks — keeps indexing reproducible."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks, start = [], 0
    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
    while start < len(text):
        chunks.append(text[start:start + CHUNK_SIZE])
        start += step
    return chunks


def _chunk_id(topic: str, content: str) -> str:
    """Content-addressed ID -> incremental, no-duplicate upserts."""
    h = hashlib.sha256(f"{topic}::{content}".encode("utf-8")).hexdigest()[:32]
    return f"{topic.lower()}_{h}"


def _embed(text: str) -> list[float]:
    """Embed a single chunk via Vertex. Billed per call (usage-based)."""
    resp = client.models.embed_content(model=EMBED_MODEL, contents=[text])
    return list(resp.embeddings[0].values)


def index_topic(topic: str, filename: str) -> dict | None:
    """
    Incrementally index one topic file into the vector collection.
    Returns {"indexed", "skipped", "pruned", "total"} or None on skip.
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

    chunks = _chunk_text(kb)
    col = db.collection(VECTOR_COLLECTION)
    n_new = n_existing = 0
    current_ids = set()

    for content in chunks:
        cid = _chunk_id(topic, content)
        current_ids.add(cid)
        doc_ref = col.document(cid)

        # Incremental: identical chunk == same content-addressed ID.
        # Only embed + write when the chunk does not already exist.
        if doc_ref.get().exists:
            n_existing += 1
            continue

        try:
            embedding = _embed(content)
        except Exception as e:
            print(f"[cache_setup] {topic}: EMBED FAILED for a chunk — "
                  f"{str(e)[:200]}")
            continue

        doc_ref.set({
            "topic": topic,
            "source": filename,
            "content": content,
            "embedding": Vector(embedding),
        })
        n_new += 1

    # Prune stale chunks for THIS topic whose content no longer exists
    # (handles edits/deletes -> prevents "memory drift": old facts lingering).
    pruned = 0
    stale_query = col.where(filter=firestore.FieldFilter("topic", "==", topic))
    for snap in stale_query.stream():
        if snap.id not in current_ids:
            snap.reference.delete()
            pruned += 1

    print(f"[cache_setup] {topic}: indexed {n_new} new, "
          f"skipped {n_existing} unchanged, pruned {pruned} stale "
          f"({len(chunks)} chunks total).")
    return {"indexed": n_new, "skipped": n_existing,
            "pruned": pruned, "total": len(chunks)}


def create_site_cache(event=None, context=None):
    """
    Build the topic-namespaced VECTOR INDEX from KB files and record metadata
    in Firestore. Name kept for deploy/trigger compatibility.
    Incremental + hash-keyed: unchanged chunks are NOT re-embedded, so adding
    knowledge files does NOT re-bill the whole corpus.
    Returns the list of indexed namespaces.
    """
    print(f"[cache_setup] Building vector index from KB_DIR='{KB_DIR}' "
          f"into '{VECTOR_COLLECTION}' for topics: {list(TOPIC_FILES.keys())}")

    summary = {}
    indexed = []
    for topic, filename in TOPIC_FILES.items():
        result = index_topic(topic, filename)
        if result:
            summary[topic] = result
            indexed.append(topic)

    # Record index metadata (freshness receipt) so the agent knows which
    # namespaces are queryable and when the index was last refreshed.
    db.collection("AgentConfig").document("SalesAgent").set(
        {
            "VectorIndex": {
                "collection": VECTOR_COLLECTION,
                "embedModel": EMBED_MODEL,
                "namespaces": indexed,
                "lastRefresh": firestore.SERVER_TIMESTAMP,
            }
        },
        merge=True,
    )

    if indexed:
        tn = sum(s["indexed"] for s in summary.values())
        ts = sum(s["skipped"] for s in summary.values())
        tp = sum(s["pruned"] for s in summary.values())
        print(f"[cache_setup] Vector index updated: "
              f"{len(indexed)}/{len(TOPIC_FILES)} namespaces | "
              f"{tn} new embedded, {ts} unchanged, {tp} pruned. "
              f"Namespaces: {indexed}")
        missing = [t for t in TOPIC_FILES if t not in indexed]
        if missing:
            print(f"[cache_setup] ⚠️ WARNING: {len(missing)} topic(s) NOT indexed: "
                  f"{missing} — check filenames in '{KB_DIR}'.")
    else:
        print("[cache_setup] No topics indexed — agent retrieval will be empty. "
              "Check KB_DIR contents and filenames.")

    return indexed


if __name__ == "__main__":
    create_site_cache()
