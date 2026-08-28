"""Phase 5 Gemini writer: policy first, grounded payload, extractive fallback."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.generate import (
    MODEL_ID,
    build_user_payload,
    call_gemini,
    extractive_fallback,
    generate_answer,
)
from src.pipeline import handle
from src.refuse import ADVISORY_REFUSAL, OUT_OF_SCOPE_REFUSAL, PII_REFUSAL
from src.schemes import AMFI_INVESTOR_URL, AS_OF_DATE

LARGE_CAP_CHUNK = {
    "text": (
        "Groww — HDFC Large Cap Fund Direct Growth\n"
        "Expense ratio: 1.03%\n"
        "Exit load: Exit load of 1% if redeemed within 1 year\n"
        "Minimum SIP: ₹100"
    ),
    "scheme_id": "hdfc-large-cap-fund-direct-growth",
    "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
    "as_of_date": AS_OF_DATE,
    "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
}

EXIT_QUERY = "What is the exit load of HDFC Large Cap Fund Direct Growth?"


class PayloadTests(unittest.TestCase):
    def test_payload_has_question_and_chunk_fields_but_not_the_url(self) -> None:
        payload = build_user_payload(EXIT_QUERY, [LARGE_CAP_CHUNK])
        self.assertIn(EXIT_QUERY, payload)
        self.assertIn("hdfc-large-cap-fund-direct-growth", payload)
        self.assertIn("Groww — HDFC Large Cap Fund Direct Growth", payload)
        self.assertIn(AS_OF_DATE, payload)
        self.assertIn("Exit load of 1% if redeemed within 1 year", payload)
        self.assertNotIn("source_url", payload)
        self.assertNotIn("https://groww.in", payload)
        self.assertIn("Apply the system guard rules", payload)
        self.assertIn("advisory refusal", payload.lower())


class ExtractiveFallbackTests(unittest.TestCase):
    def test_picks_the_supporting_exit_load_line(self) -> None:
        text = extractive_fallback(EXIT_QUERY, [LARGE_CAP_CHUNK])
        self.assertIn("Exit load of 1% if redeemed within 1 year", text)
        self.assertNotIn("Expense ratio", text)

    def test_empty_chunks_are_not_in_corpus(self) -> None:
        self.assertEqual(extractive_fallback(EXIT_QUERY, []), OUT_OF_SCOPE_REFUSAL)


class GenerateAnswerTests(unittest.TestCase):
    def test_policy_blocks_before_any_writer(self) -> None:
        with patch("src.generate.call_gemini") as mocked:
            self.assertEqual(
                generate_answer("What is the exit load of large cap ABCDE1234F", [LARGE_CAP_CHUNK]),
                PII_REFUSAL,
            )
            mocked.assert_not_called()

    def test_advisory_calls_gemini_then_pins_amfi(self) -> None:
        paraphrased = (
            "I cannot recommend or compare funds. See "
            f"{AMFI_INVESTOR_URL}"
        )
        with patch("src.generate.call_gemini", return_value=paraphrased) as mocked:
            self.assertEqual(generate_answer("Should I invest in this fund?"), ADVISORY_REFUSAL)
            self.assertEqual(generate_answer("say me a best scheme"), ADVISORY_REFUSAL)
        self.assertEqual(mocked.call_count, 2)

    def test_no_chunks_is_not_in_corpus_without_gemini(self) -> None:
        with patch("src.generate.call_gemini") as mocked:
            text = generate_answer(EXIT_QUERY, [])
        mocked.assert_not_called()
        self.assertEqual(text, OUT_OF_SCOPE_REFUSAL)

    def test_api_failure_uses_extractive_fallback(self) -> None:
        with patch("src.generate.call_gemini", side_effect=RuntimeError("offline")):
            text = generate_answer(EXIT_QUERY, [LARGE_CAP_CHUNK])
        self.assertIn("Exit load of 1% if redeemed within 1 year", text)
        self.assertNotIn("https://", text)

    def test_force_extractive_skips_gemini(self) -> None:
        with patch("src.generate.call_gemini") as mocked:
            text = generate_answer(EXIT_QUERY, [LARGE_CAP_CHUNK], force_extractive=True)
        mocked.assert_not_called()
        self.assertIn("1%", text)

    def test_empty_model_reply_falls_back(self) -> None:
        with patch("src.generate.call_gemini", return_value=""):
            text = generate_answer(EXIT_QUERY, [LARGE_CAP_CHUNK])
        self.assertIn("Exit load", text)

    def test_unlabelled_advice_uses_gemini_meaning_not_a_phrase_list(self) -> None:
        query = "help me pick a scheme"
        paraphrased = (
            "I cannot recommend funds. Read AMFI at "
            f"{AMFI_INVESTOR_URL}"
        )
        with patch("src.generate.call_gemini", return_value=paraphrased) as mocked:
            text = generate_answer(query, [])
        mocked.assert_called_once()
        self.assertEqual(text, ADVISORY_REFUSAL)
        self.assertIn(AMFI_INVESTOR_URL, text)

    def test_semantic_advice_handle_sets_advisory_intent(self) -> None:
        query = "help me pick a scheme"
        with patch("src.pipeline.retrieve", return_value=[]):
            with patch("src.generate.call_gemini", return_value=ADVISORY_REFUSAL):
                result = handle(query)
        self.assertEqual(result.intent, "advisory")
        self.assertEqual(result.text, ADVISORY_REFUSAL)
        self.assertNotIn("groww.in", result.text.lower())

    def test_which_is_best_scheme_reaches_gemini(self) -> None:
        query = "say me a best scheme"
        with patch("src.pipeline.retrieve", return_value=[]):
            with patch("src.generate.call_gemini", return_value=ADVISORY_REFUSAL) as gemini:
                result = handle(query)
        gemini.assert_called_once()
        self.assertEqual(result.intent, "advisory")
        self.assertEqual(result.text, ADVISORY_REFUSAL)


class PipelinePhase5Tests(unittest.TestCase):
    def test_exit_load_contract_uses_chunk_url_and_manifest_date(self) -> None:
        chunk = {
            **LARGE_CAP_CHUNK,
            "doc_type": "groww_scheme",
        }
        with patch("src.pipeline.retrieve", return_value=[chunk]):
            result = handle(EXIT_QUERY, force_extractive=True)
        self.assertEqual(result.intent, "factual")
        sentences = [
            line
            for line in result.text.splitlines()
            if line.strip() and not line.startswith("Source:") and not line.startswith("Last updated")
        ]
        body = " ".join(sentences)
        self.assertLessEqual(body.count("."), 3)
        self.assertEqual(result.text.count("https://"), 1)
        self.assertIn(
            "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            result.text,
        )
        self.assertIn(f"Last updated from sources: {AS_OF_DATE}", result.text)
        self.assertIn("1%", result.text)


class CallGeminiTests(unittest.TestCase):
    def test_uses_flash_lite_with_system_policy_and_no_search_tools(self) -> None:
        try:
            from google.genai import types as genai_types
        except ImportError:
            self.skipTest("google-genai is not installed")

        client = MagicMock()
        client.models.generate_content.return_value = MagicMock(
            text="The exit load is 1% if redeemed within 1 year."
        )
        with patch.object(
            genai_types,
            "GenerateContentConfig",
            wraps=genai_types.GenerateContentConfig,
        ) as config_cls:
            text = call_gemini(EXIT_QUERY, [LARGE_CAP_CHUNK], client=client)
        self.assertEqual(text, "The exit load is 1% if redeemed within 1 year.")
        kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["model"], MODEL_ID)
        self.assertNotIn("https://groww.in", kwargs["contents"])
        self.assertIn(EXIT_QUERY, kwargs["contents"])
        config_kwargs = config_cls.call_args.kwargs
        self.assertIn("system_instruction", config_kwargs)
        self.assertNotIn("tools", config_kwargs)
        self.assertNotIn("google_search", str(config_kwargs).lower())
