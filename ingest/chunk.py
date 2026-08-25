"""Split normalized Groww text into overlapping chunks. Phase 2C — no embeddings."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.normalize import DEFAULT_PROCESSED_DIR, SIDECAR_FIELDS, processed_paths
from ingest.validate_manifest import (
    DEFAULT_MANIFEST,
    ManifestError,
    load_manifest,
    validate_manifest,
    validate_url,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNKS_PATH = DEFAULT_PROCESSED_DIR / "chunks.jsonl"

# Word-count stand-in for tokens. MiniLM is not used here.
TARGET_CHUNK_TOKENS = 650
MAX_CHUNK_TOKENS = 800
OVERLAP_TOKENS = 100
LONG_UNIT_TOKENS = 80

SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])")
GLOSSARY_HEADING_RE = re.compile(r"^Understand terms$", re.I)
ABOUT_HEADING_RE = re.compile(r"^About$", re.I)

CHUNK_TEXT_FIELDS = SIDECAR_FIELDS


class ChunkError(ValueError):
    """Normalized text could not be chunked."""


def token_count(text: str) -> int:
    return len(text.split())


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]
    return parts or [text.strip()]


def split_units(text: str) -> list[str]:
    """Break a page into lines, then sentences when a line is long."""
    units: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if token_count(line) <= LONG_UNIT_TOKENS:
            units.append(line)
            continue
        units.extend(_split_sentences(line))
    return units


def _hard_split(text: str, max_tokens: int) -> list[str]:
    words = text.split()
    return [
        " ".join(words[index : index + max_tokens])
        for index in range(0, len(words), max_tokens)
    ]


def _overlap_start(units: list[str], start: int, end: int, overlap: int) -> int:
    total = 0
    index = end
    while index > start and total < overlap:
        index -= 1
        total += token_count(units[index])
    if index <= start:
        return start + 1 if start + 1 < end else end
    return index


def chunk_text(text: str) -> list[str]:
    """Split text into ~500–800 token windows with ~80–120 token overlap."""
    raw_units = split_units(text)
    units: list[str] = []
    for unit in raw_units:
        if token_count(unit) > MAX_CHUNK_TOKENS:
            units.extend(_hard_split(unit, MAX_CHUNK_TOKENS))
        else:
            units.append(unit)
    if not units:
        return []

    joined = "\n".join(units)
    if token_count(joined) <= MAX_CHUNK_TOKENS:
        return [joined]

    chunks: list[str] = []
    index = 0
    while index < len(units):
        window: list[str] = []
        tokens = 0
        cursor = index
        while cursor < len(units):
            piece = token_count(units[cursor])
            if window and tokens + piece > MAX_CHUNK_TOKENS:
                break
            if window and tokens >= TARGET_CHUNK_TOKENS:
                break
            window.append(units[cursor])
            tokens += piece
            cursor += 1
        chunks.append("\n".join(window).strip())
        if cursor >= len(units):
            break
        next_index = _overlap_start(units, index, cursor, OVERLAP_TOKENS)
        index = next_index if next_index > index else cursor
    return [chunk for chunk in chunks if chunk]


def load_sidecar(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ChunkError(f"invalid sidecar JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ChunkError(f"sidecar must be an object: {path}")
    missing = [field for field in CHUNK_TEXT_FIELDS if field not in data]
    if missing:
        raise ChunkError(f"{path}: sidecar missing fields: {missing}")
    return data


def attach_metadata(text: str, meta: dict, *, index: int) -> dict | None:
    """Copy sidecar fields onto one chunk. Drop unofficial or sourceless pieces."""
    source_url = meta.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        return None
    try:
        validate_url(source_url)
    except ManifestError:
        return None
    body = text.strip()
    if not body:
        return None
    doc_id = str(meta["doc_id"])
    return {
        "chunk_id": f"{doc_id}:{index:04d}",
        "doc_id": doc_id,
        "text": body,
        "scheme_id": meta["scheme_id"],
        "doc_type": meta["doc_type"],
        "source_url": source_url,
        "source_title": meta["source_title"],
        "as_of_date": meta["as_of_date"],
        "topic_tags": list(meta["topic_tags"]),
    }


def scheme_page_sections(text: str) -> list[str]:
    """Keep the fact header and About blurb. Drop Groww's shared glossary.

    That glossary is the same on every scheme page and reads like a primer, so
    a query such as "Large Cap expense ratio" would otherwise match education
    text instead of the scheme factsheet.
    """
    facts: list[str] = []
    about: list[str] = []
    section = "facts"
    for raw in text.splitlines():
        line = raw.strip()
        if GLOSSARY_HEADING_RE.match(line):
            section = "glossary"
            continue
        if ABOUT_HEADING_RE.match(line):
            section = "about"
        if not line:
            continue
        if section == "facts":
            facts.append(line)
        elif section == "about":
            about.append(line)
    parts = ["\n".join(facts).strip(), "\n".join(about).strip()]
    return [part for part in parts if part]


def chunk_sources(text: str, meta: dict) -> list[str]:
    """Choose the strings that become chunks. Scheme pages are split first."""
    if meta.get("doc_type") == "groww_scheme":
        sections = scheme_page_sections(text)
        if sections:
            pieces: list[str] = []
            for section in sections:
                pieces.extend(chunk_text(section))
            return pieces
    return chunk_text(text)


def chunk_document(text: str, meta: dict) -> list[dict]:
    """Turn one normalized page into metadata-stamped chunks."""
    source_url = meta.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        return []
    try:
        validate_url(source_url)
    except ManifestError:
        return []
    records: list[dict] = []
    for index, piece in enumerate(chunk_sources(text, meta)):
        record = attach_metadata(piece, meta, index=index)
        if record is not None:
            records.append(record)
    return records


def chunk_corpus(
    manifest_path: Path | None = None,
    processed_dir: Path | None = None,
    chunks_path: Path | None = None,
) -> list[dict]:
    """Read every processed page and write data/processed/chunks.jsonl."""
    path = manifest_path or DEFAULT_MANIFEST
    manifest = load_manifest(path)
    validate_manifest(manifest)

    folder = processed_dir or DEFAULT_PROCESSED_DIR
    records: list[dict] = []
    for doc in manifest["documents"]:
        text_path, meta_path = processed_paths(doc["doc_id"], folder)
        if not text_path.is_file():
            raise ChunkError(f"{doc['doc_id']}: missing normalized text {text_path}")
        if not meta_path.is_file():
            raise ChunkError(f"{doc['doc_id']}: missing sidecar {meta_path}")
        text = text_path.read_text(encoding="utf-8")
        meta = load_sidecar(meta_path)
        records.extend(chunk_document(text, meta))

    dest = chunks_path or DEFAULT_CHUNKS_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return records


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Chunk normalized Groww text into data/processed/chunks.jsonl (Phase 2C)."
    )
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--chunks-path", type=Path, default=DEFAULT_CHUNKS_PATH)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        records = chunk_corpus(args.manifest, args.processed_dir, args.chunks_path)
    except (OSError, json.JSONDecodeError, ManifestError, ChunkError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    docs = {item["doc_id"] for item in records}
    print(
        f"OK wrote {len(records)} chunk(s) from {len(docs)} document(s) -> {_rel(args.chunks_path)}"
    )
    for item in records:
        words = token_count(item["text"])
        print(f"  {item['chunk_id']}: {item['scheme_id']} ({words} words) {item['source_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
