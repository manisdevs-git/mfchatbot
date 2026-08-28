"""README and submission artifacts: prototype, sources, sample Q&A, disclaimer."""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SOURCES = ROOT / "docs" / "sources.csv"
SAMPLE_QA = ROOT / "docs" / "sample-qa.md"
ASK = (ROOT / "web" / "src" / "ask.ts").read_text(encoding="utf-8")


class ReadmeContractTests(unittest.TestCase):
    def test_readme_covers_users_and_setup(self) -> None:
        self.assertTrue((ROOT / "README.md").is_file())
        self.assertIn("https://mfchatbot-six.vercel.app", README)
        self.assertIn("https://mfchatbot-production-fd5b.up.railway.app/health", README)
        self.assertIn("Facts-only. No investment advice.", README)
        self.assertIn("uvicorn api.main:app", README)
        self.assertIn("npm run dev", README)
        self.assertIn("GEMINI_API_KEY", README)
        self.assertIn("Known limits", README)
        self.assertIn("HDFC Large Cap Fund Direct Growth", README)
        self.assertIn("python -m ingest.refresh_corpus", README)
        self.assertIn("chatdemo/01-home.png", README)
        self.assertIn("chatdemo/02-sample-faqs.png", README)
        self.assertIn("chatdemo/03-about.png", README)
        self.assertIn("chatdemo/04-fund-chip.png", README)
        self.assertIn("chatdemo/05-chat.png", README)
        self.assertIn("chatdemo/preview.html", README)
        for name in (
            "01-home.png",
            "02-sample-faqs.png",
            "03-about.png",
            "04-fund-chip.png",
            "05-chat.png",
            "preview.html",
        ):
            self.assertTrue((ROOT / "chatdemo" / name).is_file(), name)

    def test_disclaimer_matches_ui(self) -> None:
        self.assertIn("Facts-only. No investment advice.", ASK)
        self.assertIn("Facts-only. No investment advice.", README)

    def test_sources_csv_has_indexed_groww_urls(self) -> None:
        self.assertTrue(SOURCES.is_file())
        with SOURCES.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(rows), 15)
        self.assertLessEqual(len(rows), 25)
        indexed = [row["url"] for row in rows if row["in_rag_index"] == "yes"]
        self.assertEqual(len(indexed), 11)
        self.assertTrue(all(url.startswith("https://groww.in/") for url in indexed))
        self.assertIn(
            "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            indexed,
        )

    def test_sample_qa_has_sourced_answers(self) -> None:
        text = SAMPLE_QA.read_text(encoding="utf-8")
        self.assertIn("What is the expense ratio of HDFC Large Cap Fund Direct Growth?", text)
        self.assertIn("Should I invest in this fund?", text)
        self.assertIn("https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth", text)
        self.assertIn("https://www.amfiindia.com/investor", text)
        self.assertIn(
            "https://www.amfiindia.com/investor-corner/knowledge-center/risks-in-mutual-funds.html",
            text,
        )
        self.assertGreaterEqual(text.count("**Q:**"), 8)


if __name__ == "__main__":
    unittest.main()
