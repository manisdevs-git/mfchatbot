"""Phase 4 retrieve: scheme/topic filters, official hits only, no Gemini."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ingest.embed_index import EMBEDDING_DIM, EmbeddedChunk, load_pairs, persist_pairs
from src.guard import classify
from src.pipeline import handle
from src.retrieve import (
    chroma_where,
    clamp_k,
    is_official_hit,
    parse_topic_tags,
    resolve_filters,
    retrieve,
)
from src.schemes import SCHEME_IDS

ROOT = Path(__file__).resolve().parents[1]
EMBEDDINGS = ROOT / "data" / "processed" / "embeddings.jsonl"

SCHEME_TOPIC_QUERIES = (
    (
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        "hdfc-large-cap-fund-direct-growth",
        "expense_ratio",
    ),
    (
        "What is the exit load of HDFC Small Cap Fund Direct Growth?",
        "hdfc-small-cap-fund-direct-growth",
        "exit_load",
    ),
    (
        "What is the minimum SIP amount for HDFC Mid Cap Fund Direct Growth?",
        "hdfc-mid-cap-fund-direct-growth",
        "sip",
    ),
    (
        "What is the lock-in of HDFC ELSS Tax Saver Direct Plan?",
        "hdfc-elss-tax-saver-fund-direct-plan-growth",
        "lock_in",
    ),
    (
        "What is the riskometer of the Gold FoF fund?",
        "hdfc-gold-etf-fund-of-fund-direct-plan-growth",
        "riskometer",
    ),
)

PROCESS_QUERY = "How do I download a capital gains report?"


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


def _vector(slot: int) -> list[float]:
    values = [0.0] * EMBEDDING_DIM
    values[slot] = 1.0
    return values


class _KeyedMiniLM:
    """Maps query/chunk text to a fixed unit vector. No weight download."""

    def __init__(self, mapping: dict[str, list[float]], default: list[float] | None = None) -> None:
        self.mapping = mapping
        self.default = default or _vector(0)

    def encode(self, texts, **_kwargs):
        rows = []
        for text in texts:
            lowered = text.lower()
            chosen = self.default
            for needle, vector in self.mapping.items():
                if needle.lower() in lowered:
                    chosen = vector
                    break
            rows.append(list(chosen))
        return rows


class FilterHelperTests(unittest.TestCase):
    def test_clamp_k_stays_in_phase4_range(self) -> None:
        self.assertEqual(clamp_k(1), 3)
        self.assertEqual(clamp_k(4), 4)
        self.assertEqual(clamp_k(9), 5)

    def test_parse_topic_tags_from_chroma_string(self) -> None:
        self.assertEqual(parse_topic_tags("expense_ratio,exit_load"), ["expense_ratio", "exit_load"])
        self.assertEqual(parse_topic_tags(["sip"]), ["sip"])
        self.assertEqual(parse_topic_tags(""), [])

    def test_official_hit_requires_groww_url(self) -> None:
        self.assertTrue(is_official_hit("https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"))
        self.assertFalse(is_official_hit(""))
        self.assertFalse(is_official_hit(None))
        self.assertFalse(is_official_hit("https://www.hdfcfund.com/factsheet"))

    def test_resolve_filters_scheme_topic_and_process(self) -> None:
        scheme_ids, topic = resolve_filters(
            "What is the expense ratio of HDFC Large Cap Fund Direct Growth?"
        )
        self.assertEqual(scheme_ids, ["hdfc-large-cap-fund-direct-growth"])
        self.assertEqual(topic, "expense_ratio")
        self.assertEqual(chroma_where(scheme_ids), {"scheme_id": "hdfc-large-cap-fund-direct-growth"})

        scheme_ids, topic = resolve_filters(PROCESS_QUERY)
        self.assertEqual(scheme_ids, ["generic"])
        self.assertEqual(classify(PROCESS_QUERY).intent, "process")
        self.assertEqual(chroma_where(scheme_ids), {"scheme_id": "generic"})

        scheme_ids, topic = resolve_filters("What is NAV?")
        self.assertEqual(scheme_ids, ["generic"])
        self.assertEqual(topic, "nav")

    def test_out_of_scope_has_no_scheme_filter(self) -> None:
        scheme_ids, _topic = resolve_filters("SBI Bluechip expense ratio")
        self.assertIsNone(scheme_ids)


class RetrieveFilterTests(unittest.TestCase):
    def test_empty_query_returns_no_chunks(self) -> None:
        self.assertEqual(retrieve("   "), [])

    def test_out_of_scope_returns_empty_without_search(self) -> None:
        with patch("src.retrieve.embed_texts") as mocked:
            self.assertEqual(retrieve("SBI Bluechip expense ratio"), [])
            mocked.assert_not_called()

    def test_drops_unofficial_and_sourceless_hits(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        large = _vector(0)
        pairs = [
            EmbeddedChunk(vector=large, chunk=_chunk()),
            EmbeddedChunk(
                vector=_vector(1),
                chunk=_chunk(
                    chunk_id="bad-amc:0000",
                    text="HDFC Large Cap expense ratio on the AMC site.",
                    source_url="https://www.hdfcfund.com/factsheet",
                ),
            ),
            EmbeddedChunk(
                vector=_vector(2),
                chunk=_chunk(
                    chunk_id="no-url:0000",
                    text="Expense ratio with no source.",
                    source_url="",
                ),
            ),
        ]
        model = _KeyedMiniLM({"large cap": large, "expense ratio": large})
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            persist_pairs(pairs, index_dir)
            hits = retrieve(
                "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
                index_dir=index_dir,
                model=model,
            )
        self.assertTrue(hits)
        self.assertTrue(all(is_official_hit(hit["source_url"]) for hit in hits))
        self.assertTrue(all("groww.in" in hit["source_url"] for hit in hits))
        self.assertNotIn("hdfcfund.com", " ".join(hit["source_url"] for hit in hits))

    def test_scheme_topic_hits_are_groww_scheme(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        vectors = {scheme_id: _vector(index) for index, scheme_id in enumerate(SCHEME_IDS)}
        help_vec = _vector(len(SCHEME_IDS))
        pairs = [
            EmbeddedChunk(
                vector=vectors[scheme_id],
                chunk=_chunk(
                    chunk_id=f"{scheme_id}:0000",
                    text=f"{scheme_id} expense ratio exit load sip lock-in riskometer.",
                    scheme_id=scheme_id,
                    source_url=f"https://groww.in/mutual-funds/{scheme_id}",
                    topic_tags=["expense_ratio", "exit_load", "sip", "lock_in", "riskometer"],
                ),
            )
            for scheme_id in SCHEME_IDS
        ]
        pairs.append(
            EmbeddedChunk(
                vector=help_vec,
                chunk=_chunk(
                    chunk_id="groww-help-cas:0000",
                    text="How to download a capital gains report and CAS statement.",
                    scheme_id="generic",
                    doc_type="groww_help",
                    source_url="https://groww.in/help/mutual-funds/mf-others/how-to-download-capital-gain-report--50",
                    topic_tags=["statements"],
                ),
            )
        )
        mapping = {
            "large cap": vectors["hdfc-large-cap-fund-direct-growth"],
            "small cap": vectors["hdfc-small-cap-fund-direct-growth"],
            "mid cap": vectors["hdfc-mid-cap-fund-direct-growth"],
            "elss": vectors["hdfc-elss-tax-saver-fund-direct-plan-growth"],
            "gold fof": vectors["hdfc-gold-etf-fund-of-fund-direct-plan-growth"],
            "capital gains": help_vec,
        }
        model = _KeyedMiniLM(mapping)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            persist_pairs(pairs, index_dir)
            for query, scheme_id, _topic in SCHEME_TOPIC_QUERIES:
                hits = retrieve(query, index_dir=index_dir, model=model)
                self.assertTrue(hits, query)
                self.assertTrue(all(hit["doc_type"] == "groww_scheme" for hit in hits), query)
                self.assertTrue(all(hit["scheme_id"] == scheme_id for hit in hits), query)
                self.assertTrue(all(is_official_hit(hit["source_url"]) for hit in hits), query)

            process_hits = retrieve(PROCESS_QUERY, index_dir=index_dir, model=model)
            self.assertTrue(process_hits)
            self.assertTrue(all(hit["scheme_id"] == "generic" for hit in process_hits))
            self.assertTrue(all(hit["doc_type"] == "groww_help" for hit in process_hits))


class PipelineRetrieveTests(unittest.TestCase):
    def test_handle_attaches_retrieved_chunks(self) -> None:
        chunk = {
            "text": "Expense ratio: 1.03%.",
            "scheme_id": "hdfc-large-cap-fund-direct-growth",
            "doc_type": "groww_scheme",
            "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        }
        with patch("src.pipeline.retrieve", return_value=[chunk]) as mocked:
            with patch(
                "src.pipeline.generate_answer",
                return_value="The expense ratio is 1.03%.",
            ):
                result = handle("What is the expense ratio of HDFC Large Cap Fund Direct Growth?")
        mocked.assert_called_once()
        self.assertEqual(result.chunks, [chunk])
        self.assertTrue(result.allow_retrieve)
        self.assertIn("The expense ratio is 1.03%.", result.text)
        self.assertIn("Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth", result.text)
        self.assertIn("Last updated from sources: 2026-08-21", result.text)

    def test_empty_handle_does_not_retrieve(self) -> None:
        with patch("src.pipeline.retrieve") as mocked:
            result = handle("   ")
        mocked.assert_not_called()
        self.assertEqual(result.chunks, [])
        self.assertFalse(result.allow_retrieve)


class LiveExitCheckTests(unittest.TestCase):
    @unittest.skipUnless(EMBEDDINGS.is_file(), "Phase 2D embeddings.jsonl is missing")
    def test_live_scheme_topics_and_process_help(self) -> None:
        try:
            import chromadb  # noqa: F401
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self.skipTest("chromadb or sentence-transformers is not installed")

        from ingest.embed_index import MODEL_NAME

        pairs = load_pairs(EMBEDDINGS)
        self.assertGreater(len(pairs), 0)
        model = SentenceTransformer(MODEL_NAME)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            index_dir = Path(tmp) / "index"
            persist_pairs(pairs, index_dir)
            for query, scheme_id, _topic in SCHEME_TOPIC_QUERIES:
                hits = retrieve(query, index_dir=index_dir, model=model)
                self.assertTrue(hits, query)
                self.assertEqual(hits[0]["doc_type"], "groww_scheme", query)
                self.assertEqual(hits[0]["scheme_id"], scheme_id, query)
                self.assertIn("groww.in", hits[0]["source_url"], query)
            process_hits = retrieve(PROCESS_QUERY, index_dir=index_dir, model=model)
            self.assertTrue(process_hits, PROCESS_QUERY)
            self.assertEqual(process_hits[0]["scheme_id"], "generic")
            self.assertEqual(process_hits[0]["doc_type"], "groww_help")


if __name__ == "__main__":
    unittest.main()
