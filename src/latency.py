"""Probe the live ask path and return a layer-by-layer latency report.

GET /latency never logs the raw question. Canned probes are not PII.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ingest.embed_index import load_model
from src.generate import pii_block_for_gemini
from src.pipeline import handle
from src.timing import Stopwatch, server_timing_header, span_if

MODES = ("full", "extractive", "catalog")

PROBES: dict[str, tuple[str, str]] = {
    "full": (
        "factual_expense_ratio",
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
    ),
    "extractive": (
        "factual_expense_ratio",
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
    ),
    "catalog": (
        "catalog_exit_load",
        "Show exit loads of all schemes in a table",
    ),
}


class LatencyError(ValueError):
    """The latency probe could not run."""


def measure_latency(
    *,
    mode: str = "full",
    query: str | None = None,
    check_index: Callable[[], bool],
) -> tuple[dict[str, Any], str]:
    """Run one timed ask. Returns (JSON body, Server-Timing header)."""
    chosen = (mode or "full").strip().lower()
    if chosen not in MODES:
        raise LatencyError(f"mode must be one of: {', '.join(MODES)}")

    probe_id, canned = PROBES[chosen]
    text = (query or "").strip()
    if text:
        probe_id = "custom"
        question = text
    else:
        question = canned

    watch = Stopwatch()
    encoder_cached = load_model.cache_info().currsize > 0
    watch.meta["encoder_cached"] = encoder_cached
    with span_if(watch, "index_ready", "Index ready check", "api"):
        ready = bool(check_index())
    if not ready:
        layers = watch.finalize(writer="skipped")
        server_ms = round(watch.elapsed_ms(), 3)
        body = {
            "ok": False,
            "index_ready": False,
            "encoder_cached": encoder_cached,
            "mode": chosen,
            "probe": probe_id,
            "intent": None,
            "scheme_id": None,
            "topic": None,
            "writer": "skipped",
            "chunks": 0,
            "pii_blocked": False,
            "layers": [layer.as_dict() for layer in layers],
            "server_ms": server_ms,
        }
        return body, server_timing_header(layers, server_ms)

    result = handle(
        question,
        force_extractive=chosen == "extractive",
        watch=watch,
    )
    encoder_cached = bool(watch.meta.get("encoder_cached", encoder_cached))
    writer = str(watch.meta.get("writer") or "unknown")
    layers = watch.finalize(writer=writer)
    server_ms = round(watch.elapsed_ms(), 3)
    body = {
        "ok": True,
        "index_ready": True,
        "encoder_cached": encoder_cached,
        "mode": chosen,
        "probe": probe_id,
        "intent": result.intent,
        "scheme_id": result.scheme_id,
        "topic": result.topic,
        "writer": writer,
        "chunks": len(result.chunks),
        "pii_blocked": pii_block_for_gemini(question) is not None,
        "layers": [layer.as_dict() for layer in layers],
        "server_ms": server_ms,
    }
    return body, server_timing_header(layers, server_ms)
