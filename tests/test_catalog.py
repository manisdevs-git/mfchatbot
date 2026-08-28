"""All-scheme catalog path: route, table format, no Gemini, no re-ingest."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.format import format_catalog
from src.generate import policy_block_for_gemini
from src.guard import classify
from src.pipeline import handle
from src.retrieve import resolve_filters
from src.schemes import AS_OF_DATE, CATALOG_SCHEME_IDS, SCHEME_TITLES, SCHEME_URLS

CATALOG_QUERY = "show me exit loads of all schemes in table"


def _scheme_chunk(scheme_id: str, fact: str) -> dict:
    return {
        "text": f"{SCHEME_TITLES[scheme_id]}\n{fact}",
        "scheme_id": scheme_id,
        "source_url": SCHEME_URLS[scheme_id],
        "source_title": SCHEME_TITLES[scheme_id],
        "as_of_date": AS_OF_DATE,
        "doc_type": "groww_scheme",
        "topic_tags": ["exit_load"],
    }


class CatalogRoutingTests(unittest.TestCase):
    def test_all_schemes_table_is_catalog(self) -> None:
        decision = classify(CATALOG_QUERY)
        self.assertEqual(decision.intent, "catalog")
        self.assertEqual(decision.topic, "exit_load")
        self.assertIsNone(decision.scheme_id)
        self.assertTrue(decision.allow_retrieve)
        self.assertIsNone(policy_block_for_gemini(CATALOG_QUERY))

    def test_plural_topic_still_matches_one_scheme(self) -> None:
        decision = classify("What are the exit loads of HDFC Large Cap Fund Direct Growth?")
        self.assertEqual(decision.intent, "factual")
        self.assertEqual(decision.topic, "exit_load")

    def test_compare_all_schemes_is_catalog_until_gemini_refuses(self) -> None:
        decision = classify("Compare exit loads of all schemes")
        self.assertEqual(decision.intent, "catalog")
        self.assertIsNone(policy_block_for_gemini("Compare exit loads of all schemes"))

    def test_resolve_filters_lists_every_scheme(self) -> None:
        scheme_ids, topic = resolve_filters(CATALOG_QUERY)
        self.assertEqual(scheme_ids, list(CATALOG_SCHEME_IDS))
        self.assertEqual(topic, "exit_load")

    def test_nav_of_all_schemes_is_catalog(self) -> None:
        decision = classify("NAV of all schemes")
        self.assertEqual(decision.intent, "catalog")
        self.assertEqual(decision.topic, "nav")
        self.assertIsNone(policy_block_for_gemini("NAV of all schemes"))


class CatalogFormatTests(unittest.TestCase):
    def test_table_has_one_row_and_url_per_scheme(self) -> None:
        chunks = [
            _scheme_chunk(scheme_id, "Exit load of 1% if redeemed within 1 year.")
            for scheme_id in CATALOG_SCHEME_IDS
        ]
        chunks[3] = _scheme_chunk(
            "hdfc-gold-etf-fund-of-fund-direct-plan-growth",
            "Exit load of 1%, if redeemed within 15 days.",
        )
        chunks[4] = _scheme_chunk(
            "hdfc-elss-tax-saver-fund-direct-plan-growth",
            "Exit load: Nil",
        )
        text = format_catalog(chunks, "exit_load")
        self.assertIn("| Scheme | Exit load | Source |", text)
        for scheme_id in CATALOG_SCHEME_IDS:
            self.assertIn(SCHEME_TITLES[scheme_id], text)
            self.assertIn(SCHEME_URLS[scheme_id], text)
        self.assertIn("15 days", text)
        self.assertIn("Nil", text)
        self.assertIn(f"Last updated from sources: {AS_OF_DATE}", text)
        self.assertEqual(text.count("https://groww.in/mutual-funds/"), 5)

    def test_nav_table_copies_snapshot_line(self) -> None:
        chunks = [
            _scheme_chunk(scheme_id, "NAV: ₹1245.13 as of 2026-08-21")
            for scheme_id in CATALOG_SCHEME_IDS
        ]
        text = format_catalog(chunks, "nav")
        self.assertIn("| Scheme | NAV | Source |", text)
        self.assertIn("NAV: ₹1245.13 as of 2026-08-21", text)


class CatalogPipelineTests(unittest.TestCase):
    def test_handle_builds_table_without_gemini(self) -> None:
        chunks = [
            _scheme_chunk(scheme_id, "Exit load of 1% if redeemed within 1 year.")
            for scheme_id in CATALOG_SCHEME_IDS
        ]
        with patch("src.pipeline.retrieve", return_value=chunks):
            with patch("src.generate.call_gemini", return_value="Exit loads vary by scheme.") as gemini:
                result = handle(CATALOG_QUERY)
        gemini.assert_called_once()
        self.assertEqual(result.intent, "catalog")
        self.assertEqual(len(result.chunks), 5)
        self.assertIn("| Scheme | Exit load | Source |", result.text)
        self.assertIn(SCHEME_TITLES["hdfc-large-cap-fund-direct-growth"], result.text)


if __name__ == "__main__":
    unittest.main()
