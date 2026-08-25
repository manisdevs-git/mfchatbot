"""Phase 6 FastAPI: /health and POST /v1/ask. Mock handle / retrieve / index."""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import CORPUS_UNAVAILABLE, app
from src.pipeline import PipelineResult
from src.refuse import ADVISORY_REFUSAL, INCOMPLETE_EMPTY, PII_REFUSAL
from src.schemes import AS_OF_DATE, EDUCATION_URL

LARGE_CAP_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
EXIT_QUERY = "What is the exit load of HDFC Large Cap Fund Direct Growth?"
ADVISORY_QUERY = "Should I invest in this fund?"
PII_TOKEN = "ABCDE1234F"
PII_QUERY = f"What is the exit load of HDFC Large Cap Fund Direct Growth {PII_TOKEN}"

LARGE_CAP_CHUNK = {
    "text": "Exit load of 1% if redeemed within 1 year.",
    "scheme_id": "hdfc-large-cap-fund-direct-growth",
    "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
    "as_of_date": AS_OF_DATE,
    "source_url": LARGE_CAP_URL,
    "doc_type": "groww_scheme",
}


def _factual_result(**overrides: object) -> PipelineResult:
    values: dict = {
        "intent": "factual",
        "scheme_id": "hdfc-large-cap-fund-direct-growth",
        "topic": "exit_load",
        "allow_retrieve": True,
        "allow_gemini": True,
        "text": (
            "Exit load of 1% if redeemed within 1 year.\n\n"
            f"Source: {LARGE_CAP_URL}\n\n"
            f"Last updated from sources: {AS_OF_DATE}"
        ),
        "chunks": [LARGE_CAP_CHUNK],
    }
    values.update(overrides)
    return PipelineResult(**values)


class HealthTests(unittest.TestCase):
    def test_health_reports_ok_and_index_flag(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["index_ready"])

    def test_health_stays_ok_when_index_is_missing(self) -> None:
        with patch("api.main.index_ready", return_value=False):
            response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["index_ready"])


class AskContractTests(unittest.TestCase):
    def test_factual_returns_json_contract_without_echoing_query(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("api.main.handle", return_value=_factual_result()) as mocked:
                response = TestClient(app).post("/v1/ask", json={"query": EXIT_QUERY})
        mocked.assert_called_once_with(EXIT_QUERY, force_extractive=False)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {"text", "intent", "scheme_id", "topic", "source_url", "as_of_date", "pii_blocked"},
        )
        self.assertEqual(body["intent"], "factual")
        self.assertEqual(body["scheme_id"], "hdfc-large-cap-fund-direct-growth")
        self.assertEqual(body["topic"], "exit_load")
        self.assertEqual(body["source_url"], LARGE_CAP_URL)
        self.assertEqual(body["as_of_date"], AS_OF_DATE)
        self.assertFalse(body["pii_blocked"])
        self.assertIn("1%", body["text"])
        self.assertIn(LARGE_CAP_URL, body["text"])
        self.assertIn(f"Last updated from sources: {AS_OF_DATE}", body["text"])
        self.assertNotIn("query", body)
        self.assertNotIn(EXIT_QUERY, response.text)

    def test_extractive_flag_is_passed_to_handle(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("api.main.handle", return_value=_factual_result()) as mocked:
                response = TestClient(app).post(
                    "/v1/ask",
                    json={"query": EXIT_QUERY, "extractive": True},
                )
        self.assertEqual(response.status_code, 200)
        mocked.assert_called_once_with(EXIT_QUERY, force_extractive=True)

    def test_advisory_is_200_with_primer_and_does_not_call_gemini(self) -> None:
        result = PipelineResult(
            intent="advisory",
            scheme_id=None,
            topic=None,
            allow_retrieve=True,
            allow_gemini=True,
            text=ADVISORY_REFUSAL,
            chunks=[],
        )
        with patch("api.main.index_ready", return_value=True):
            with patch("api.main.handle", return_value=result):
                with patch("src.generate.call_gemini") as gemini:
                    response = TestClient(app).post("/v1/ask", json={"query": ADVISORY_QUERY})
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["intent"], "advisory")
        self.assertEqual(body["text"], ADVISORY_REFUSAL)
        self.assertIn(EDUCATION_URL, body["text"])
        self.assertEqual(body["text"].count("https://"), 1)
        self.assertFalse(body["pii_blocked"])
        self.assertIsNone(body["source_url"])

    def test_pii_sets_flag_and_omits_identifier_from_body_and_logs(self) -> None:
        result = PipelineResult(
            intent="factual",
            scheme_id="hdfc-large-cap-fund-direct-growth",
            topic="exit_load",
            allow_retrieve=True,
            allow_gemini=True,
            text=PII_REFUSAL,
            chunks=[],
        )
        with patch("api.main.index_ready", return_value=True):
            with patch("api.main.handle", return_value=result):
                with patch("src.generate.call_gemini") as gemini:
                    with self.assertLogs("api", level="INFO") as captured:
                        response = TestClient(app).post("/v1/ask", json={"query": PII_QUERY})
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["pii_blocked"])
        self.assertEqual(body["text"], PII_REFUSAL)
        self.assertNotIn(PII_TOKEN, response.text)
        self.assertNotIn(PII_TOKEN, body["text"])
        self.assertNotIn("query", body)
        logs = "\n".join(captured.output)
        self.assertNotIn(PII_TOKEN, logs)
        self.assertIn("pii_blocked=True", logs)

    def test_empty_query_is_400_with_incomplete_copy(self) -> None:
        with patch("api.main.handle") as mocked:
            with patch("api.main.index_ready", return_value=True):
                response = TestClient(app).post("/v1/ask", json={"query": "   "})
        mocked.assert_not_called()
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["text"], INCOMPLETE_EMPTY)
        self.assertEqual(body["intent"], "incomplete")
        self.assertFalse(body["pii_blocked"])
        self.assertNotIn("query", body)

    def test_missing_index_is_503_and_skips_handle(self) -> None:
        with patch("api.main.index_ready", return_value=False):
            with patch("api.main.handle") as mocked:
                with patch("src.generate.call_gemini") as gemini:
                    response = TestClient(app).post("/v1/ask", json={"query": EXIT_QUERY})
        mocked.assert_not_called()
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["text"], CORPUS_UNAVAILABLE)
        self.assertFalse(body["pii_blocked"])
        self.assertNotIn(EXIT_QUERY, response.text)


class AskPipelineTests(unittest.TestCase):
    """Call the real handle path with retrieve mocked. No Gemini."""

    def test_factual_handle_fills_citation_fields(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[LARGE_CAP_CHUNK]):
                response = TestClient(app).post(
                    "/v1/ask",
                    json={"query": EXIT_QUERY, "extractive": True},
                )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["intent"], "factual")
        self.assertEqual(body["scheme_id"], "hdfc-large-cap-fund-direct-growth")
        self.assertEqual(body["topic"], "exit_load")
        self.assertEqual(body["source_url"], LARGE_CAP_URL)
        self.assertEqual(body["as_of_date"], AS_OF_DATE)
        self.assertFalse(body["pii_blocked"])
        self.assertLessEqual(
            body["text"].split("Source:")[0].count("."),
            3,
        )
        self.assertEqual(body["text"].count("https://"), 1)
        self.assertIn(f"Source: {LARGE_CAP_URL}", body["text"])
        self.assertIn(f"Last updated from sources: {AS_OF_DATE}", body["text"])

    def test_advisory_handle_refuses_without_gemini(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[]):
                with patch("src.generate.call_gemini") as gemini:
                    response = TestClient(app).post("/v1/ask", json={"query": ADVISORY_QUERY})
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["text"], ADVISORY_REFUSAL)
        self.assertIn(EDUCATION_URL, body["text"])
        self.assertFalse(body["pii_blocked"])
        self.assertIsNone(body["source_url"])
        self.assertIsNone(body["as_of_date"])

    def test_pii_handle_refuses_without_gemini_or_token(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[LARGE_CAP_CHUNK]):
                with patch("src.generate.call_gemini") as gemini:
                    with self.assertLogs("api", level="INFO") as captured:
                        response = TestClient(app).post("/v1/ask", json={"query": PII_QUERY})
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["pii_blocked"])
        self.assertEqual(body["text"], PII_REFUSAL)
        self.assertIsNone(body["source_url"])
        self.assertNotIn(PII_TOKEN, response.text)
        self.assertNotIn(PII_TOKEN, "\n".join(captured.output))


class CorsTests(unittest.TestCase):
    def test_local_vite_origin_is_allowed(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            response = TestClient(app).get(
                "/health",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://127.0.0.1:5173",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
