"""Show a query, its MiniLM vector, and the matching Chroma chunks.

Does not call Gemini. Usage:
  python scripts/show_retrievals.py "ELSS lock-in"
  python scripts/show_retrievals.py "Large Cap expense ratio" -k 3 --full-vectors
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.embed_index import DEFAULT_INDEX_DIR, EmbedError, inspect_retrieval

TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?", re.I)


def query_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in TOKEN_RE.finditer(query.lower()):
        token = match.group(0)
        parts = [token, *token.split("-")]
        for part in parts:
            if len(part) < 3 or part in seen:
                continue
            seen.add(part)
            tokens.append(part)
    return tokens


def matching_lines(query: str, text: str) -> list[str]:
    """Lines in the chunk that share a query token (ELSS, lock-in, …)."""
    tokens = query_tokens(query)
    if not tokens:
        return []
    hits: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(token in lowered for token in tokens):
            hits.append(line)
    return hits


def format_vector(values: list[float] | None, *, preview: int, full: bool) -> str:
    if not values:
        return "(missing vector)"
    if full or preview <= 0 or preview >= len(values):
        body = ", ".join(f"{value:.6f}" for value in values)
        return f"dim={len(values)} [{body}]"
    head = ", ".join(f"{value:.6f}" for value in values[:preview])
    tail = ", ".join(f"{value:.6f}" for value in values[-3:])
    return f"dim={len(values)} [{head}, ..., {tail}]"


def _distance_and_similarity(hit: dict) -> tuple[str, str]:
    raw = hit.get("distance")
    if raw is None:
        return "n/a", "n/a"
    distance = float(raw)
    return f"{distance:.6f}", f"{1.0 - distance:.6f}"


def render_inspect(payload: dict, *, preview: int, full_vectors: bool) -> str:
    lines = [
        f"QUERY: {payload['query']}",
        f"MODEL: {payload['model']}  dim={payload['dim']}",
        f"SCHEME FILTER: {payload['scheme_filter'] or '(none)'}",
        f"QUERY VECTOR: {format_vector(payload['query_vector'], preview=preview, full=full_vectors)}",
        f"HITS: {len(payload['hits'])}",
    ]
    for rank, hit in enumerate(payload["hits"], start=1):
        distance, similarity = _distance_and_similarity(hit)
        lines.extend(
            [
                "",
                "=" * 72,
                f"HIT {rank}",
                f"  distance     : {distance}",
                f"  similarity   : {similarity}",
                f"  chunk_id     : {hit.get('chunk_id')}",
                f"  scheme_id    : {hit.get('scheme_id')}",
                f"  doc_type     : {hit.get('doc_type')}",
                f"  topic_tags   : {hit.get('topic_tags')}",
                f"  source_url   : {hit.get('source_url')}",
                f"  chunk vector : {format_vector(hit.get('embedding'), preview=preview, full=full_vectors)}",
                "  matching lines:",
            ]
        )
        matched = matching_lines(str(payload["query"]), str(hit.get("text") or ""))
        if matched:
            lines.extend(f"    >> {line}" for line in matched)
        else:
            lines.append("    (no shared query tokens in a line)")
        lines.append("  FULL CHUNK:")
        body = str(hit.get("text") or "").rstrip() or "(empty)"
        lines.extend(f"    {line}" if line else "    " for line in body.splitlines())
    return "\n".join(lines) + "\n"


def _json_ready(payload: dict) -> dict:
    hits = []
    for hit in payload["hits"]:
        item = dict(hit)
        raw = item.get("distance")
        if raw is not None:
            item["distance"] = float(raw)
            item["similarity"] = 1.0 - float(raw)
        item["matching_lines"] = matching_lines(
            str(payload["query"]), str(item.get("text") or "")
        )
        hits.append(item)
    return {**payload, "hits": hits}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Inspect Chroma retrievals: query vector, chunk vectors, matching text."
    )
    parser.add_argument("query", help="Question to embed and search. Gemini is not used.")
    parser.add_argument("-k", type=int, default=3, help="How many nearest chunks (default: 3).")
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument(
        "--vector-preview",
        type=int,
        default=8,
        help="How many leading floats to print (default: 8).",
    )
    parser.add_argument(
        "--full-vectors",
        action="store_true",
        help="Print every float in the query and chunk vectors.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object instead of the text report.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        payload = inspect_retrieval(args.query, args.index_dir, n_results=max(1, args.k))
    except (OSError, EmbedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2))
        return 0
    print(
        render_inspect(
            payload,
            preview=max(1, args.vector_preview),
            full_vectors=args.full_vectors,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
