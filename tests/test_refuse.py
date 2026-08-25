"""Refusal templates do not leak identifiers or rank funds."""

from __future__ import annotations

import unittest

from src.generate import policy_block_for_gemini
from src.refuse import ADVISORY_REFUSAL, EDUCATION_URL, PII_REFUSAL
from src.schemes import AS_OF_DATE, SCHEME_URLS


class RefuseCopyTests(unittest.TestCase):
    def test_advisory_points_at_one_groww_primer(self) -> None:
        text = policy_block_for_gemini("Should I invest in this fund?")
        self.assertEqual(text, ADVISORY_REFUSAL)
        self.assertEqual(text.count("https://"), 1)
        self.assertIn(EDUCATION_URL, text)

    def test_pii_is_short_and_does_not_repeat_the_token(self) -> None:
        token = "ABCDE1234F"
        text = policy_block_for_gemini(f"exit load of large cap {token}")
        self.assertEqual(text, PII_REFUSAL)
        self.assertNotIn(token, text)

    def test_performance_uses_scheme_url_and_manifest_date(self) -> None:
        text = policy_block_for_gemini("3-year return of HDFC Large Cap Fund Direct Growth")
        self.assertIn(SCHEME_URLS["hdfc-large-cap-fund-direct-growth"], text)
        self.assertIn(AS_OF_DATE, text)
        self.assertNotIn("%", text)

    def test_out_of_scope_names_no_other_amc_page(self) -> None:
        text = policy_block_for_gemini("SBI Bluechip expense ratio")
        self.assertIn("not available on the current groww pages", text.lower())
        self.assertNotIn("sbimf.com", text)


if __name__ == "__main__":
    unittest.main()
