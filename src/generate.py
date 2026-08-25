"""Gemini call site. All guard rules are enforced here.

The front door (src/guard.py) only labels the question for retrieve.
Before any API call this module applies the same policy as the system
prompt. PII is refused first so identifiers are never sent to Gemini.
"""

from __future__ import annotations

import os
from typing import Any

from src.format import cap_sentences, split_sentences
from src.guard import classify, contains_pii
from src.refuse import (
    ADVISORY_REFUSAL,
    OUT_OF_SCOPE_REFUSAL,
    PII_REFUSAL,
    format_refusal,
)
from src.schemes import (
    AS_OF_DATE,
    EDUCATION_URL,
    SCHEME_TITLES,
    SCHEME_URLS,
)

MODEL_ID = "gemini-3.5-flash-lite"

POLICY_INTENTS = frozenset(
    {"advisory", "performance", "out_of_scope", "incomplete"}
)


def llm_system_prompt() -> str:
    """Full guard policy for gemini-3.5-flash-lite."""
    scheme_lines = "\n".join(
        f"- {SCHEME_TITLES[scheme_id]} → {SCHEME_URLS[scheme_id]}"
        for scheme_id in SCHEME_URLS
    )
    return f"""You are a facts-only Groww mutual-fund FAQ writer.

Apply these guard rules to the user question. Priority (first match wins):
PII > advisory/compare > performance > out_of_scope > incomplete > catalog > process/factual.

## PII
If the question contains PAN, Aadhaar, phone, email, OTP, or folio/account
numbers, refuse. Do not repeat the identifier. Reply exactly:
{PII_REFUSAL}

Expense ratio figures (e.g. 1.25%) and SIP amounts (e.g. 500) are not PII.

## Advisory / compare
If the user asks whether to invest, which fund is better, vs/compare/rank,
suitability, or recommendations, refuse. Do not rank schemes. Reply:
{ADVISORY_REFUSAL}

Education link: {EDUCATION_URL}

## Performance
If the user asks for returns, CAGR, XIRR, NAV as a live price, or
"if I invested …", do not calculate and do not quote a return number.
Point only to that scheme's Groww page. Footer date: {AS_OF_DATE}

In-scope scheme pages:
{scheme_lines}

## Out of scope
Other AMCs (SBI, ICICI, …), HDFC schemes not in the five pages, news,
tax planning, or portfolio construction: say the fact is not available
on the current Groww pages. Reply in this spirit:
{OUT_OF_SCOPE_REFUSAL}

## Incomplete
Topic without an in-scope scheme, or a scheme without a topic: ask for
the missing piece. Do not guess a scheme. Two named schemes: ask for one;
do not compare.

## Catalog
If the user asks for one factual topic across all in-scope schemes
(for example "exit loads of all schemes"), do not refuse. The formatter
builds a table from one Groww chunk per scheme. Do not rank or recommend.

## Process
How to download CAS, capital-gains, or ELSS statements: answer only from
retrieved Groww help chunks. Never download on the user's behalf. Never
ask for PAN or folio.

## Factual
Expense ratio, exit load, min SIP, lock-in, riskometer, benchmark: state
only what is in the retrieved chunks. At most three sentences for a
single-scheme answer. No bullets or tables except catalog. Catalog answers
use one row per scheme. Do not invent a number, date, URL, or scheme name.
Do not invent a citation; the formatter attaches Source.

If chunks lack the fact, say it is not available on the current Groww pages.

Do not write a URL, a Source line, or a last-updated line. The formatter
attaches the citation. Do not use parametric knowledge to fill gaps.

Allowed schemes: Large Cap, Mid Cap, Small Cap, Gold ETF FoF, ELSS Tax Saver
(Direct Growth on Groww only).
"""


def pii_block_for_gemini(query: str) -> str | None:
    """Refuse identifiers at the Gemini boundary. Do not send them to the API."""
    if contains_pii(query or ""):
        return PII_REFUSAL
    return None


def policy_block_for_gemini(query: str) -> str | None:
    """Apply every guard at the Gemini boundary. PII wins first."""
    blocked = pii_block_for_gemini(query)
    if blocked is not None:
        return blocked
    decision = classify(query)
    if decision.intent in POLICY_INTENTS:
        return format_refusal(decision)
    return None


def screen_model_output(text: str) -> str:
    """If the model echoed identifiers, replace the reply."""
    body = (text or "").strip()
    if not body:
        return body
    if contains_pii(body):
        return PII_REFUSAL
    return body


_SUPPORT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exit load", ("exit load", "exit-load")),
    ("expense", ("expense ratio", "expense", "ter", "total expense")),
    ("sip", ("sip", "minimum sip", "min. for sip")),
    ("lock", ("lock-in", "lock in", "lockin")),
    ("riskometer", ("riskometer",)),
    ("benchmark", ("benchmark",)),
    ("capital gain", ("capital gain", "cas", "statement")),
    ("download", ("download", "statement", "cas")),
)


class GeminiError(RuntimeError):
    """The Gemini writer could not be called."""


def build_user_payload(query: str, chunks: list[dict]) -> str:
    """Question plus retrieved chunk text. No source_url — the formatter owns it."""
    blocks = [
        "Answer using only the retrieved Groww chunks.",
        "Do not add a Source URL or a last-updated line.",
        "",
        f"Question:\n{(query or '').strip()}",
        "",
        "Retrieved chunks:",
    ]
    for index, chunk in enumerate(chunks, start=1):
        text = str(chunk.get("text") or "").strip()
        blocks.append(
            f"[{index}] scheme_id={chunk.get('scheme_id') or ''} "
            f"source_title={chunk.get('source_title') or ''} "
            f"as_of_date={chunk.get('as_of_date') or ''}\n{text}"
        )
    return "\n\n".join(blocks)


def extractive_fallback(query: str, chunks: list[dict]) -> str:
    """Copy the first supporting sentence from the top chunk. No paraphrase."""
    if not chunks:
        return OUT_OF_SCOPE_REFUSAL
    text = str(chunks[0].get("text") or "").strip()
    units = split_sentences(text)
    if not units:
        return OUT_OF_SCOPE_REFUSAL
    lowered_query = (query or "").lower()
    hints: list[str] = []
    for needle, keys in _SUPPORT_HINTS:
        if needle in lowered_query:
            hints.extend(keys)
    if hints:
        for unit in units:
            lowered = unit.lower()
            if any(hint in lowered for hint in hints):
                return cap_sentences(unit, 1)
    for unit in units:
        if len(unit) >= 12:
            return cap_sentences(unit, 1)
    return cap_sentences(units[0], 1)


def _gemini_client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiError("GEMINI_API_KEY is missing")
    from google import genai

    return genai.Client(api_key=api_key)


def call_gemini(query: str, chunks: list[dict], *, client: Any | None = None) -> str:
    """Grounded write only. No Google Search and no URL-context tools."""
    from google.genai import types

    writer = _gemini_client(client)
    config_kwargs: dict[str, Any] = {
        "system_instruction": llm_system_prompt(),
        "temperature": 0.1,
        "max_output_tokens": 256,
    }
    afc = getattr(types, "AutomaticFunctionCallingConfig", None)
    if afc is not None:
        config_kwargs["automatic_function_calling"] = afc(disable=True)
    config = types.GenerateContentConfig(**config_kwargs)
    response = writer.models.generate_content(
        model=MODEL_ID,
        contents=build_user_payload(query, chunks),
        config=config,
    )
    return (getattr(response, "text", None) or "").strip()


def generate_answer(
    query: str,
    chunks: list[dict] | None = None,
    *,
    client: Any | None = None,
    force_extractive: bool = False,
) -> str:
    """All guards run here before any Gemini call. Returns body text only."""
    blocked = policy_block_for_gemini(query)
    if blocked is not None:
        return blocked
    hits = [chunk for chunk in (chunks or []) if isinstance(chunk, dict)]
    if not hits:
        return OUT_OF_SCOPE_REFUSAL
    if force_extractive:
        return screen_model_output(extractive_fallback(query, hits))
    try:
        text = call_gemini(query, hits, client=client)
        if not text:
            text = extractive_fallback(query, hits)
    except Exception:
        text = extractive_fallback(query, hits)
    return screen_model_output(text)
