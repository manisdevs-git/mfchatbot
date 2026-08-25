"""Phase 2D MiniLM embed and Phase 2E Chroma persist. No Gemini."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from ingest.embed_index import (
    EMBEDDING_DIM,
    MODEL_NAME,
    SMOKE_QUERY,
    SMOKE_SOURCE_URL,
    EmbedError,
    EmbeddedChunk,
    chroma_metadata,
    embed_chunks,
    embed_corpus,
    embed_texts,
    embeddings_digest,
    index_is_current,
    load_chunks,
    load_model,
    load_pairs,
    named_scheme_id,
    persist_pairs,
    query_index,
    smoke_search,
    write_embeddings_stamp,
)

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "data" / "processed" / "chunks.jsonl"
EMBEDDINGS = ROOT / "data" / "processed" / "embeddings.jsonl"


def _chunk(**overrides: object) -> dict:
    base = {
        "chunk_id": "groww-large-cap-direct-growth:0000",
        "doc_id": "groww-large-cap-direct-growth",
        "text": "HDFC Large Cap Fund Direct Growth. Expense ratio: 1.03%.",
        "scheme_id": "hdfc-large-cap-fund-direct-growth",
        "doc_type": "groww_scheme",
        "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
        "as_of_date": "2026-08-21",
        "topic_tags": ["expense_ratio"],
    }
    base.update(overrides)
    return base


class _FakeMiniLM:
    """Stand-in encoder so unit tests do not download weights."""

    def encode(self, texts, **_kwargs):
        rows = []
        for index, _text in enumerate(texts):
            row = [0.0] * EMBEDDING_DIM
            row[0] = float(index + 1)
            rows.append(row)
        return rows


class LoadChunksTests(unittest.TestCase):
    def test_reads_jsonl_and_keeps_chunk_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunks.jsonl"
            records = [_chunk(), _chunk(chunk_id="groww-help-cas:0000", text="CAS is a statement.")]
            path.write_text(
                "\n".join(json.dumps(item) for item in records) + "\n",
                encoding="utf-8",
            )
            loaded = load_chunks(path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["source_url"], records[0]["source_url"])
            self.assertNotIn("embedding", loaded[0])
            self.assertNotIn("vector", loaded[0])

    def test_drops_non_groww_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunks.jsonl"
            path.write_text(
                json.dumps(_chunk(source_url="https://www.hdfcfund.com/factsheet")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(EmbedError):
                load_chunks(path)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(EmbedError):
            load_chunks(Path("missing-chunks.jsonl"))


class EmbedChunksTests(unittest.TestCase):
    def test_one_vector_per_chunk_with_minilm_dim(self) -> None:
        chunks = [
            _chunk(),
            _chunk(chunk_id="groww-elss-tax-saver-direct-growth:0000", text="ELSS lock-in is 3 years."),
        ]
        pairs = embed_chunks(chunks, model=_FakeMiniLM())
        self.assertEqual(len(pairs), len(chunks))
        for pair in pairs:
            self.assertEqual(len(pair.vector), EMBEDDING_DIM)
            self.assertNotIn("embedding", pair.chunk)
            self.assertNotIn("vector", pair.chunk)
            self.assertEqual(
                pair.chunk["source_url"],
                "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            )

    def test_does_not_rewrite_chunk_metadata(self) -> None:
        original = _chunk()
        snapshot = json.dumps(original, sort_keys=True)
        pair = embed_chunks([original], model=_FakeMiniLM())[0]
        self.assertEqual(json.dumps(pair.chunk, sort_keys=True), snapshot)
        self.assertEqual(json.dumps(original, sort_keys=True), snapshot)

    def test_writes_pairs_without_inventing_chunk_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            chunks_path = Path(tmp) / "chunks.jsonl"
            dest = Path(tmp) / "embeddings.jsonl"
            chunks_path.write_text(json.dumps(_chunk()) + "\n", encoding="utf-8")
            pairs = embed_corpus(chunks_path, dest, model=_FakeMiniLM())
            self.assertEqual(len(pairs), 1)
            lines = dest.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["dim"], EMBEDDING_DIM)
            self.assertEqual(len(record["embedding"]), EMBEDDING_DIM)
            self.assertEqual(record["chunk"]["chunk_id"], "groww-large-cap-direct-growth:0000")
            self.assertNotIn("embedding", record["chunk"])

    def test_embed_texts_rejects_wrong_dim(self) -> None:
        class _Bad:
            def encode(self, texts, **_kwargs):
                return [[0.0, 1.0] for _ in texts]

        with self.assertRaises(EmbedError):
            embed_texts(["expense ratio"], model=_Bad())

    def test_load_model_reuses_the_same_encoder(self) -> None:
        with patch("sentence_transformers.SentenceTransformer") as ctor:
            ctor.side_effect = lambda *args, **kwargs: object()
            load_model.cache_clear()
            try:
                first = load_model("cache-test-encoder")
                second = load_model("cache-test-encoder")
            finally:
                load_model.cache_clear()
        self.assertIs(first, second)
        self.assertEqual(ctor.call_count, 1)


class ChromaStoreTests(unittest.TestCase):
    def test_named_scheme_id_from_query(self) -> None:
        self.assertEqual(
            named_scheme_id(SMOKE_QUERY),
            "hdfc-large-cap-fund-direct-growth",
        )
        self.assertEqual(
            named_scheme_id("small-cap exit load"),
            "hdfc-small-cap-fund-direct-growth",
        )
        self.assertIsNone(named_scheme_id("what is an expense ratio?"))

    def test_metadata_flattens_topic_tags(self) -> None:
        meta = chroma_metadata(_chunk())
        self.assertEqual(meta["scheme_id"], "hdfc-large-cap-fund-direct-growth")
        self.assertEqual(meta["topic_tags"], "expense_ratio")
        self.assertEqual(meta["source_url"], SMOKE_SOURCE_URL)
        self.assertEqual(meta["as_of_date"], "2026-08-21")
        self.assertEqual(meta["doc_type"], "groww_scheme")
        self.assertNotIsInstance(meta["topic_tags"], list)

    def test_persists_and_smoke_hits_large_cap(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        large = [0.0] * EMBEDDING_DIM
        large[0] = 1.0
        primer = [0.0] * EMBEDDING_DIM
        primer[1] = 1.0
        pairs = [
            EmbeddedChunk(vector=large, chunk=_chunk()),
            EmbeddedChunk(
                vector=primer,
                chunk=_chunk(
                    chunk_id="groww-primer-expense-ratio:0000",
                    doc_id="groww-primer-expense-ratio",
                    text="What is expense ratio in large-cap mutual funds?",
                    scheme_id="generic",
                    doc_type="groww_help",
                    source_url="https://groww.in/p/expense-ratio",
                    source_title="Groww — Expense ratio",
                    topic_tags=["education", "expense_ratio"],
                ),
            ),
        ]

        class _QueryMiniLM:
            def encode(self, texts, **_kwargs):
                return [list(large) for _ in texts]

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            count = persist_pairs(pairs, index_dir)
            self.assertEqual(count, 2)
            hits = query_index(SMOKE_QUERY, index_dir, n_results=2, model=_QueryMiniLM())
            self.assertEqual(hits[0]["source_url"], SMOKE_SOURCE_URL)
            self.assertEqual(hits[0]["scheme_id"], "hdfc-large-cap-fund-direct-growth")
            self.assertEqual(hits[0]["doc_type"], "groww_scheme")
            self.assertEqual(hits[0]["topic_tags"], "expense_ratio")
            top = smoke_search(index_dir, model=_QueryMiniLM())
            self.assertEqual(top["source_url"], SMOKE_SOURCE_URL)

    def test_smoke_fails_when_help_page_wins(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        help_vec = [0.0] * EMBEDDING_DIM
        help_vec[0] = 1.0
        pair = EmbeddedChunk(
            vector=help_vec,
            chunk=_chunk(
                chunk_id="groww-primer-expense-ratio:0000",
                text="Expense ratio of large-cap mutual funds.",
                scheme_id="generic",
                doc_type="groww_help",
                source_url="https://groww.in/p/expense-ratio",
                topic_tags=["education", "expense_ratio"],
            ),
        )

        class _QueryMiniLM:
            def encode(self, texts, **_kwargs):
                return [list(help_vec) for _ in texts]

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            persist_pairs([pair], index_dir)
            with self.assertRaises(EmbedError):
                smoke_search(index_dir, model=_QueryMiniLM())

    def test_scheme_metadata_beats_closer_primer(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        primer_vec = [0.0] * EMBEDDING_DIM
        primer_vec[0] = 1.0
        large_vec = [0.0] * EMBEDDING_DIM
        large_vec[1] = 1.0
        pairs = [
            EmbeddedChunk(
                vector=primer_vec,
                chunk=_chunk(
                    chunk_id="groww-primer-expense-ratio:0000",
                    text="Expense ratio of large-cap mutual funds in general.",
                    scheme_id="generic",
                    doc_type="groww_help",
                    source_url="https://groww.in/p/expense-ratio",
                    topic_tags=["education", "expense_ratio"],
                ),
            ),
            EmbeddedChunk(vector=large_vec, chunk=_chunk()),
        ]

        class _QueryMiniLM:
            def encode(self, texts, **_kwargs):
                return [list(primer_vec) for _ in texts]

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            persist_pairs(pairs, index_dir)
            top = smoke_search(index_dir, model=_QueryMiniLM())
            self.assertEqual(top["source_url"], SMOKE_SOURCE_URL)
            self.assertEqual(top["doc_type"], "groww_scheme")


class EmbeddingsStampTests(unittest.TestCase):
    def test_index_is_current_tracks_embeddings_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            embeddings = Path(tmp) / "embeddings.jsonl"
            index_dir = Path(tmp) / "index"
            embeddings.write_text("alpha\n", encoding="utf-8")
            index_dir.mkdir()
            self.assertFalse(index_is_current(index_dir, embeddings))
            write_embeddings_stamp(embeddings, index_dir)
            self.assertTrue(index_is_current(index_dir, embeddings))
            embeddings.write_text("beta\n", encoding="utf-8")
            self.assertFalse(index_is_current(index_dir, embeddings))
            self.assertNotEqual(
                embeddings_digest(embeddings),
                (index_dir / ".embeddings_sha256").read_text(encoding="utf-8").strip(),
            )


class LiveExitCheckTests(unittest.TestCase):
    @unittest.skipUnless(CHUNKS.is_file(), "Phase 2C chunks.jsonl is missing")
    def test_live_minilm_dim_and_count(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.skipTest("sentence-transformers is not installed")

        chunks = load_chunks(CHUNKS)
        self.assertGreater(len(chunks), 0)
        model = SentenceTransformer(MODEL_NAME)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "embeddings.jsonl"
            pairs = embed_corpus(CHUNKS, dest, model=model)
        self.assertEqual(len(pairs), len(chunks))
        self.assertEqual(len(pairs[0].vector), EMBEDDING_DIM)
        self.assertTrue(all(len(pair.vector) == EMBEDDING_DIM for pair in pairs))
        self.assertTrue(all("groww.in" in pair.chunk["source_url"] for pair in pairs))
        self.assertNotIn("embedding", pairs[0].chunk)

    @unittest.skipUnless(EMBEDDINGS.is_file(), "Phase 2D embeddings.jsonl is missing")
    def test_live_chroma_smoke_large_cap_expense_ratio(self) -> None:
        try:
            import chromadb  # noqa: F401
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.skipTest("chromadb or sentence-transformers is not installed")

        pairs = load_pairs(EMBEDDINGS)
        self.assertGreater(len(pairs), 0)
        model = SentenceTransformer(MODEL_NAME)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            persist_pairs(pairs, index_dir)
            top = smoke_search(index_dir, model=model)
        self.assertEqual(top["source_url"], SMOKE_SOURCE_URL)
        self.assertEqual(top["scheme_id"], "hdfc-large-cap-fund-direct-growth")
        self.assertEqual(top["doc_type"], "groww_scheme")


if __name__ == "__main__":
    unittest.main()
