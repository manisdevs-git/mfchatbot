"""Phase 2 refresh: staging rebuild must not touch live files until checks pass."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ingest.embed_index import EMBEDDING_DIM
from ingest.fetch_official import FetchError
from ingest.refresh_corpus import RefreshError, refresh_corpus
from ingest.validate_manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.json"


class _ContentMiniLM:
    """Stand-in encoder so refresh tests do not download MiniLM weights."""

    def encode(self, texts, **_kwargs):
        rows = []
        for text in texts:
            row = [0.0] * EMBEDDING_DIM
            folded = text.lower()
            if "large cap" in folded:
                row[0] = 1.0
            else:
                row[1] = 1.0
            rows.append(row)
        return rows


def _download(_url: str) -> tuple[str, bytes]:
    body = (
        "<html><body>"
        f"<p>Page for {_url}</p>"
        "<p>Expense ratio: 1.03%. Exit load of 1% if redeemed within 1 year. "
        "Minimum SIP ₹100.</p>"
        "</body></html>"
    )
    return _url, body.encode("utf-8")


def _seed_live_processed(folder: Path, marker: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "chunks.jsonl").write_text(marker + "\n", encoding="utf-8")
    (folder / "embeddings.jsonl").write_text(marker + "\n", encoding="utf-8")
    (folder / "old-doc.txt").write_text("stale\n", encoding="utf-8")


class RefreshCorpusTests(unittest.TestCase):
    def test_failed_fetch_leaves_live_chunks_and_embeddings(self) -> None:
        calls = {"n": 0}

        def download(url: str) -> tuple[str, bytes]:
            calls["n"] += 1
            if calls["n"] > 2:
                raise FetchError("groww.in unavailable")
            return _download(url)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            live = root / "processed"
            staging = root / "staging"
            marker = '{"chunk_id":"keep-me"}'
            _seed_live_processed(live, marker)

            with self.assertRaises(FetchError):
                refresh_corpus(
                    MANIFEST,
                    live_processed=live,
                    live_manifest=root / "corpus_manifest.json",
                    staging_root=staging,
                    download=download,
                    delay_s=0,
                    model=_ContentMiniLM(),
                    as_of_date="2026-08-25",
                )

            self.assertEqual((live / "chunks.jsonl").read_text(encoding="utf-8"), marker + "\n")
            self.assertEqual((live / "embeddings.jsonl").read_text(encoding="utf-8"), marker + "\n")
            self.assertTrue((live / "old-doc.txt").is_file())
            self.assertFalse((root / "corpus_manifest.json").exists())

    def test_dry_run_does_not_replace_live_processed(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            live = root / "processed"
            staging = root / "staging"
            marker = '{"chunk_id":"keep-me"}'
            _seed_live_processed(live, marker)

            result = refresh_corpus(
                MANIFEST,
                live_processed=live,
                live_manifest=root / "corpus_manifest.json",
                staging_root=staging,
                download=_download,
                delay_s=0,
                model=_ContentMiniLM(),
                as_of_date="2026-08-25",
                dry_run=True,
            )

            self.assertFalse(result.swapped)
            self.assertEqual((live / "chunks.jsonl").read_text(encoding="utf-8"), marker + "\n")
            self.assertTrue((staging / "processed" / "chunks.jsonl").is_file())
            self.assertGreaterEqual(result.chunk_count, 11)
            self.assertFalse((root / "corpus_manifest.json").exists())

    def test_successful_refresh_replaces_chunks_and_embeddings(self) -> None:
        try:
            import chromadb  # noqa: F401
        except ImportError:
            self.skipTest("chromadb is not installed")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            live = root / "processed"
            staging = root / "staging"
            live_manifest = root / "corpus_manifest.json"
            marker = '{"chunk_id":"stale"}'
            _seed_live_processed(live, marker)

            result = refresh_corpus(
                MANIFEST,
                live_processed=live,
                live_manifest=live_manifest,
                staging_root=staging,
                download=_download,
                delay_s=0,
                model=_ContentMiniLM(),
                as_of_date="2026-08-25",
            )

            self.assertTrue(result.swapped)
            self.assertFalse((live / "old-doc.txt").exists())
            chunks = [
                json.loads(line)
                for line in (live / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            embeddings = [
                json.loads(line)
                for line in (live / "embeddings.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            expected_ids = {doc["doc_id"] for doc in load_manifest(MANIFEST)["documents"]}
            self.assertGreaterEqual(len(chunks), len(expected_ids))
            self.assertEqual(len(embeddings), len(chunks))
            self.assertEqual({item["doc_id"] for item in chunks}, expected_ids)
            self.assertTrue(all("groww.in" in item["source_url"] for item in chunks))
            self.assertTrue(all(item["as_of_date"] == "2026-08-25" for item in chunks))
            self.assertNotEqual((live / "chunks.jsonl").read_text(encoding="utf-8"), marker + "\n")
            stamped = json.loads(live_manifest.read_text(encoding="utf-8"))
            self.assertTrue(all(doc["as_of_date"] == "2026-08-25" for doc in stamped["documents"]))

    def test_tainted_manifest_never_swaps(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            live = root / "processed"
            staging = root / "staging"
            marker = '{"chunk_id":"keep-me"}'
            _seed_live_processed(live, marker)
            tainted = load_manifest(MANIFEST)
            tainted["documents"][0]["source_url"] = "https://www.hdfcfund.com/factsheet"
            manifest_path = root / "bad-manifest.json"
            manifest_path.write_text(json.dumps(tainted), encoding="utf-8")

            with self.assertRaises(Exception):
                refresh_corpus(
                    manifest_path,
                    live_processed=live,
                    live_manifest=root / "out-manifest.json",
                    staging_root=staging,
                    download=_download,
                    delay_s=0,
                    model=_ContentMiniLM(),
                )

            self.assertEqual((live / "chunks.jsonl").read_text(encoding="utf-8"), marker + "\n")
            self.assertTrue((live / "old-doc.txt").is_file())


class RefreshQualityGateTests(unittest.TestCase):
    def test_assert_rejects_short_chunk_file(self) -> None:
        from ingest.refresh_corpus import assert_staged_corpus

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            processed = Path(tmp)
            chunks = processed / "chunks.jsonl"
            embeddings = processed / "embeddings.jsonl"
            chunks.write_text("{}\n", encoding="utf-8")
            embeddings.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(RefreshError):
                assert_staged_corpus(
                    load_manifest(MANIFEST),
                    processed,
                    chunks,
                    embeddings,
                    processed / "index",
                    skip_smoke=True,
                )


if __name__ == "__main__":
    unittest.main()
