"""Embed chunk text with MiniLM (2D) and persist those vectors in Chroma (2E).

Gemini is not used here.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.chunk import DEFAULT_CHUNKS_PATH
from ingest.normalize import DEFAULT_PROCESSED_DIR
from ingest.validate_manifest import ManifestError, validate_url

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMBEDDINGS_PATH = DEFAULT_PROCESSED_DIR / "embeddings.jsonl"
DEFAULT_INDEX_DIR = ROOT / "data" / "index"
EMBEDDINGS_STAMP_NAME = ".embeddings_sha256"

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
WARMUP_TEXT = "expense ratio"
COLLECTION_NAME = "groww_chunks"
_LOGGER = logging.getLogger("ingest.embed_index")
_TORCH_TUNED = False
INDEX_METADATA_FIELDS = (
    "scheme_id",
    "topic_tags",
    "source_url",
    "as_of_date",
    "doc_type",
)
SMOKE_QUERY = "Large Cap expense ratio"
SMOKE_SOURCE_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


def embeddings_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def embeddings_stamp_path(index_dir: Path | None = None) -> Path:
    return (index_dir or DEFAULT_INDEX_DIR) / EMBEDDINGS_STAMP_NAME


def index_is_current(
    index_dir: Path | None = None,
    embeddings_path: Path | None = None,
) -> bool:
    """True when the Chroma folder was built from this embeddings.jsonl."""
    src = embeddings_path or DEFAULT_EMBEDDINGS_PATH
    if not src.is_file():
        return False
    stamp = embeddings_stamp_path(index_dir)
    if not stamp.is_file():
        return False
    try:
        return stamp.read_text(encoding="utf-8").strip() == embeddings_digest(src)
    except OSError:
        return False


def write_embeddings_stamp(embeddings_path: Path, index_dir: Path) -> None:
    embeddings_stamp_path(index_dir).write_text(
        embeddings_digest(embeddings_path) + "\n",
        encoding="utf-8",
    )


def forget_chroma_client(index_dir: Path) -> None:
    """Drop a cached PersistentClient so the folder can be replaced or rebuilt."""
    import gc

    key = str(Path(index_dir).resolve())
    _CLIENTS.pop(key, None)
    gc.collect()


# Query phrases → scheme_id. Used so a named scheme beats a Groww primer.
SCHEME_QUERY_HINTS = (
    ("large cap", "hdfc-large-cap-fund-direct-growth"),
    ("mid cap", "hdfc-mid-cap-fund-direct-growth"),
    ("small cap", "hdfc-small-cap-fund-direct-growth"),
    ("tax saver", "hdfc-elss-tax-saver-fund-direct-plan-growth"),
    ("elss", "hdfc-elss-tax-saver-fund-direct-plan-growth"),
    ("gold etf", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
    ("gold fof", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
)


class EmbedError(ValueError):
    """Chunks could not be embedded."""


@dataclass(frozen=True)
class EmbeddedChunk:
    """One MiniLM vector paired with the original chunk. Metadata is not rewritten."""

    vector: list[float]
    chunk: dict


def load_chunks(path: Path | None = None) -> list[dict]:
    """Read Phase 2C chunks.jsonl. Drop unofficial or empty pieces."""
    src = path or DEFAULT_CHUNKS_PATH
    if not src.is_file():
        raise EmbedError(f"missing chunks file: {src}")
    records: list[dict] = []
    for line_no, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EmbedError(f"{src}:{line_no}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise EmbedError(f"{src}:{line_no}: chunk must be an object")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise EmbedError(f"{src}:{line_no}: chunk is missing text")
        source_url = item.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            continue
        try:
            validate_url(source_url)
        except ManifestError:
            continue
        records.append(item)
    if not records:
        raise EmbedError(f"{src}: no embeddable Groww chunks")
    return records


def encoder_is_cached() -> bool:
    """True when this process already holds a MiniLM encoder."""
    return load_model.cache_info().currsize > 0


def should_warm_encoder() -> bool:
    """Skip warmup in the test runner so unit tests do not load MiniLM."""
    flag = os.environ.get("SKIP_ENCODER_WARMUP", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return False
    if flag in {"0", "false", "no"}:
        return True
    if any(name == "tests" or name.startswith("tests.") for name in sys.modules):
        return False
    if "pytest" in sys.modules:
        return False
    return True


def _hub_snapshot_exists(name: str) -> bool:
    slug = "models--" + name.replace("/", "--")
    homes: list[Path] = []
    for key in ("HF_HOME", "HUGGINGFACE_HUB_CACHE"):
        raw = os.environ.get(key, "").strip()
        if raw:
            homes.append(Path(raw))
    homes.append(Path.home() / ".cache" / "huggingface")
    for home in homes:
        candidates = (home / "hub" / slug, home / slug, home / "hub" / slug)
        if any(path.is_dir() for path in candidates):
            return True
    return False


def _tune_torch() -> None:
    global _TORCH_TUNED
    if _TORCH_TUNED:
        return
    try:
        import torch

        threads = max(1, min(4, os.cpu_count() or 1))
        torch.set_num_threads(threads)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        torch.set_grad_enabled(False)
    except Exception:
        return
    _TORCH_TUNED = True


def _build_encoder(source: str, *, local_only: bool) -> Any:
    from sentence_transformers import SentenceTransformer

    kwargs: dict[str, Any] = {"device": "cpu", "local_files_only": local_only}
    try:
        return SentenceTransformer(source, **kwargs)
    except TypeError:
        if local_only:
            return SentenceTransformer(source, local_files_only=True)
        return SentenceTransformer(source)


@functools.lru_cache(maxsize=4)
def load_model(name: str = MODEL_NAME) -> Any:
    """Load MiniLM once per process. Prefers a local Hugging Face snapshot."""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError as exc:
        raise EmbedError(
            "sentence-transformers is required for Phase 2D. "
            "Install it with: pip install sentence-transformers"
        ) from exc
    _tune_torch()
    cached = _hub_snapshot_exists(name)
    if cached:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        encoder = _build_encoder(name, local_only=True)
    except Exception:
        if cached:
            os.environ.pop("HF_HUB_OFFLINE", None)
        encoder = _build_encoder(name, local_only=False)
    try:
        encoder.eval()
    except Exception:
        pass
    return encoder


def warm_encoder(name: str = MODEL_NAME) -> Any:
    """Load MiniLM and run one encode so the first ask does not pay init cost."""
    encoder = load_model(name)
    embed_texts([WARMUP_TEXT], model=encoder)
    _LOGGER.info("MiniLM encoder ready (%s)", name)
    return encoder


def boot_retriever() -> bool:
    """Open Chroma and warm MiniLM in parallel. Returns whether the index has chunks."""
    if not should_warm_encoder():
        return bool(ensure_persisted_index())
    with ThreadPoolExecutor(max_workers=2) as pool:
        index_job = pool.submit(ensure_persisted_index)
        encoder_job = pool.submit(warm_encoder)
        encoder_job.result()
        return bool(index_job.result())


def _rows_from_encode(raw: Any) -> list[list[float]]:
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if not isinstance(raw, list) or not raw:
        raise EmbedError("encoder returned no vectors")
    rows: list[list[float]] = []
    for row in raw:
        values = [float(value) for value in row]
        if len(values) != EMBEDDING_DIM:
            raise EmbedError(f"expected dim {EMBEDDING_DIM}, got {len(values)}")
        rows.append(values)
    return rows


def embed_texts(texts: list[str], model: Any | None = None) -> list[list[float]]:
    """Turn strings into MiniLM vectors. Same model will encode questions in Phase 4."""
    if not texts:
        return []
    encoder = model if model is not None else load_model()
    inference = nullcontext()
    try:
        import torch

        inference = torch.inference_mode()
    except Exception:
        inference = nullcontext()
    with inference:
        raw = encoder.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=min(32, len(texts)),
        )
    rows = _rows_from_encode(raw)
    if len(rows) != len(texts):
        raise EmbedError(f"encoder returned {len(rows)} vectors for {len(texts)} texts")
    return rows


def embed_chunks(chunks: list[dict], model: Any | None = None) -> list[EmbeddedChunk]:
    """Pair each chunk with its vector. Does not add fields onto the chunk dict."""
    texts = [item["text"] for item in chunks]
    vectors = embed_texts(texts, model)
    return [
        EmbeddedChunk(vector=vector, chunk=dict(chunk))
        for vector, chunk in zip(vectors, chunks, strict=True)
    ]


def write_pairs(pairs: list[EmbeddedChunk], path: Path) -> None:
    """Write (vector, chunk) pairs so Phase 2D output is inspectable. Not a vector DB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for pair in pairs:
            record = {
                "dim": len(pair.vector),
                "embedding": pair.vector,
                "chunk": pair.chunk,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def embed_corpus(
    chunks_path: Path | None = None,
    embeddings_path: Path | None = None,
    model: Any | None = None,
) -> list[EmbeddedChunk]:
    """Load chunks.jsonl, embed each text, keep (vector, chunk) pairs."""
    chunks = load_chunks(chunks_path)
    pairs = embed_chunks(chunks, model)
    dest = embeddings_path or DEFAULT_EMBEDDINGS_PATH
    write_pairs(pairs, dest)
    return pairs


def load_pairs(path: Path | None = None) -> list[EmbeddedChunk]:
    """Read Phase 2D embeddings.jsonl. Metadata on each chunk is left as stored."""
    src = path or DEFAULT_EMBEDDINGS_PATH
    if not src.is_file():
        raise EmbedError(f"missing embeddings file: {src}")
    pairs: list[EmbeddedChunk] = []
    for line_no, raw in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EmbedError(f"{src}:{line_no}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise EmbedError(f"{src}:{line_no}: pair must be an object")
        embedding = record.get("embedding")
        chunk = record.get("chunk")
        if not isinstance(embedding, list) or not embedding:
            raise EmbedError(f"{src}:{line_no}: missing embedding")
        if not isinstance(chunk, dict):
            raise EmbedError(f"{src}:{line_no}: missing chunk")
        values = [float(value) for value in embedding]
        if len(values) != EMBEDDING_DIM:
            raise EmbedError(f"{src}:{line_no}: expected dim {EMBEDDING_DIM}, got {len(values)}")
        source_url = chunk.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            continue
        try:
            validate_url(source_url)
        except ManifestError:
            continue
        text = chunk.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise EmbedError(f"{src}:{line_no}: chunk is missing chunk_id")
        pairs.append(EmbeddedChunk(vector=values, chunk=dict(chunk)))
    if not pairs:
        raise EmbedError(f"{src}: no storeable Groww pairs")
    return pairs


def chroma_metadata(chunk: dict) -> dict[str, str]:
    """Chroma only stores scalars. topic_tags becomes a comma-separated string."""
    tags = chunk.get("topic_tags") or []
    if isinstance(tags, list):
        tags_value = ",".join(str(tag) for tag in tags)
    else:
        tags_value = str(tags)
    meta = {
        "scheme_id": str(chunk.get("scheme_id") or ""),
        "topic_tags": tags_value,
        "source_url": str(chunk.get("source_url") or ""),
        "as_of_date": str(chunk.get("as_of_date") or ""),
        "doc_type": str(chunk.get("doc_type") or ""),
    }
    if chunk.get("source_title"):
        meta["source_title"] = str(chunk["source_title"])
    if chunk.get("doc_id"):
        meta["doc_id"] = str(chunk["doc_id"])
    return meta


_CLIENTS: dict[str, Any] = {}


def _chroma_client(index_dir: Path) -> Any:
    try:
        import chromadb
    except ImportError as exc:
        raise EmbedError(
            "chromadb is required for Phase 2E. Install it with: pip install chromadb"
        ) from exc
    index_dir.mkdir(parents=True, exist_ok=True)
    key = str(index_dir.resolve())
    existing = _CLIENTS.get(key)
    if existing is not None:
        return existing
    try:
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=str(index_dir),
            settings=Settings(anonymized_telemetry=False),
        )
    except TypeError:
        client = chromadb.PersistentClient(path=str(index_dir))
    _CLIENTS[key] = client
    return client


def _collection_names(client: Any) -> set[str]:
    names: set[str] = set()
    listed = client.list_collections()
    for item in listed:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]))
        elif hasattr(item, "name"):
            names.add(str(item.name))
    return names


def open_collection(index_dir: Path | None = None, *, reset: bool = False) -> Any:
    """Open the local Groww collection. reset=True rebuilds it from scratch."""
    folder = index_dir or DEFAULT_INDEX_DIR
    client = _chroma_client(folder)
    if reset and COLLECTION_NAME in _collection_names(client):
        client.delete_collection(COLLECTION_NAME)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def persist_pairs(pairs: list[EmbeddedChunk], index_dir: Path | None = None) -> int:
    """Write vectors + chunk text + metadata under data/index/. Local Chroma only."""
    if not pairs:
        raise EmbedError("no pairs to persist")
    folder = index_dir or DEFAULT_INDEX_DIR
    collection = open_collection(folder, reset=True)
    collection.add(
        ids=[pair.chunk["chunk_id"] for pair in pairs],
        embeddings=[pair.vector for pair in pairs],
        documents=[pair.chunk["text"] for pair in pairs],
        metadatas=[chroma_metadata(pair.chunk) for pair in pairs],
    )
    return int(collection.count())


def persist_index(
    embeddings_path: Path | None = None,
    index_dir: Path | None = None,
) -> tuple[int, Path]:
    """Load embeddings.jsonl and persist the pairs in Chroma."""
    src = embeddings_path or DEFAULT_EMBEDDINGS_PATH
    pairs = load_pairs(src)
    folder = index_dir or DEFAULT_INDEX_DIR
    count = persist_pairs(pairs, folder)
    write_embeddings_stamp(src, folder)
    return count, folder


def ensure_persisted_index(
    embeddings_path: Path | None = None,
    index_dir: Path | None = None,
) -> bool:
    """Open or rebuild data/index/ from embeddings.jsonl.

    A missing stamp on an already-populated index is treated as current so a
    running API is not rebuilt (and is not renamed) on first boot. When the
    embeddings hash changes, Chroma is rewritten in place from embeddings.jsonl.
    """
    src = Path(embeddings_path or DEFAULT_EMBEDDINGS_PATH)
    folder = Path(index_dir or DEFAULT_INDEX_DIR)
    if not src.is_file():
        return False
    ready = False
    try:
        ready = int(open_collection(folder, reset=False).count()) > 0
    except Exception:
        ready = False
    if ready and index_is_current(folder, src):
        return True
    if ready and not embeddings_stamp_path(folder).is_file():
        write_embeddings_stamp(src, folder)
        return True
    forget_chroma_client(folder)
    persist_index(src, folder)
    try:
        return int(open_collection(folder, reset=False).count()) > 0
    except Exception:
        return False



def named_scheme_id(query: str) -> str | None:
    """Return a scheme_id when the question names one of the five Groww pages."""
    folded = " ".join(query.lower().replace("-", " ").split())
    for hint, scheme_id in SCHEME_QUERY_HINTS:
        if hint in folded:
            return scheme_id
    return None


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


def _as_floats(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if not isinstance(raw, list) or not raw:
        return None
    return [float(value) for value in raw]


def _embeddings_by_id(collection: Any, chunk_ids: list[str]) -> dict[str, list[float]]:
    if not chunk_ids:
        return {}
    raw = collection.get(ids=chunk_ids, include=["embeddings"])
    got_ids = raw.get("ids") or []
    got_embs = raw.get("embeddings")
    if got_embs is None:
        return {}
    if hasattr(got_embs, "tolist"):
        got_embs = got_embs.tolist()
    mapped: dict[str, list[float]] = {}
    for chunk_id, embedding in zip(got_ids, got_embs, strict=False):
        values = _as_floats(embedding)
        if values:
            mapped[str(chunk_id)] = values
    return mapped


def query_index(
    query: str,
    index_dir: Path | None = None,
    n_results: int = 5,
    model: Any | None = None,
    query_vector: list[float] | None = None,
    include_embeddings: bool = False,
) -> list[dict]:
    """Embed the question with MiniLM and return nearest Groww chunks.

    When the question names a scheme, filter on stored scheme_id so a primer
    about the same topic cannot outrank that scheme's factsheet.
    """
    text = query.strip()
    if not text:
        raise EmbedError("query is empty")
    vector = query_vector if query_vector is not None else embed_texts([text], model)[0]
    collection = open_collection(index_dir, reset=False)
    scheme_id = named_scheme_id(text)
    raw = _query_collection(
        collection,
        vector,
        n_results,
        {"scheme_id": scheme_id} if scheme_id else None,
    )
    if scheme_id and not (raw.get("ids") or [[]])[0]:
        raw = _query_collection(collection, vector, n_results)
    ids = (raw.get("ids") or [[]])[0]
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    stored = _embeddings_by_id(collection, [str(item) for item in ids]) if include_embeddings else {}
    hits: list[dict] = []
    for index, chunk_id in enumerate(ids):
        meta = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
        source_url = str(meta.get("source_url") or "")
        if not source_url:
            continue
        try:
            validate_url(source_url)
        except ManifestError:
            continue
        record = {
            "chunk_id": chunk_id,
            "text": documents[index] if index < len(documents) else "",
            "distance": distances[index] if index < len(distances) else None,
            "scheme_id": meta.get("scheme_id"),
            "topic_tags": meta.get("topic_tags"),
            "source_url": source_url,
            "as_of_date": meta.get("as_of_date"),
            "doc_type": meta.get("doc_type"),
            "source_title": meta.get("source_title"),
        }
        if include_embeddings:
            record["embedding"] = stored.get(str(chunk_id))
        hits.append(record)
    return hits


def inspect_retrieval(
    query: str,
    index_dir: Path | None = None,
    n_results: int = 5,
    model: Any | None = None,
) -> dict:
    """Return the query vector plus each hit's stored vector and chunk text."""
    text = query.strip()
    if not text:
        raise EmbedError("query is empty")
    query_vector = embed_texts([text], model)[0]
    hits = query_index(
        text,
        index_dir,
        n_results,
        model=model,
        query_vector=query_vector,
        include_embeddings=True,
    )
    return {
        "query": text,
        "model": MODEL_NAME,
        "dim": len(query_vector),
        "query_vector": query_vector,
        "scheme_filter": named_scheme_id(text),
        "hits": hits,
    }


def smoke_search(
    index_dir: Path | None = None,
    model: Any | None = None,
    query: str = SMOKE_QUERY,
    expected_url: str = SMOKE_SOURCE_URL,
) -> dict:
    """Phase 2E exit check: top hit must be the Large Cap Groww scheme page."""
    hits = query_index(query, index_dir, n_results=5, model=model)
    if not hits:
        raise EmbedError(f"smoke search returned no Groww hits for {query!r}")
    top = hits[0]
    if top["source_url"] != expected_url:
        raise EmbedError(
            "smoke search top hit is not the Large Cap scheme page: "
            f"{top['source_url']} (doc_type={top.get('doc_type')}, "
            f"scheme_id={top.get('scheme_id')}). "
            "Fix chunking or metadata before Phase 4 retrieve."
        )
    return top


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2D MiniLM embed and/or Phase 2E Chroma persist + smoke search."
    )
    parser.add_argument("--chunks-path", type=Path, default=DEFAULT_CHUNKS_PATH)
    parser.add_argument("--embeddings-path", type=Path, default=DEFAULT_EMBEDDINGS_PATH)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX_DIR)
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Re-embed chunks.jsonl (Phase 2D). Implied when --store/--smoke are omitted.",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Persist embeddings.jsonl into data/index/ (Phase 2E).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Search for 'Large Cap expense ratio' and require the Large Cap scheme URL.",
    )
    parser.add_argument(
        "--query",
        metavar="TEXT",
        help="Search the Chroma index and print nearest chunks. Does not call Gemini.",
    )
    parser.add_argument(
        "-k",
        type=int,
        default=5,
        help="How many nearest chunks to print with --query (default: 5).",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    do_embed = args.embed or not (args.store or args.smoke or args.query)

    try:
        if do_embed:
            pairs = embed_corpus(args.chunks_path, args.embeddings_path)
            first_dim = len(pairs[0].vector)
            print(
                f"OK embedded {len(pairs)} chunk(s) with {MODEL_NAME} -> {_rel(args.embeddings_path)}"
            )
            print(f"  len(vector)={first_dim}")
            print(f"  vectors={len(pairs)} chunks={len(pairs)}")
            for pair in pairs:
                chunk = pair.chunk
                print(
                    f"  {chunk['chunk_id']}: dim={len(pair.vector)} "
                    f"{chunk['scheme_id']} {chunk['source_url']}"
                )
        if args.store:
            count, folder = persist_index(args.embeddings_path, args.index_dir)
            print(f"OK stored {count} vector(s) in Chroma -> {_rel(folder)}")
        if args.smoke:
            hits = query_index(SMOKE_QUERY, args.index_dir, n_results=5)
            print(f"SMOKE query={SMOKE_QUERY!r}")
            for rank, hit in enumerate(hits, start=1):
                print(
                    f"  {rank}. {hit['source_url']}  {hit.get('scheme_id')}  "
                    f"{hit.get('doc_type')}  {hit.get('chunk_id')}"
                )
            if not hits or hits[0]["source_url"] != SMOKE_SOURCE_URL:
                smoke_search(args.index_dir, query=SMOKE_QUERY)
            print(f"OK top hit is the Large Cap scheme page: {hits[0]['source_url']}")
        if args.query:
            hits = query_index(args.query, args.index_dir, n_results=max(1, args.k))
            print(f"QUERY {args.query!r}  hits={len(hits)}")
            if not hits:
                print("  (no Groww chunks)")
            for rank, hit in enumerate(hits, start=1):
                snippet = " ".join(str(hit.get("text") or "").split())
                if len(snippet) > 180:
                    snippet = snippet[:177] + "..."
                print(
                    f"  {rank}. dist={hit.get('distance')}  {hit.get('scheme_id')}  "
                    f"{hit.get('doc_type')}  {hit.get('chunk_id')}"
                )
                print(f"     {hit['source_url']}")
                print(f"     {snippet}")
    except (OSError, EmbedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
