"""Phase 1: Groww-only corpus manifest checks."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from ingest.validate_manifest import (
    ManifestError,
    load_manifest,
    validate_manifest,
    validate_url,
)

ROOT = Path(__file__).resolve().parents[1]


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_groww_hosts(self) -> None:
        validate_url("https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth")
        validate_url("https://groww.in/help/mutual-funds/mf-others/how-to-download-capital-gain-report--50")
        validate_url("https://groww.in/p/riskometer")

    def test_rejects_amc_and_other_aggregators(self) -> None:
        blocked = [
            "https://www.hdfcfund.com/explore/mutual-funds/hdfc-large-cap-fund/direct",
            "https://www.valueresearchonline.com/funds/123/hdfc-large-cap",
            "https://www.moneycontrol.com/mutual-funds/nav/hdfc-large-cap/123",
            "https://medium.com/@someone/best-funds",
        ]
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(ManifestError):
                    validate_url(url)

    def test_rejects_unknown_third_party_host(self) -> None:
        with self.assertRaises(ManifestError):
            validate_url("https://example.com/factsheet.pdf")


class ManifestFileTests(unittest.TestCase):
    def test_checked_in_manifest_passes_phase1_exit_check(self) -> None:
        summary = validate_manifest(load_manifest(ROOT / "corpus_manifest.json"))
        self.assertIn("schemes=5", summary)
        self.assertIn("process=yes", summary)
        self.assertIn("education=yes", summary)
        self.assertIn("hosts=groww-only", summary)

    def test_amc_url_in_manifest_fails(self) -> None:
        manifest = load_manifest(ROOT / "corpus_manifest.json")
        tainted = copy.deepcopy(manifest)
        tainted["documents"][0]["source_url"] = (
            "https://www.hdfcfund.com/explore/mutual-funds/hdfc-mid-cap-fund/direct"
        )
        with self.assertRaises(ManifestError):
            validate_manifest(tainted)

    def test_missing_scheme_fails(self) -> None:
        manifest = load_manifest(ROOT / "corpus_manifest.json")
        stripped = copy.deepcopy(manifest)
        stripped["documents"] = [
            doc
            for doc in stripped["documents"]
            if doc["scheme_id"] != "hdfc-large-cap-fund-direct-growth"
        ]
        with self.assertRaises(ManifestError):
            validate_manifest(stripped)


if __name__ == "__main__":
    unittest.main()
