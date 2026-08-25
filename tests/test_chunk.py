"""Phase 2C: normalized text becomes overlapping chunks with Groww metadata."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ingest.chunk import (
    attach_metadata,
    chunk_corpus,
    chunk_document,
    chunk_text,
    scheme_page_sections,
    token_count,
)
from ingest.normalize import SIDECAR_FIELDS

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
MANIFEST = ROOT / "corpus_manifest.json"


def _meta(**overrides: object) -> dict:
    base = {
        "doc_id": "groww-large-cap-direct-growth",
        "scheme_id": "hdfc-large-cap-fund-direct-growth",
        "doc_type": "groww_scheme",
        "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
        "as_of_date": "2026-08-21",
        "topic_tags": ["expense_ratio", "exit_load", "sip"],
    }
    base.update(overrides)
    return base


def _words(count: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{index:04d}" for index in range(count))


class ChunkTextTests(unittest.TestCase):
    def test_short_page_is_one_chunk(self) -> None:
        text = "Expense ratio: 1.03%\nMinimum SIP: ₹100\nExit load of 1% if redeemed within 1 year"
        pieces = chunk_text(text)
        self.assertEqual(len(pieces), 1)
        self.assertIn("1.03%", pieces[0])
        self.assertIn("₹100", pieces[0])

    def test_long_page_splits_with_overlap(self) -> None:
        sentences = [f"Fact number {index:04d} is stated here." for index in range(200)]
        text = " ".join(sentences)
        self.assertGreater(token_count(text), 800)
        pieces = chunk_text(text)
        self.assertGreaterEqual(len(pieces), 2)
        for piece in pieces:
            self.assertLessEqual(token_count(piece), 800)
        first_words = set(pieces[0].split()[-80:])
        second_words = set(pieces[1].split()[:80])
        self.assertTrue(first_words & second_words)


class SchemePageSectionTests(unittest.TestCase):
    def test_drops_shared_glossary_and_keeps_facts(self) -> None:
        text = (
            "Groww — HDFC Large Cap Fund Direct Growth\n"
            "HDFC Large Cap Fund Direct Growth\n"
            "Expense ratio: 1.03%\n"
            "Understand terms\n"
            "Expense ratio\n"
            "A fee payable to a mutual fund house for managing your mutual fund investments.\n"
            "About\n"
            "The scheme seeks to invest predominantly in Large-Cap companies.\n"
        )
        parts = scheme_page_sections(text)
        self.assertEqual(len(parts), 2)
        self.assertIn("1.03%", parts[0])
        self.assertIn("Large Cap", parts[0])
        self.assertNotIn("A fee payable to a mutual fund house", parts[0])
        self.assertIn("Large-Cap companies", parts[1])
        records = chunk_document(text, _meta())
        joined = "\n".join(item["text"] for item in records)
        self.assertIn("1.03%", joined)
        self.assertNotIn("A fee payable to a mutual fund house", joined)


class MetadataTests(unittest.TestCase):
    def test_sidecar_fields_copied_onto_every_chunk(self) -> None:
        text = _words(900)
        records = chunk_document(text, _meta())
        self.assertGreaterEqual(len(records), 2)
        for index, record in enumerate(records):
            self.assertEqual(record["chunk_id"], f"groww-large-cap-direct-growth:{index:04d}")
            self.assertEqual(record["scheme_id"], "hdfc-large-cap-fund-direct-growth")
            self.assertEqual(record["doc_type"], "groww_scheme")
            self.assertEqual(
                record["source_url"],
                "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            )
            self.assertEqual(record["source_title"], "Groww — HDFC Large Cap Fund Direct Growth")
            self.assertEqual(record["as_of_date"], "2026-08-21")
            self.assertEqual(record["topic_tags"], ["expense_ratio", "exit_load", "sip"])
            self.assertIn("text", record)
            self.assertNotIn("embedding", record)
            self.assertNotIn("vector", record)
            for field in SIDECAR_FIELDS:
                self.assertIn(field, record)

    def test_drops_missing_source_url(self) -> None:
        self.assertEqual(chunk_document("Expense ratio 1.03%", _meta(source_url="")), [])
        self.assertIsNone(attach_metadata("hello", _meta(source_url=""), index=0))

    def test_drops_non_groww_source_url(self) -> None:
        records = chunk_document(
            "Expense ratio 1.03%",
            _meta(source_url="https://www.hdfcfund.com/factsheet"),
        )
        self.assertEqual(records, [])


class ChunkCorpusTests(unittest.TestCase):
    def test_writes_jsonl_one_object_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            text_path = folder / "groww-large-cap-direct-growth.txt"
            meta_path = folder / "groww-large-cap-direct-growth.meta.json"
            text_path.write_text(_words(900, "alpha"), encoding="utf-8")
            meta_path.write_text(json.dumps(_meta()), encoding="utf-8")

            # Minimal valid corpus needs the real manifest; write only the one
            # pair that chunk_corpus will look up after validation. Use the
            # checked-in manifest and a processed dir that contains every doc.
            self._write_all_docs(folder, long_doc_id="groww-primer-expense-ratio")
            dest = folder / "chunks.jsonl"
            records = chunk_corpus(MANIFEST, folder, dest)
            lines = dest.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), len(records))
            self.assertGreater(len(records), 11)
            parsed = [json.loads(line) for line in lines]
            self.assertTrue(all("groww.in" in item["source_url"] for item in parsed))
            self.assertTrue(all("text" in item and item["text"] for item in parsed))
            self.assertTrue(all("embedding" not in item for item in parsed))

    def _write_all_docs(self, folder: Path, long_doc_id: str) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for doc in manifest["documents"]:
            body = _words(900 if doc["doc_id"] == long_doc_id else 40, doc["doc_id"][:8])
            (folder / f"{doc['doc_id']}.txt").write_text(body, encoding="utf-8")
            (folder / f"{doc['doc_id']}.meta.json").write_text(json.dumps(doc), encoding="utf-8")


class LiveExitCheckTests(unittest.TestCase):
    @unittest.skipUnless(
        (PROCESSED / "groww-large-cap-direct-growth.txt").is_file(),
        "Phase 2B processed files are missing",
    )
    def test_live_chunks_exceed_document_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chunks.jsonl"
            records = chunk_corpus(MANIFEST, PROCESSED, dest)
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            self.assertGreater(len(records), len(manifest["documents"]))
            sample = records[0]
            self.assertTrue(sample["text"].strip())
            self.assertIn("groww.in", sample["source_url"])
            self.assertNotIn("embedding", sample)
            self.assertNotIn("vector", sample)
            large = next(
                item for item in records if item["doc_id"] == "groww-large-cap-direct-growth"
            )
            self.assertIn("1.03", large["text"])
            self.assertEqual(
                large["source_url"],
                "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            )


if __name__ == "__main__":
    unittest.main()
