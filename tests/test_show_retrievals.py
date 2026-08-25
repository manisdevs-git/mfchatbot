"""Inspect-retrieval formatter: query tokens, matching lines, vector preview."""

from __future__ import annotations

import unittest

from scripts.show_retrievals import format_vector, matching_lines, query_tokens, render_inspect


class QueryTokenTests(unittest.TestCase):
    def test_splits_hyphen_and_keeps_phrase_parts(self) -> None:
        tokens = query_tokens("ELSS lock-in")
        self.assertIn("elss", tokens)
        self.assertIn("lock-in", tokens)
        self.assertIn("lock", tokens)


class MatchingLineTests(unittest.TestCase):
    def test_keeps_lines_that_share_query_tokens(self) -> None:
        text = (
            "Groww — HDFC ELSS Tax Saver Fund Direct Plan Growth\n"
            "Expense ratio: 1.19%\n"
            "Lock-in: 3 years\n"
            "Riskometer: Very High\n"
        )
        matched = matching_lines("ELSS lock-in", text)
        self.assertIn("Groww — HDFC ELSS Tax Saver Fund Direct Plan Growth", matched)
        self.assertIn("Lock-in: 3 years", matched)
        self.assertNotIn("Riskometer: Very High", matched)


class VectorPreviewTests(unittest.TestCase):
    def test_preview_shows_dim_and_ellipsis(self) -> None:
        values = [float(index) for index in range(12)]
        text = format_vector(values, preview=3, full=False)
        self.assertIn("dim=12", text)
        self.assertIn("0.000000", text)
        self.assertIn("...", text)
        self.assertNotIn("5.000000", text)

    def test_full_prints_every_value(self) -> None:
        values = [0.1, 0.2, 0.3]
        text = format_vector(values, preview=1, full=True)
        self.assertIn("0.100000", text)
        self.assertIn("0.200000", text)
        self.assertIn("0.300000", text)


class RenderTests(unittest.TestCase):
    def test_report_includes_query_vector_and_chunk(self) -> None:
        payload = {
            "query": "ELSS lock-in",
            "model": "all-MiniLM-L6-v2",
            "dim": 3,
            "query_vector": [0.1, 0.2, 0.3],
            "scheme_filter": "hdfc-elss-tax-saver-fund-direct-plan-growth",
            "hits": [
                {
                    "chunk_id": "groww-elss-tax-saver-direct-growth:0000",
                    "text": "HDFC ELSS Tax Saver\nLock-in: 3 years\n",
                    "distance": 0.79,
                    "scheme_id": "hdfc-elss-tax-saver-fund-direct-plan-growth",
                    "doc_type": "groww_scheme",
                    "topic_tags": "lock_in",
                    "source_url": "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
                    "embedding": [0.4, 0.5, 0.6],
                }
            ],
        }
        report = render_inspect(payload, preview=8, full_vectors=False)
        self.assertIn("QUERY: ELSS lock-in", report)
        self.assertIn("QUERY VECTOR:", report)
        self.assertIn("chunk vector", report)
        self.assertIn(">> Lock-in: 3 years", report)
        self.assertIn("0.790000", report)
        self.assertIn("0.210000", report)


if __name__ == "__main__":
    unittest.main()
