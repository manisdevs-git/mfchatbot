"""Refusal and factsheet-redirect copy. Does not log the raw user query."""

from __future__ import annotations

from src.guard import GuardDecision
from src.schemes import AS_OF_DATE, EDUCATION_URL, SCHEME_TITLES, scheme_url

PII_REFUSAL = (
    "I cannot use personal identifiers. Ask a factual question without PAN, "
    "Aadhaar, phone, email, OTP, or account numbers."
)

ADVISORY_REFUSAL = (
    "I can only answer factual questions from Groww scheme and help pages, "
    "and I cannot recommend or compare funds. For investor education, see: "
    f"{EDUCATION_URL}"
)

OUT_OF_SCOPE_REFUSAL = (
    "That is not available on the current Groww pages in this assistant. "
    "I cover five HDFC Direct Growth schemes on Groww plus Groww help on "
    "statements, expense ratio, exit load, and the riskometer."
)

INCOMPLETE_SCHEME = (
    "Please name one in-scope scheme (Large Cap, Mid Cap, Small Cap, Gold FoF, "
    "or ELSS) and one topic such as expense ratio, exit load, SIP, or NAV."
)

INCOMPLETE_TOPIC = (
    "Please ask one factual topic for that scheme: expense ratio, exit load, "
    "minimum SIP, lock-in, riskometer, benchmark, or NAV."
)

INCOMPLETE_EMPTY = "Please type a factual question about an in-scope Groww scheme or help page."

INCOMPLETE_MULTI = (
    "Please ask about one scheme at a time. I cannot merge or compare two funds."
)


def _performance_body(decision: GuardDecision) -> str:
    url = scheme_url(decision.scheme_id)
    if url:
        title = SCHEME_TITLES.get(decision.scheme_id or "", "this scheme")
        return (
            f"I cannot calculate or quote returns. See the official Groww page "
            f"for {title}.\n\n"
            f"Source: {url}\n\n"
            f"Last updated from sources: {AS_OF_DATE}"
        )
    return (
        "I cannot calculate or quote returns. Name one in-scope scheme "
        "and open its Groww page for official figures."
    )


def format_refusal(decision: GuardDecision) -> str:
    """User-visible text for a blocked intent. Never echoes identifiers."""
    if decision.intent == "pii":
        return PII_REFUSAL
    if decision.intent == "advisory":
        return ADVISORY_REFUSAL
    if decision.intent == "performance":
        return _performance_body(decision)
    if decision.intent == "out_of_scope":
        return OUT_OF_SCOPE_REFUSAL
    if decision.intent == "incomplete":
        if decision.reason == "empty":
            return INCOMPLETE_EMPTY
        if decision.reason == "multiple_schemes":
            return INCOMPLETE_MULTI
        if decision.reason == "topic_required":
            return INCOMPLETE_TOPIC
        return INCOMPLETE_SCHEME
    raise ValueError(f"no refusal template for intent={decision.intent!r}")
