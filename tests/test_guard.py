"""Retrieve routing vs Gemini-side policy. No Chroma, no live Gemini."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.generate import (
    generate_answer,
    llm_system_prompt,
    pii_block_for_gemini,
    policy_block_for_gemini,
    screen_model_output,
)
from src.guard import GuardDecision, classify, contains_pii
from src.pipeline import handle
from src.refuse import (
    ADVISORY_REFUSAL,
    EDUCATION_URL,
    PII_REFUSAL,
    format_refusal,
)
from src.schemes import SCHEME_URLS


class PiiTests(unittest.TestCase):
    def test_pan_is_pii(self) -> None:
        self.assertTrue(contains_pii("What is the TER ABCDE1234F"))

    def test_expense_ratio_and_sip_are_not_pii(self) -> None:
        self.assertFalse(contains_pii("Expense ratio 1.25% and SIP 500"))
        decision = classify("What is the expense ratio of HDFC Large Cap Fund Direct Growth?")
        self.assertEqual(decision.intent, "factual")
        self.assertFalse(contains_pii("What is the expense ratio of HDFC Large Cap Fund Direct Growth?"))

    def test_email_and_phone_are_pii(self) -> None:
        self.assertTrue(contains_pii("mail me at user@example.com"))
        self.assertTrue(contains_pii("call 9876543210"))


class IntentTableTests(unittest.TestCase):
    def test_advisory_should_i_invest_is_routed_not_refused(self) -> None:
        decision = classify("Should I invest in this fund?")
        self.assertEqual(decision.intent, "advisory")
        self.assertTrue(decision.allow_retrieve)
        self.assertTrue(decision.allow_gemini)

    def test_advisory_which_is_better(self) -> None:
        decision = classify("Which fund is better?")
        self.assertEqual(decision.intent, "advisory")
        self.assertTrue(decision.allow_gemini)

    def test_advisory_compare_two_schemes(self) -> None:
        decision = classify("Compare expense ratio of large cap and mid cap")
        self.assertEqual(decision.intent, "advisory")

    def test_performance_three_year_return(self) -> None:
        decision = classify("What was the 3-year return of the Large Cap fund?")
        self.assertEqual(decision.intent, "performance")
        self.assertEqual(decision.scheme_id, "hdfc-large-cap-fund-direct-growth")
        self.assertTrue(decision.allow_retrieve)
        self.assertTrue(decision.allow_gemini)

    def test_factual_large_cap_expense_ratio(self) -> None:
        decision = classify("What is the expense ratio of HDFC Large Cap Fund Direct Growth?")
        self.assertEqual(decision.intent, "factual")
        self.assertEqual(decision.scheme_id, "hdfc-large-cap-fund-direct-growth")
        self.assertEqual(decision.topic, "expense_ratio")
        self.assertTrue(decision.allow_retrieve)
        self.assertTrue(decision.allow_gemini)

    def test_factual_nav_is_not_performance(self) -> None:
        decision = classify("What is the current NAV of HDFC Large Cap Fund Direct Growth?")
        self.assertEqual(decision.intent, "factual")
        self.assertEqual(decision.scheme_id, "hdfc-large-cap-fund-direct-growth")
        self.assertEqual(decision.topic, "nav")
        self.assertTrue(decision.allow_retrieve)
        self.assertTrue(decision.allow_gemini)

    def test_process_capital_gains(self) -> None:
        decision = classify("How do I download a capital gains report?")
        self.assertEqual(decision.intent, "process")
        self.assertEqual(decision.scheme_id, "generic")
        self.assertTrue(decision.allow_retrieve)

    def test_out_of_scope_other_amc(self) -> None:
        decision = classify("SBI Bluechip expense ratio")
        self.assertEqual(decision.intent, "out_of_scope")
        self.assertTrue(decision.allow_gemini)

    def test_pan_does_not_change_guard_intent(self) -> None:
        decision = classify("What is the exit load of large cap ABCDE1234F")
        self.assertEqual(decision.intent, "factual")
        self.assertEqual(decision.topic, "exit_load")
        self.assertTrue(decision.allow_retrieve)

    def test_advisory_wins_over_factual_in_same_turn(self) -> None:
        decision = classify("Should I invest? Also what is TER of large cap?")
        self.assertEqual(decision.intent, "advisory")

    def test_incomplete_topic_without_scheme(self) -> None:
        decision = classify("What is the expense ratio?")
        self.assertEqual(decision.intent, "incomplete")
        self.assertTrue(decision.allow_gemini)


class PipelineRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.pipeline.retrieve", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_policy_intents_are_allowed_through_to_retrieve(self) -> None:
        routed = (
            "Should I invest in this fund?",
            "Which fund is better?",
            "What was the 3-year return of the Large Cap fund?",
            "SBI Bluechip expense ratio",
            "What is the expense ratio?",
        )
        for query in routed:
            result = handle(query)
            self.assertTrue(result.allow_retrieve, query)
            self.assertTrue(result.allow_gemini, query)
            self.assertEqual(result.text, policy_block_for_gemini(query), query)

    def test_empty_is_the_only_front_door_stop(self) -> None:
        result = handle("   ")
        self.assertFalse(result.allow_retrieve)
        self.assertFalse(result.allow_gemini)

    @patch("src.pipeline.classify")
    def test_handle_does_not_need_a_model(self, mocked_classify) -> None:
        mocked_classify.return_value = GuardDecision(
            intent="advisory",
            scheme_id=None,
            topic=None,
            allow_retrieve=True,
            allow_gemini=True,
            reason="advisory",
        )
        result = handle("Should I invest in this fund?")
        self.assertEqual(result.text, ADVISORY_REFUSAL)
        mocked_classify.assert_called_once()


class GeminiSideGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch("src.pipeline.retrieve", return_value=[])
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_advisory_refused_only_at_gemini_boundary(self) -> None:
        query = "Which fund is better?"
        self.assertTrue(handle(query).allow_retrieve)
        self.assertEqual(policy_block_for_gemini(query), ADVISORY_REFUSAL)
        self.assertEqual(generate_answer(query, chunks=[{"text": "TER 1%"}]), ADVISORY_REFUSAL)
        self.assertIn(EDUCATION_URL, format_refusal(classify(query)))

    def test_performance_refused_only_at_gemini_boundary(self) -> None:
        query = "What was the 3-year return of the Large Cap fund?"
        result = handle(query)
        self.assertTrue(result.allow_retrieve)
        text = generate_answer(query)
        url = SCHEME_URLS["hdfc-large-cap-fund-direct-growth"]
        self.assertIn(url, text)
        self.assertNotIn("CAGR", text)
        self.assertIn("Last updated from sources:", text)

    def test_out_of_scope_refused_only_at_gemini_boundary(self) -> None:
        query = "SBI Bluechip expense ratio"
        self.assertTrue(handle(query).allow_retrieve)
        text = generate_answer(query)
        self.assertIn("not available on the current Groww pages", text)

    def test_incomplete_refused_only_at_gemini_boundary(self) -> None:
        query = "What is the expense ratio?"
        self.assertTrue(handle(query).allow_retrieve)
        text = generate_answer(query)
        self.assertIn("in-scope scheme", text.lower())

    def test_pan_is_refused_only_at_gemini_boundary(self) -> None:
        query = "What is the exit load of large cap ABCDE1234F"
        self.assertEqual(classify(query).intent, "factual")
        self.assertTrue(handle(query).allow_retrieve)
        self.assertEqual(pii_block_for_gemini(query), PII_REFUSAL)
        self.assertEqual(generate_answer(query, chunks=[{"text": "Exit load 1%"}]), PII_REFUSAL)
        self.assertNotIn("ABCDE1234F", generate_answer(query))

    def test_llm_screen_drops_leaked_pii(self) -> None:
        self.assertEqual(screen_model_output("PAN is ABCDE1234F"), PII_REFUSAL)
        self.assertEqual(screen_model_output("Expense ratio is 1.03%."), "Expense ratio is 1.03%.")

    def test_system_prompt_contains_guard_rules(self) -> None:
        prompt = llm_system_prompt()
        self.assertIn("PII > advisory/compare > performance", prompt)
        self.assertIn("PAN", prompt)
        self.assertIn("which fund is better", prompt)
        self.assertIn("CAGR", prompt)
        self.assertIn("factsheet snapshot", prompt)
        self.assertIn(EDUCATION_URL, prompt)
        self.assertIn("hdfc-large-cap-fund-direct-growth", prompt)
        self.assertIn("At most three sentences", prompt)
        self.assertIn("Incomplete", prompt)

    def test_nav_is_not_refused_at_gemini_boundary(self) -> None:
        query = "What is the current NAV of HDFC Large Cap Fund Direct Growth?"
        self.assertEqual(classify(query).intent, "factual")
        self.assertIsNone(policy_block_for_gemini(query))


if __name__ == "__main__":
    unittest.main()
