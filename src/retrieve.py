"""Resolve scheme/topic from the query and search official Groww chunks.

Uses the same MiniLM model as Phase 2D and the Chroma store from Phase 2E.
Does not call Gemini. Policy refusals stay in src/generate.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ingest.embed_index import DEFAULT_INDEX_DIR, embed_texts, load_model, open_collection
from ingest.validate_manifest import ManifestError, validate_url
from src.guard import GuardDecision, classify, truncate_query
from src.schemes import CATALOG_SCHEME_IDS
from src.timing import Stopwatch, skip_if, span_if

DEFAULT_K = 5
MIN_K = 3
MAX_K = 5


class RetrieveError(ValueError):
    """The Chroma index could not be searched."""


def clamp_k(k: int) -> int:
    return max(MIN_K, min(MAX_K, int(k)))


def parse_topic_tags(raw: object) -> list[str]:
    """Chroma stores topic_tags as a comma-separated string."""
    if isinstance(raw, list):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def is_official_hit(source_url: object) -> bool:
    """True when the hit cites a Groww page. Sourceless and other hosts are dropped."""
    if not isinstance(source_url, str) or not source_url.strip():
        return False
    try:
        validate_url(source_url)
    except ManifestError:
        return False
    host = (urlparse(source_url).hostname or "").lower().rstrip(".")
    return host == "groww.in" or host.endswith(".groww.in")


def resolve_filters(query: str, decision: GuardDecision | None = None) -> tuple[list[str] | None, str | None]:
    """Return (scheme_id values for the Chroma where-clause, topic)."""
    routed = decision if decision is not None else classify(query)
    topic = routed.topic
    if routed.intent == "process":
        return ["generic"], topic or "statements"
    if routed.intent == "catalog":
        return list(CATALOG_SCHEME_IDS), topic
    if routed.scheme_id and routed.scheme_id != "generic":
        return [routed.scheme_id], topic
    if routed.intent == "out_of_scope":
        return None, topic
    if routed.intent == "incomplete" and topic:
        return ["generic"], topic
    return None, topic


def chroma_where(scheme_ids: list[str] | None) -> dict | None:
    if not scheme_ids:
        return None
    if len(scheme_ids) == 1:
        return {"scheme_id": scheme_ids[0]}
    return {"scheme_id": {"$in": list(scheme_ids)}}


def _query_collection(
    collection: Any,
    vector: list[float],
    n_results: int,
    where: dict | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "query_embeddings": [vector],
        "n_results": max(1, n_results),
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    return collection.query(**kwargs)


def _unpack_hits(raw: Any) -> list[dict]:
    ids = (raw.get("ids") or [[]])[0]
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    hits: list[dict] = []
    for index, chunk_id in enumerate(ids):
        meta = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
        source_url = meta.get("source_url")
        if not is_official_hit(source_url):
            continue
        text = documents[index] if index < len(documents) else ""
        if not isinstance(text, str) or not text.strip():
            continue
        distance = distances[index] if index < len(distances) else None
        hits.append(
            {
                "chunk_id": str(chunk_id),
                "text": text,
                "distance": float(distance) if distance is not None else None,
                "scheme_id": meta.get("scheme_id") or None,
                "topic_tags": parse_topic_tags(meta.get("topic_tags")),
                "source_url": str(source_url),
                "as_of_date": meta.get("as_of_date") or None,
                "doc_type": meta.get("doc_type") or None,
                "source_title": meta.get("source_title") or None,
            }
        )
    return hits


def _prefer_topic(hits: list[dict], topic: str | None) -> list[dict]:
    if not topic or not hits:
        return hits
    matched = [hit for hit in hits if topic in (hit.get("topic_tags") or [])]
    return matched or hits


def retrieve(
    query: str,
    *,
    k: int = DEFAULT_K,
    decision: GuardDecision | None = None,
    index_dir: Path | None = None,
    model: Any | None = None,
    query_vector: list[float] | None = None,
    watch: Stopwatch | None = None,
) -> list[dict]:
    """Return up to k official Groww chunks, or [] when nothing is relevant.

    Metadata-filters Chroma by the resolved scheme_id (process questions use
    generic). Unofficial and sourceless hits are discarded.
    """
    routed = decision if decision is not None else classify(query)
    if not routed.allow_retrieve:
        return []

    scheme_ids, topic = resolve_filters(query, routed)
    if routed.intent == "out_of_scope":
        return []

    text = truncate_query(query if isinstance(query, str) else "").strip()
    if not text:
        return []

    folder = index_dir or DEFAULT_INDEX_DIR
    try:
        with span_if(watch, "chroma_open", "Open Chroma collection", "retrieve"):
            collection = open_collection(folder, reset=False)
            count = int(collection.count())
    except Exception as exc:
        raise RetrieveError(f"cannot open Chroma index at {folder}: {exc}") from exc
    if count <= 0:
        return []

    try:
        vector = _query_vector(text, model, query_vector, watch)
    except Exception as exc:
        raise RetrieveError(f"cannot embed query: {exc}") from exc

    if routed.intent == "catalog":
        with span_if(
            watch,
            "chroma_search",
            "Vector search",
            "retrieve",
            "one filtered search per in-scope scheme",
        ):
            return _retrieve_each_scheme(collection, vector, topic, count)

    limit = clamp_k(k)
    where = chroma_where(scheme_ids)
    n_results = min(limit, count)
    try:
        with span_if(
            watch,
            "chroma_search",
            "Vector search",
            "retrieve",
            f"k={n_results}",
        ):
            raw = _query_collection(collection, vector, n_results, where)
    except Exception:
        return []
    hits = _prefer_topic(_unpack_hits(raw), topic)
    return hits[:limit]


def _query_vector(
    text: str,
    model: Any | None,
    query_vector: list[float] | None,
    watch: Stopwatch | None,
) -> list[float]:
    if query_vector is not None:
        skip_if(watch, "minilm_load", "MiniLM encoder load", "retrieve", "precomputed vector")
        skip_if(watch, "query_embed", "Query embedding", "retrieve", "precomputed vector")
        return query_vector
    cached = model is not None or load_model.cache_info().currsize > 0
    if watch is not None:
        watch.meta["encoder_cached"] = cached
    if cached:
        skip_if(
            watch,
            "minilm_load",
            "MiniLM encoder load",
            "retrieve",
            "already in process memory",
        )
    else:
        with span_if(
            watch,
            "minilm_load",
            "MiniLM encoder load",
            "retrieve",
            "all-MiniLM-L6-v2 first load in this process",
        ):
            load_model()
    with span_if(watch, "query_embed", "Query embedding", "retrieve"):
        return embed_texts([text], model)[0]


def _retrieve_each_scheme(
    collection: Any,
    vector: list[float],
    topic: str | None,
    count: int,
) -> list[dict]:
    """One official Groww hit per in-scope scheme, topic-preferred."""
    hits: list[dict] = []
    seen: set[str] = set()
    n_results = min(3, max(1, count))
    for scheme_id in CATALOG_SCHEME_IDS:
        try:
            raw = _query_collection(
                collection,
                vector,
                n_results,
                chroma_where([scheme_id]),
            )
        except Exception:
            continue
        scheme_hits = _prefer_topic(_unpack_hits(raw), topic)
        if not scheme_hits:
            continue
        chosen = scheme_hits[0]
        chosen_id = str(chosen.get("scheme_id") or scheme_id)
        if chosen_id in seen:
            continue
        seen.add(chosen_id)
        hits.append(chosen)
    return hits


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Search the local Groww Chroma index. Does not call Gemini."
    )
    parser.add_argument("query", help="User question")
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="How many chunks (3–5).")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    decision = classify(args.query)
    print(f"intent={decision.intent}")
    print(f"scheme_id={decision.scheme_id}")
    print(f"topic={decision.topic}")
    print(f"allow_retrieve={decision.allow_retrieve}")
    try:
        hits = retrieve(args.query, k=args.k, decision=decision, index_dir=args.index_dir)
    except (OSError, RetrieveError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"hits={len(hits)}")
    if not hits:
        print("  (no relevant official Groww chunks)")
        return 0
    for rank, hit in enumerate(hits, start=1):
        snippet = " ".join(str(hit.get("text") or "").split())
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        print(
            f"  {rank}. {hit.get('doc_type')}  {hit.get('scheme_id')}  "
            f"{hit.get('chunk_id')}  dist={hit.get('distance')}"
        )
        print(f"     {hit['source_url']}")
        print(f"     {snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
