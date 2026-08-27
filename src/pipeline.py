"""Retrieve routing → retrieve → generate (policy then writer) → format."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from src.format import format_catalog, format_response
from src.generate import generate_answer, llm_system_prompt, policy_block_for_gemini
from src.guard import GuardDecision, classify
from src.refuse import format_refusal
from src.retrieve import RetrieveError, retrieve
from src.timing import Stopwatch, skip_if, span_if


@dataclass(frozen=True)
class PipelineResult:
    intent: str
    scheme_id: str | None
    topic: str | None
    allow_retrieve: bool
    allow_gemini: bool
    text: str
    chunks: list[dict] = field(default_factory=list)


def handle(
    query: str,
    *,
    force_extractive: bool = False,
    watch: Stopwatch | None = None,
) -> PipelineResult:
    """Route, retrieve official chunks, then apply Gemini-side policy and format."""
    with span_if(watch, "classify", "Classify / route", "api"):
        decision: GuardDecision = classify(query)
    if decision.reason == "empty":
        skip_if(watch, "policy", "Policy gate", "api", "empty query")
        if watch is not None:
            watch.meta["writer"] = "refusal"
        with span_if(watch, "format", "Format + citation", "api"):
            text = format_refusal(decision)
        return PipelineResult(
            intent=decision.intent,
            scheme_id=decision.scheme_id,
            topic=decision.topic,
            allow_retrieve=False,
            allow_gemini=False,
            text=text,
        )
    try:
        chunks = retrieve(query, decision=decision, watch=watch)
    except RetrieveError:
        chunks = []

    with span_if(watch, "policy", "Policy gate", "api"):
        blocked = policy_block_for_gemini(query)
    if blocked is not None:
        skip_if(watch, "gemini", "Gemini writer", "writer", "policy blocked")
        skip_if(watch, "extractive", "Extractive fallback", "writer", "policy blocked")
        if watch is not None:
            watch.meta["writer"] = "refusal"
        skip_if(watch, "format", "Format + citation", "api", "refusal copy")
        return PipelineResult(
            intent=decision.intent,
            scheme_id=decision.scheme_id,
            topic=decision.topic,
            allow_retrieve=True,
            allow_gemini=True,
            chunks=chunks,
            text=blocked,
        )

    if decision.intent == "catalog":
        skip_if(watch, "gemini", "Gemini writer", "writer", "catalog is extractive")
        skip_if(watch, "extractive", "Extractive fallback", "writer", "catalog formatter")
        if watch is not None:
            watch.meta["writer"] = "catalog"
        with span_if(watch, "format", "Format + citation", "api", "catalog table"):
            text = format_catalog(chunks, decision.topic)
        return PipelineResult(
            intent=decision.intent,
            scheme_id=None,
            topic=decision.topic,
            allow_retrieve=True,
            allow_gemini=True,
            chunks=chunks,
            text=text,
        )

    body = generate_answer(
        query,
        chunks,
        force_extractive=force_extractive,
        watch=watch,
    )
    if not chunks:
        skip_if(watch, "format", "Format + citation", "api", "no chunks")
        return PipelineResult(
            intent=decision.intent,
            scheme_id=decision.scheme_id,
            topic=decision.topic,
            allow_retrieve=True,
            allow_gemini=True,
            chunks=[],
            text=body,
        )
    with span_if(watch, "format", "Format + citation", "api"):
        text = format_response(body, chunks[0])
    return PipelineResult(
        intent=decision.intent,
        scheme_id=decision.scheme_id,
        topic=decision.topic,
        allow_retrieve=True,
        allow_gemini=True,
        chunks=chunks,
        text=text,
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Answer a Groww FAQ question. Policy runs at the Gemini boundary."
    )
    parser.add_argument("query", help="User question")
    parser.add_argument(
        "--show-llm-policy",
        action="store_true",
        help="Print the final-LLM system policy (not a model call).",
    )
    parser.add_argument(
        "--gemini-guards",
        action="store_true",
        help="Show the Gemini-side refusal without calling the API.",
    )
    parser.add_argument(
        "--extractive",
        action="store_true",
        help="Skip Gemini and copy the first supporting sentence from the top chunk.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.show_llm_policy:
        print(llm_system_prompt())
    result = handle(args.query, force_extractive=args.extractive)
    print(f"intent={result.intent}")
    print(f"scheme_id={result.scheme_id}")
    print(f"topic={result.topic}")
    print(f"allow_retrieve={result.allow_retrieve}")
    print(f"allow_gemini={result.allow_gemini}")
    print(f"chunks={len(result.chunks)}")
    print("---")
    print(result.text)
    if args.gemini_guards:
        blocked = policy_block_for_gemini(args.query)
        print("--- gemini-side policy ---")
        print(blocked if blocked is not None else "(no Gemini-side refusal; factual/process)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
