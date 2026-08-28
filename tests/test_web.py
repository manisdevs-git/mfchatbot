"""Phase 7 UI contract: welcome, examples, disclaimer, no Gemini key."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
APP = (WEB / "src" / "App.tsx").read_text(encoding="utf-8")
ASK = (WEB / "src" / "ask.ts").read_text(encoding="utf-8")
MAIN = (WEB / "src" / "main.tsx").read_text(encoding="utf-8")
LATENCY = (WEB / "src" / "Latency.tsx").read_text(encoding="utf-8")
SCHEDULER = (WEB / "src" / "Scheduler.tsx").read_text(encoding="utf-8")
ENV_EXAMPLE = (WEB / ".env.example").read_text(encoding="utf-8")


class WebContractTests(unittest.TestCase):
    def test_layout_files_exist(self) -> None:
        required = (
            WEB / "package.json",
            WEB / "index.html",
            WEB / ".env.example",
            WEB / "src" / "App.tsx",
            WEB / "src" / "ask.ts",
            WEB / "src" / "Latency.tsx",
            WEB / "src" / "Scheduler.tsx",
            WEB / "vercel.json",
        )
        missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        self.assertEqual(missing, [], f"missing Phase 7 files: {missing}")

    def test_welcome_examples_and_disclaimer(self) -> None:
        self.assertIn("Groww’s HDFC", APP)
        self.assertIn("Limited FAQ", APP)
        self.assertIn("Available schemes", APP)
        self.assertIn("{DISCLAIMER}", APP)
        self.assertIn("What is the expense ratio of HDFC Large Cap Fund Direct Growth?", ASK)
        self.assertIn("What is the exit load of HDFC ELSS Tax Saver Direct Plan?", ASK)
        self.assertIn("What is the minimum SIP amount for HDFC Mid Cap Fund Direct Growth?", ASK)
        self.assertIn("What is the NAV of HDFC Large Cap Fund Direct Growth?", ASK)
        self.assertIn(
            "What is the riskometer of HDFC Gold ETF Fund of Fund Direct Plan Growth?",
            ASK,
        )
        self.assertIn("What is the benchmark of HDFC Small Cap Fund Direct Growth?", ASK)
        self.assertIn("Facts-only. No investment advice.", ASK)
        for title in (
            "HDFC Large Cap Fund Direct Growth",
            "HDFC Mid Cap Fund Direct Growth",
            "HDFC Small Cap Fund Direct Growth",
            "HDFC Gold ETF Fund of Fund Direct Plan Growth",
            "HDFC ELSS Tax Saver Fund Direct Plan Growth",
        ):
            self.assertIn(title, ASK)

    def test_pii_blocked_does_not_keep_user_text(self) -> None:
        self.assertIn("if (response.pii_blocked)", ASK)
        self.assertIn("return [...history, assistant]", ASK)
        self.assertNotIn("localStorage", APP)
        self.assertNotIn("sessionStorage", APP)

    def test_send_is_disabled_while_busy(self) -> None:
        self.assertIn("disabled={busy}", APP)
        self.assertIn("disabled={!draft.trim()}", APP)
        self.assertIn("stopAsk", APP)
        self.assertIn("AbortController", APP)
        self.assertIn("controller.signal", APP)

    def test_env_example_is_api_base_only(self) -> None:
        self.assertIn("VITE_API_BASE_URL=", ENV_EXAMPLE)
        self.assertNotIn("GEMINI", ENV_EXAMPLE)
        self.assertNotIn("GEMINI", ASK)
        self.assertNotIn("GEMINI", APP)
        self.assertNotIn("GEMINI", LATENCY)
        self.assertNotIn("GEMINI", MAIN)
        self.assertNotIn("GEMINI", SCHEDULER)

    def test_latency_review_is_routed_and_measures_layers(self) -> None:
        self.assertNotIn('href="/latency"', APP)
        self.assertNotIn("latency-link", APP)
        self.assertIn('path === \'/latency\'', MAIN)
        self.assertIn("fetchLatency", ASK)
        self.assertIn("Frontend → backend", LATENCY)
        self.assertIn("/latency", ASK)
        self.assertIn("extractive", LATENCY)
        self.assertIn("catalog", LATENCY)
        self.assertIn("fe_network", LATENCY)
        vercel = (WEB / "vercel.json").read_text(encoding="utf-8")
        self.assertIn("/index.html", vercel)

    def test_scheduler_page_is_routed_without_chat_link(self) -> None:
        self.assertNotIn('href="/scheduler"', APP)
        self.assertIn('path === \'/scheduler\'', MAIN)
        self.assertIn("fetchSchedules", ASK)
        self.assertIn("Pause", SCHEDULER)
        self.assertIn("Delete", SCHEDULER)
        self.assertIn("Run history", SCHEDULER)
        self.assertIn("/v1/schedules", ASK)
        self.assertIn("IST", SCHEDULER)
        self.assertIn("Railway", SCHEDULER)
        self.assertIn("Run now", SCHEDULER)

    def test_chat_input_has_no_identity_fields(self) -> None:
        lowered = APP.lower()
        self.assertNotIn('type="email"', lowered)
        self.assertNotIn('type="tel"', lowered)
        self.assertNotIn("aadhaar", lowered)
        self.assertNotIn('name="pan"', lowered)
        self.assertNotIn("account number", lowered)

    def test_scheme_click_lands_in_chat(self) -> None:
        self.assertIn("pickScheme", APP)
        self.assertIn("focusDraft(scheme.title)", APP)
        self.assertIn("What is the ${example.topic} of ${picked.title}?", APP)
        self.assertIn("clearPrompt", APP)
        self.assertIn("clearHistory", APP)
        self.assertIn("ClearIcon", APP)
        self.assertIn("SparkIcon", APP)
        self.assertIn("SweepIcon", APP)
        self.assertIn("Clear history", APP)
        self.assertIn("is-clearing", APP)
        self.assertIn('aria-label="About"', APP)
        self.assertIn("info-btn", APP)
        self.assertIn("Sample FAQs", APP)
        self.assertIn("className=\"send\"", APP)
        self.assertIn("EnterIcon", APP)
        self.assertIn("pickedTopic", APP)
        self.assertIn("setPickedTopic(null)", APP)
        self.assertNotIn("How to ask a question", APP)
        self.assertNotIn("{showFacts ? 'Close' : 'About'}", APP)
        self.assertNotIn("facts-toggle", APP)
        self.assertIn("{scheme.code}", APP)
        self.assertIn("HDFC LG DG", ASK)
        self.assertIn("scheme-tip", APP)
        self.assertIn("pickTopic", APP)
        self.assertIn("pickHowToQuestion", APP)
        self.assertIn("howto-box", APP)
        self.assertIn("transcript-log", APP)
        self.assertIn("chat-step", APP)
        self.assertIn("className=\"cite\"", APP)
        self.assertIn("{sourceUrl}", APP)
        self.assertNotIn(">Source<", APP)
        self.assertIn("peelCitation", APP)
        self.assertIn("peelCitation", ASK)
        self.assertNotIn("Q:", APP)
        self.assertNotIn("Ans:", APP)
        self.assertNotIn("ledger-row", APP)
        self.assertNotIn("Fact ledger", APP)
        self.assertIn("focusDraft(example.question)", APP)
        self.assertNotIn("pickHowToAsk", APP)
        self.assertNotIn('className="starter"', APP)
        self.assertNotIn("scheme-card", APP)

    def test_catalog_table_is_rendered(self) -> None:
        self.assertIn("parseMarkdownTable", APP)
        self.assertIn("<table>", APP)


if __name__ == "__main__":
    unittest.main()
