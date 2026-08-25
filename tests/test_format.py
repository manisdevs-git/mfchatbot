"""Phase 5: response-contract formatter (three sentences, one Groww Source, footer)."""

from __future__ import annotations

import unittest
from datetime import date

from src.format import (
    FOOTER_LABEL,
    SOURCE_LABEL,
    cap_sentences,
    format_response,
    split_sentences,
    strip_model_links,
    winning_citation,
)
from src.schemes import AS_OF_DATE

LARGE_CAP_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


def _chunk(**overrides: object) -> dict:
    base = {
        "text": "Exit load of 1% if redeemed within 1 year.",
        "scheme_id": "hdfc-large-cap-fund-direct-growth",
        "source_url": LARGE_CAP_URL,
        "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
        "as_of_date": AS_OF_DATE,
    }
    base.update(overrides)
    return base


class SentenceCapTests(unittest.TestCase):
    def test_keeps_decimals_as_one_sentence(self) -> None:
        sentences = split_sentences("The expense ratio is 1.03%. Minimum SIP is 100.")
        self.assertEqual(sentences, ["The expense ratio is 1.03%.", "Minimum SIP is 100."])

    def test_caps_at_three_sentences(self) -> None:
        body = "One. Two. Three. Four. Five."
        self.assertEqual(cap_sentences(body), "One. Two. Three.")


class LinkStripTests(unittest.TestCase):
    def test_strips_urls_and_model_source_lines(self) -> None:
        body = (
            "Exit load is 1%. See https://www.moneycontrol.com/mf\n"
            "Source: https://www.hdfcfund.com/factsheet\n"
            f"Last updated from sources: {date.today().isoformat()}"
        )
        cleaned = strip_model_links(body)
        self.assertNotIn("http", cleaned)
        self.assertNotIn("moneycontrol", cleaned)
        self.assertNotIn("hdfcfund.com", cleaned)
        self.assertNotIn(SOURCE_LABEL, cleaned)
        self.assertNotIn(FOOTER_LABEL, cleaned)


class FormatContractTests(unittest.TestCase):
    def test_appends_winning_chunk_url_and_manifest_date(self) -> None:
        text = format_response(
            "The exit load is 1% if redeemed within 1 year.",
            _chunk(),
        )
        self.assertIn("The exit load is 1% if redeemed within 1 year.", text)
        self.assertEqual(text.count("https://"), 1)
        self.assertIn(f"{SOURCE_LABEL} {LARGE_CAP_URL}", text)
        self.assertIn(f"{FOOTER_LABEL} {AS_OF_DATE}", text)
        self.assertNotEqual(AS_OF_DATE, date.today().isoformat())
        self.assertNotIn(date.today().isoformat(), text)

    def test_drops_extra_model_links_and_keeps_one_groww_source(self) -> None:
        text = format_response(
            "Exit load is 1%. Details at https://groww.in/p/exit-load-in-mutual-funds "
            "and https://www.moneycontrol.com/x",
            _chunk(),
        )
        self.assertEqual(text.count("https://"), 1)
        self.assertIn(LARGE_CAP_URL, text)
        self.assertNotIn("moneycontrol", text)
        self.assertNotIn("groww.in/p/exit-load", text)

    def test_does_not_invent_a_citation_without_a_groww_chunk(self) -> None:
        text = format_response(
            "Exit load is 1%.",
            _chunk(source_url="https://www.hdfcfund.com/factsheet"),
        )
        self.assertNotIn(SOURCE_LABEL, text)
        self.assertNotIn("hdfcfund.com", text)
        self.assertEqual(winning_citation(_chunk(source_url="")), (None, None))
