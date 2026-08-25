"""Rebuild chunks, MiniLM pairs, and the local Chroma index (Phases 2C–2E)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.chunk import DEFAULT_CHUNKS_PATH, ChunkError, chunk_corpus
from ingest.embed_index import (
    DEFAULT_EMBEDDINGS_PATH,
    DEFAULT_INDEX_DIR,
    SMOKE_QUERY,
    SMOKE_SOURCE_URL,
    EmbedError,
    embed_corpus,
    persist_index,
    query_index,
    smoke_search,
)
from ingest.normalize import DEFAULT_PROCESSED_DIR
from ingest.validate_manifest import DEFAULT_MANIFEST, ManifestError

ROOT = Path(__file__).resolve().parents[1]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chunk, embed, and persist the Groww corpus into data/index/."
    )
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--chunks-path", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--embeddings-path", type=Path, default=DEFAULT_EMBEDDINGS_PATH)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Persist the index without running the Large Cap expense-ratio check.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        records = chunk_corpus(args.manifest, args.processed_dir, args.chunks_path)
        print(f"OK 2C wrote {len(records)} chunk(s) -> {_rel(args.chunks_path)}")
        pairs = embed_corpus(args.chunks_path, args.embeddings_path)
        print(f"OK 2D embedded {len(pairs)} vector(s) -> {_rel(args.embeddings_path)}")
        count, folder = persist_index(args.embeddings_path, args.index_dir)
        print(f"OK 2E stored {count} vector(s) in Chroma -> {_rel(folder)}")
        if not args.skip_smoke:
            hits = query_index(SMOKE_QUERY, args.index_dir, n_results=3)
            print(f"SMOKE query={SMOKE_QUERY!r}")
            for rank, hit in enumerate(hits, start=1):
                print(f"  {rank}. {hit['source_url']}  {hit.get('scheme_id')}")
            if not hits or hits[0]["source_url"] != SMOKE_SOURCE_URL:
                smoke_search(args.index_dir)
            print(f"OK top hit is the Large Cap scheme page: {hits[0]['source_url']}")
    except (OSError, json.JSONDecodeError, ManifestError, ChunkError, EmbedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
