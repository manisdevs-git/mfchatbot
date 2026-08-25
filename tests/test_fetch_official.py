"""Phase 2A: Groww-only scrape writes one raw HTML file per manifest document."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ingest.fetch_official import FetchError, fetch_corpus, fetch_one, raw_path_for
from ingest.validate_manifest import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def _scheme_doc(**overrides: object) -> dict:
    base = {
        "doc_id": "groww-large-cap-direct-growth",
        "scheme_id": "hdfc-large-cap-fund-direct-growth",
        "doc_type": "groww_scheme",
        "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
        "as_of_date": "2026-08-21",
        "topic_tags": ["expense_ratio"],
    }
    base.update(overrides)
    return base


class FetchOneTests(unittest.TestCase):
    def test_writes_html_named_by_doc_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            result = fetch_one(
                _scheme_doc(),
                raw_dir,
                download=lambda url: (url, b"<html>expense ratio SIP exit load</html>"),
            )
            dest = raw_dir / "groww-large-cap-direct-growth.html"
            self.assertEqual(result.path, dest)
            self.assertTrue(dest.is_file())
            self.assertIn(b"expense ratio", dest.read_bytes())
            self.assertEqual(result.source_url, result.final_url)
            self.assertTrue(result.source_url.startswith("https://groww.in/"))

    def test_refuses_non_groww_host_without_downloading(self) -> None:
        called: list[str] = []

        def download(url: str) -> tuple[str, bytes]:
            called.append(url)
            return url, b"<html></html>"

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError):
            fetch_one(
                _scheme_doc(source_url="https://www.hdfcfund.com/factsheet"),
                Path(tmp),
                download=download,
            )
        self.assertEqual(called, [])

    def test_refuses_redirect_off_groww(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError):
            fetch_one(
                _scheme_doc(),
                Path(tmp),
                download=lambda url: ("https://www.moneycontrol.com/funds", b"<html></html>"),
            )

    def test_refuses_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FetchError):
            fetch_one(_scheme_doc(), Path(tmp), download=lambda url: (url, b""))

    def test_unsafe_doc_id_is_rejected(self) -> None:
        with self.assertRaises(FetchError):
            raw_path_for("../escape")


class FetchCorpusTests(unittest.TestCase):
    def test_checked_in_manifest_writes_one_file_per_document(self) -> None:
        manifest = load_manifest(ROOT / "corpus_manifest.json")
        expected_ids = [doc["doc_id"] for doc in manifest["documents"]]
        url_by_id = {doc["doc_id"]: doc["source_url"] for doc in manifest["documents"]}

        def download(url: str) -> tuple[str, bytes]:
            return url, f"<html>{url}</html>".encode()

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            results = fetch_corpus(
                ROOT / "corpus_manifest.json",
                raw_dir,
                download=download,
                delay_s=0,
            )
            written = sorted(p.name for p in raw_dir.glob("*.html"))
            self.assertEqual(written, sorted(f"{doc_id}.html" for doc_id in expected_ids))
            self.assertEqual(len(results), len(expected_ids))
            for item in results:
                self.assertEqual(item.source_url, url_by_id[item.doc_id])
                self.assertIn("groww.in", item.source_url)
                self.assertEqual(item.path.read_text(encoding="utf-8"), f"<html>{item.source_url}</html>")

    def test_tainted_manifest_never_downloads(self) -> None:
        manifest = load_manifest(ROOT / "corpus_manifest.json")
        tainted = copy.deepcopy(manifest)
        tainted["documents"][0]["source_url"] = (
            "https://www.hdfcfund.com/explore/mutual-funds/hdfc-mid-cap-fund/direct"
        )
        called: list[str] = []

        def download(url: str) -> tuple[str, bytes]:
            called.append(url)
            return url, b"<html></html>"

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as manifest_tmp:
            path = Path(manifest_tmp) / "corpus_manifest.json"
            path.write_text(json.dumps(tainted), encoding="utf-8")
            with self.assertRaises(ManifestError):
                fetch_corpus(path, Path(tmp), download=download, delay_s=0)
        self.assertEqual(called, [])

    def test_invalid_json_never_downloads(self) -> None:
        called: list[str] = []

        def download(url: str) -> tuple[str, bytes]:
            called.append(url)
            return url, b"<html></html>"

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as manifest_tmp:
            path = Path(manifest_tmp) / "corpus_manifest.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                fetch_corpus(path, Path(tmp), download=download, delay_s=0)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
