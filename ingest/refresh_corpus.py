"""Rebuild the Groww corpus from corpus_manifest.json into a staging tree.

Live `data/processed` is replaced only after fetch, chunk, embed, and smoke
search all succeed. A failed run leaves the previous files in place.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.atomic import replace_directory
from ingest.chunk import ChunkError, chunk_corpus
from ingest.embed_index import (
    EMBEDDING_DIM,
    EmbedError,
    embed_corpus,
    forget_chroma_client,
    persist_index,
    smoke_search,
)
from ingest.fetch_official import Downloader, FetchError, fetch_corpus
from ingest.normalize import DEFAULT_PROCESSED_DIR, NormalizeError, normalize_corpus
from ingest.validate_manifest import (
    DEFAULT_MANIFEST,
    ManifestError,
    load_manifest,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGING_ROOT = ROOT / "data" / ".ingest-staging"

DownloadFn = Downloader


class RefreshError(ValueError):
    """The staged corpus is not safe to swap onto live processed files."""


@dataclass(frozen=True)
class RefreshResult:
    document_count: int
    chunk_count: int
    embedding_count: int
    as_of_date: str
    swapped: bool
    processed_dir: Path


def utc_as_of_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def stamp_manifest(manifest: dict, as_of_date: str) -> dict:
    stamped = copy.deepcopy(manifest)
    for doc in stamped["documents"]:
        doc["as_of_date"] = as_of_date
    return stamped


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def assert_staged_corpus(
    manifest: dict,
    processed_dir: Path,
    chunks_path: Path,
    embeddings_path: Path,
    index_dir: Path,
    *,
    model: Any | None = None,
    skip_smoke: bool = False,
) -> tuple[int, int]:
    """Raise RefreshError unless the staged files are a complete replacement."""
    documents = manifest["documents"]
    expected_ids = [doc["doc_id"] for doc in documents]
    missing_text = [
        doc_id
        for doc_id in expected_ids
        if not (processed_dir / f"{doc_id}.txt").is_file()
        or not (processed_dir / f"{doc_id}.meta.json").is_file()
    ]
    if missing_text:
        raise RefreshError("normalized files missing for: " + ", ".join(missing_text))

    chunks = load_jsonl(chunks_path)
    if len(chunks) < len(documents):
        raise RefreshError(
            f"need at least {len(documents)} chunks, staged {len(chunks)}"
        )
    chunk_docs = {item.get("doc_id") for item in chunks}
    missing_chunks = [doc_id for doc_id in expected_ids if doc_id not in chunk_docs]
    if missing_chunks:
        raise RefreshError("no chunks for: " + ", ".join(missing_chunks))
    for item in chunks:
        url = item.get("source_url")
        if not isinstance(url, str) or "groww.in" not in url:
            raise RefreshError(f"chunk {item.get('chunk_id')} is missing a Groww URL")

    pairs = load_jsonl(embeddings_path)
    if len(pairs) != len(chunks):
        raise RefreshError(f"embedded {len(pairs)} vectors for {len(chunks)} chunks")
    for index, record in enumerate(pairs, start=1):
        embedding = record.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != EMBEDDING_DIM:
            raise RefreshError(f"embeddings.jsonl:{index} has the wrong vector size")
        chunk = record.get("chunk")
        if not isinstance(chunk, dict) or not chunk.get("text"):
            raise RefreshError(f"embeddings.jsonl:{index} is missing chunk text")

    if not skip_smoke:
        smoke_search(index_dir, model=model)
    return len(chunks), len(pairs)


def refresh_corpus(
    manifest_path: Path | None = None,
    *,
    live_processed: Path | None = None,
    live_manifest: Path | None = None,
    staging_root: Path | None = None,
    download: DownloadFn | None = None,
    delay_s: float = 0.75,
    model: Any | None = None,
    as_of_date: str | None = None,
    dry_run: bool = False,
    skip_smoke: bool = False,
    keep_staging: bool = False,
) -> RefreshResult:
    """Fetch Groww pages into staging, rebuild chunks/embeddings, then swap live files."""
    source_manifest = Path(manifest_path or DEFAULT_MANIFEST)
    live_processed_dir = Path(live_processed or DEFAULT_PROCESSED_DIR)
    live_manifest_path = Path(live_manifest or source_manifest)
    stage = Path(staging_root or DEFAULT_STAGING_ROOT)
    as_of = as_of_date or utc_as_of_date()

    manifest = load_manifest(source_manifest)
    validate_manifest(manifest)
    stamped = stamp_manifest(manifest, as_of)

    raw_dir = stage / "raw"
    processed_dir = stage / "processed"
    index_dir = stage / "index"
    chunks_path = processed_dir / "chunks.jsonl"
    embeddings_path = processed_dir / "embeddings.jsonl"
    staged_manifest = stage / "corpus_manifest.json"

    _clear_dir(stage)
    write_json(staged_manifest, stamped)
    fetch_kwargs: dict[str, Any] = {"delay_s": delay_s}
    if download is not None:
        fetch_kwargs["download"] = download
    results = fetch_corpus(staged_manifest, raw_dir, **fetch_kwargs)
    if len(results) != len(stamped["documents"]):
        raise RefreshError(
            f"fetched {len(results)} documents, manifest has {len(stamped['documents'])}"
        )

    normalize_corpus(staged_manifest, raw_dir, processed_dir)
    chunk_corpus(staged_manifest, processed_dir, chunks_path)
    embed_corpus(chunks_path, embeddings_path, model=model)
    persist_index(embeddings_path, index_dir)
    chunk_count, embedding_count = assert_staged_corpus(
        stamped,
        processed_dir,
        chunks_path,
        embeddings_path,
        index_dir,
        model=model,
        skip_smoke=skip_smoke,
    )
    (processed_dir / ".gitkeep").write_text("", encoding="utf-8")

    swapped = False
    if not dry_run:
        incoming = live_processed_dir.with_name(live_processed_dir.name + ".next")
        if incoming.exists():
            shutil.rmtree(incoming)
        shutil.copytree(processed_dir, incoming)
        replace_directory(live_processed_dir, incoming)
        write_json(live_manifest_path, stamped)
        swapped = True

    if swapped and not keep_staging:
        forget_chroma_client(index_dir)
        shutil.rmtree(stage, ignore_errors=True)

    return RefreshResult(
        document_count=len(stamped["documents"]),
        chunk_count=chunk_count,
        embedding_count=embedding_count,
        as_of_date=as_of,
        swapped=swapped,
        processed_dir=live_processed_dir if swapped else processed_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Groww URLs from corpus_manifest.json and rebuild chunks + embeddings. "
            "Live files are replaced only after the staged corpus passes checks."
        )
    )
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--staging-dir", type=Path, default=DEFAULT_STAGING_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate staging only. Do not replace data/processed.",
    )
    parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="Leave data/.ingest-staging/ after a successful swap.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the Large Cap expense-ratio retrieval check.",
    )
    parser.add_argument(
        "--as-of-date",
        metavar="YYYY-MM-DD",
        help="Stamp this date on sidecars (default: UTC today).",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        result = refresh_corpus(
            args.manifest,
            live_processed=args.processed_dir,
            live_manifest=args.manifest,
            staging_root=args.staging_dir,
            dry_run=args.dry_run,
            skip_smoke=args.skip_smoke,
            keep_staging=args.keep_staging,
            as_of_date=args.as_of_date,
        )
    except (
        OSError,
        json.JSONDecodeError,
        ManifestError,
        FetchError,
        NormalizeError,
        ChunkError,
        EmbedError,
        RefreshError,
    ) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    action = "staged only" if args.dry_run else f"replaced {_rel(result.processed_dir)}"
    print(
        f"OK refresh {action}: docs={result.document_count} "
        f"chunks={result.chunk_count} embeddings={result.embedding_count} "
        f"as_of={result.as_of_date}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
