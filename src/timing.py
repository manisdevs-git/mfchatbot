"""Named wall-clock spans for the /latency review. No I/O of its own."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Iterator

import time

LAYER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("index_ready", "Index ready check", "api"),
    ("classify", "Classify / route", "api"),
    ("minilm_load", "MiniLM encoder load", "retrieve"),
    ("query_embed", "Query embedding", "retrieve"),
    ("chroma_open", "Open Chroma collection", "retrieve"),
    ("chroma_search", "Vector search", "retrieve"),
    ("policy", "Policy gate", "api"),
    ("gemini", "Gemini writer", "writer"),
    ("extractive", "Extractive fallback", "writer"),
    ("format", "Format + citation", "api"),
    ("server_other", "API overhead", "api"),
)


@dataclass
class Layer:
    id: str
    label: str
    group: str
    ms: float
    detail: str | None = None
    skipped: bool = False

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "ms": round(self.ms, 3),
            "skipped": self.skipped,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class Stopwatch:
    """Collect spans. `meta` holds writer / encoder flags for the JSON report."""

    layers: list[Layer] = field(default_factory=list)
    meta: dict[str, object] = field(default_factory=dict)
    _started: float = field(default_factory=time.perf_counter)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000.0

    @contextmanager
    def span(
        self,
        layer_id: str,
        label: str,
        group: str,
        detail: str | None = None,
    ) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.layers.append(
                Layer(
                    id=layer_id,
                    label=label,
                    group=group,
                    ms=(time.perf_counter() - start) * 1000.0,
                    detail=detail,
                )
            )

    def skip(
        self,
        layer_id: str,
        label: str,
        group: str,
        detail: str | None = None,
    ) -> None:
        self.layers.append(
            Layer(
                id=layer_id,
                label=label,
                group=group,
                ms=0.0,
                detail=detail,
                skipped=True,
            )
        )

    def finalize(self, *, writer: str) -> list[Layer]:
        """Keep a stable layer list so the review view can compare runs."""
        self.meta["writer"] = writer
        by_id = {layer.id: layer for layer in self.layers}
        measured = sum(layer.ms for layer in self.layers if layer.id != "server_other")
        leftover = max(0.0, self.elapsed_ms() - measured)
        by_id["server_other"] = Layer(
            id="server_other",
            label="API overhead",
            group="api",
            ms=leftover,
            detail="time not in a named span",
        )
        ordered: list[Layer] = []
        for layer_id, label, group in LAYER_SPECS:
            if layer_id in by_id:
                ordered.append(by_id[layer_id])
            else:
                ordered.append(
                    Layer(
                        id=layer_id,
                        label=label,
                        group=group,
                        ms=0.0,
                        skipped=True,
                    )
                )
        self.layers = ordered
        return ordered


def span_if(
    watch: Stopwatch | None,
    layer_id: str,
    label: str,
    group: str,
    detail: str | None = None,
):
    if watch is None:
        return nullcontext()
    return watch.span(layer_id, label, group, detail)


def skip_if(
    watch: Stopwatch | None,
    layer_id: str,
    label: str,
    group: str,
    detail: str | None = None,
) -> None:
    if watch is not None:
        watch.skip(layer_id, label, group, detail)


def server_timing_header(layers: list[Layer], total_ms: float) -> str:
    parts: list[str] = []
    for layer in layers:
        if layer.skipped:
            continue
        parts.append(f"{layer.id};dur={layer.ms:.1f}")
    parts.append(f"total;dur={total_ms:.1f}")
    return ", ".join(parts)
