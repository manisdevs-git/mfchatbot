"""GET/POST /latency: layer timings without echoing the question."""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from src.schemes import AS_OF_DATE
from src.timing import Stopwatch

LARGE_CAP_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
LARGE_CAP_CHUNK = {
    "text": "Expense ratio: 1.03%. Exit load of 1% if redeemed within 1 year.",
    "scheme_id": "hdfc-large-cap-fund-direct-growth",
    "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
    "as_of_date": AS_OF_DATE,
    "source_url": LARGE_CAP_URL,
    "doc_type": "groww_scheme",
}
PII_TOKEN = "ABCDE1234F"
LAYER_IDS = (
    "index_ready",
    "classify",
    "minilm_load",
    "query_embed",
    "chroma_open",
    "chroma_search",
    "policy",
    "gemini",
    "extractive",
    "format",
    "server_other",
)


def _ids(body: dict) -> list[str]:
    return [str(layer["id"]) for layer in body["layers"]]


def _layer(body: dict, layer_id: str) -> dict:
    for layer in body["layers"]:
        if layer["id"] == layer_id:
            return layer
    raise AssertionError(f"missing layer {layer_id}")


class StopwatchTests(unittest.TestCase):
    def test_span_and_finalize_keep_stable_ids(self) -> None:
        watch = Stopwatch()
        with watch.span("classify", "Classify / route", "api"):
            pass
        layers = watch.finalize(writer="gemini")
        self.assertEqual([layer.id for layer in layers], list(LAYER_IDS))
        self.assertFalse(next(layer for layer in layers if layer.id == "classify").skipped)
        self.assertTrue(next(layer for layer in layers if layer.id == "gemini").skipped)
        self.assertGreaterEqual(watch.elapsed_ms(), 0)


class LatencyRouteTests(unittest.TestCase):
    def test_full_probe_times_gemini_and_does_not_echo_the_question(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[LARGE_CAP_CHUNK]):
                with patch(
                    "src.generate.call_gemini",
                    return_value="Expense ratio is 1.03%.",
                ) as gemini:
                    response = TestClient(app).get("/latency?mode=full")
        gemini.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertIn("timing-allow-origin", {key.lower() for key in response.headers})
        self.assertIn("server-timing", {key.lower() for key in response.headers})
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["mode"], "full")
        self.assertEqual(body["probe"], "factual_expense_ratio")
        self.assertEqual(body["writer"], "gemini")
        self.assertEqual(_ids(body), list(LAYER_IDS))
        self.assertFalse(_layer(body, "gemini")["skipped"])
        self.assertGreaterEqual(body["server_ms"], _layer(body, "gemini")["ms"])
        self.assertNotIn("query", body)
        self.assertNotIn("What is the expense ratio", response.text)
        self.assertNotIn(PII_TOKEN, response.text)

    def test_extractive_probe_skips_gemini(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[LARGE_CAP_CHUNK]):
                with patch("src.generate.call_gemini") as gemini:
                    response = TestClient(app).get("/latency?mode=extractive")
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["writer"], "extractive")
        self.assertTrue(_layer(body, "gemini")["skipped"])
        self.assertFalse(_layer(body, "extractive")["skipped"])

    def test_catalog_probe_skips_gemini(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[LARGE_CAP_CHUNK]):
                with patch("src.generate.call_gemini") as gemini:
                    response = TestClient(app).get("/latency?mode=catalog")
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["intent"], "catalog")
        self.assertEqual(body["writer"], "catalog")
        self.assertTrue(_layer(body, "gemini")["skipped"])

    def test_missing_index_is_503_with_layers(self) -> None:
        with patch("api.main.index_ready", return_value=False):
            with patch("src.pipeline.handle") as mocked:
                response = TestClient(app).get("/latency")
        mocked.assert_not_called()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertFalse(body["index_ready"])
        self.assertEqual(body["writer"], "skipped")
        self.assertEqual(_ids(body), list(LAYER_IDS))

    def test_invalid_mode_is_400(self) -> None:
        response = TestClient(app).get("/latency?mode=nope")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_pii_post_does_not_echo_token_or_call_gemini(self) -> None:
        with patch("api.main.index_ready", return_value=True):
            with patch("src.pipeline.retrieve", return_value=[LARGE_CAP_CHUNK]):
                with patch("src.generate.call_gemini") as gemini:
                    with self.assertLogs("api", level="INFO") as captured:
                        response = TestClient(app).post(
                            "/latency",
                            json={
                                "mode": "full",
                                "query": f"What is the exit load {PII_TOKEN}",
                            },
                        )
        gemini.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["pii_blocked"])
        self.assertEqual(body["writer"], "refusal")
        self.assertNotIn(PII_TOKEN, response.text)
        self.assertNotIn(PII_TOKEN, "\n".join(captured.output))
        self.assertEqual(body["probe"], "custom")

    def test_root_lists_latency(self) -> None:
        response = TestClient(app).get("/")
        self.assertEqual(response.json()["latency"], "GET /latency")

    def test_vercel_origin_can_read_latency(self) -> None:
        with patch("api.main.index_ready", return_value=False):
            response = TestClient(app).get(
                "/latency",
                headers={"Origin": "https://mfchatbot.vercel.app"},
            )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://mfchatbot.vercel.app",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
