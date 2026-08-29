"""Retrieve routing only. No refusals.

This module labels the question for retrieve (scheme_id / topic).
Advisory / compare / ranking is not detected here. Gemini applies that
rule from the system prompt in src/generate.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

from src.schemes import resolve_scheme_ids

MAX_QUERY_CHARS = 2000

INTENTS = (
    "advisory",
    "performance",
    "out_of_scope",
    "process",
    "factual",
    "catalog",
    "incomplete",
)

# PAN: AAAAA9999A. Do not use a raw query that matched this in logs.
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.I)
AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
OTP_CONTEXT_RE = re.compile(r"\botp\b", re.I)
OTP_DIGITS_RE = re.compile(r"\b\d{4,8}\b")
FOLIO_RE = re.compile(r"\b(?:folio|account|ac(?:c)?(?:ount)?(?:\s*no\.?)?)\b", re.I)
LONG_DIGITS_RE = re.compile(r"\b\d{11,18}\b")

PERFORMANCE_RES = (
    re.compile(r"\b(?:\d+\s*[- ]\s*year|1 year|3 year|5 year|since inception)\s+returns?\b", re.I),
    re.compile(r"\b(?:cagr|xirr|outperform(?:ed)?)\b", re.I),
    re.compile(r"\b(?:historic |past |annual )?returns?\b", re.I),
    re.compile(r"\bif i invested\b", re.I),
    re.compile(r"\bwould (?:i|it) have\b", re.I),
    re.compile(r"\bbeat the benchmark\b", re.I),
    re.compile(r"\bwhat would i have\b", re.I),
)

PROCESS_RES = (
    re.compile(r"\b(?:download|how (?:do|can) i (?:get|download))\b", re.I),
    re.compile(r"\bcapital gains?\b", re.I),
    re.compile(r"\b\bcas\b", re.I),
    re.compile(r"\b(?:elss )?(?:tax )?statement\b", re.I),
    re.compile(r"\bwhat is cas\b", re.I),
)

FACT_TOPIC_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("expense_ratio", re.compile(r"\b(?:expense ratios?|ter|total expense)\b", re.I)),
    ("exit_load", re.compile(r"\bexit loads?\b", re.I)),
    ("sip", re.compile(r"\b(?:min(?:imum)? )?sips?\b", re.I)),
    ("lock_in", re.compile(r"\block[ -]?ins?\b", re.I)),
    ("riskometer", re.compile(r"\briskometers?\b", re.I)),
    ("benchmark", re.compile(r"\bbenchmarks?\b", re.I)),
    ("nav", re.compile(r"\bnavs?\b", re.I)),
    ("lumpsum", re.compile(r"\blump[ -]?sums?\b", re.I)),
    ("aum", re.compile(r"\b(?:aum|assets? under management)\b", re.I)),
    ("units", re.compile(r"\b(?:mutual fund units?|fund units?)\b", re.I)),
    ("sebi", re.compile(r"\bsebi\b", re.I)),
    ("listing", re.compile(
        r"\b(?:mutual fund categor(?:y|ies)|fund categor(?:y|ies)|"
        r"groww help(?: home)?|help hub|hdfc (?:mutual funds? )?amc)\b",
        re.I,
    )),
)

ALL_SCHEMES_RE = re.compile(
    r"\b(?:all|every|each)\s+(?:five\s+)?(?:the\s+)?(?:in[ -]scope\s+)?(?:schemes?|funds?)\b"
    r"|\bacross\s+(?:all\s+)?(?:schemes?|funds?)\b",
    re.I,
)

DEFINITION_RE = re.compile(
    r"\bwhat (?:is|are) (?:an? )?(?:"
    r"expense ratio|exit load|riskometer|ter|"
    r"nav|n\.?a\.?v\.?|"
    r"sip|systematic investment plan|"
    r"benchmark|"
    r"lump[ -]?sum|"
    r"aum|assets? under management|"
    r"mutual fund units?|"
    r"sebi|securities and exchange board(?: of india)?|"
    r"mutual fund categor(?:y|ies)|fund categor(?:y|ies)"
    r")\b",
    re.I,
)

OUT_OF_SCOPE_RES = (
    re.compile(
        r"\b(?:sbi|icici|axis|nippon|kotak|tata|dsp|mirae|uti|parag parikh|ppfas)\b",
        re.I,
    ),
    re.compile(r"\b(?:bluechip|flexi cap|balanced advantage|index fund)\b", re.I),
    re.compile(r"\b(?:moneycontrol|value research|morningstar)\b", re.I),
    re.compile(r"\btax planning\b", re.I),
    re.compile(r"\bportfolio construction\b", re.I),
)


@dataclass(frozen=True)
class GuardDecision:
    """Retrieve hints. Does not refuse. Gemini-side policy lives in generate.py."""

    intent: str
    scheme_id: str | None
    topic: str | None
    allow_retrieve: bool
    allow_gemini: bool
    reason: str


def truncate_query(text: str) -> str:
    if len(text) <= MAX_QUERY_CHARS:
        return text
    return text[:MAX_QUERY_CHARS]


def contains_pii(text: str) -> bool:
    """True when the string looks like PAN / Aadhaar / phone / email / OTP / folio."""
    if PAN_RE.search(text):
        return True
    if EMAIL_RE.search(text):
        return True
    if AADHAAR_RE.search(text):
        return True
    if PHONE_RE.search(text):
        return True
    if OTP_CONTEXT_RE.search(text) and OTP_DIGITS_RE.search(text):
        return True
    if FOLIO_RE.search(text) and (LONG_DIGITS_RE.search(text) or re.search(r"\b\d{8,}\b", text)):
        return True
    if LONG_DIGITS_RE.search(text):
        return True
    return False


def _has_performance(text: str) -> bool:
    return any(pattern.search(text) for pattern in PERFORMANCE_RES)


def _has_process(text: str) -> bool:
    return any(pattern.search(text) for pattern in PROCESS_RES)


def _fact_topic(text: str) -> str | None:
    for topic, pattern in FACT_TOPIC_RES:
        if pattern.search(text):
            return topic
    return None


def _out_of_scope(text: str) -> bool:
    return any(pattern.search(text) for pattern in OUT_OF_SCOPE_RES)


def wants_catalog(text: str) -> bool:
    """True when the user asks for a topic across every in-scope scheme."""
    return bool(ALL_SCHEMES_RE.search(text or ""))


def _routed(
    intent: str,
    scheme_id: str | None,
    topic: str | None,
    reason: str,
    *,
    empty: bool = False,
) -> GuardDecision:
    """Empty input cannot be retrieved. Every other label is allowed through."""
    allow = not empty
    return GuardDecision(
        intent=intent,
        scheme_id=scheme_id,
        topic=topic,
        allow_retrieve=allow,
        allow_gemini=allow,
        reason=reason,
    )


def classify(query: str) -> GuardDecision:
    """Label the question for retrieve. Does not refuse and does not call Gemini."""
    raw = query if isinstance(query, str) else ""
    text = truncate_query(raw).strip()
    if not text or not re.search(r"[A-Za-z0-9]", text):
        return _routed("incomplete", None, None, "empty", empty=True)

    schemes = resolve_scheme_ids(text)
    scheme_id = schemes[0] if len(schemes) == 1 else None
    topic = _fact_topic(text)

    if _has_performance(text):
        return _routed("performance", scheme_id, topic, "performance")
    if _out_of_scope(text):
        return _routed("out_of_scope", None, topic, "out_of_scope")
    if wants_catalog(text) and topic:
        return _routed("catalog", None, topic, "catalog")
    if wants_catalog(text) and not topic:
        return _routed("incomplete", None, None, "topic_required")
    if len(schemes) >= 2:
        return _routed("incomplete", None, topic, "multiple_schemes")
    if _has_process(text):
        return _routed("process", "generic", topic or "statements", "process")
    if DEFINITION_RE.search(text) and not scheme_id:
        return _routed("process", "generic", topic or "education", "process")
    if topic == "listing" and not scheme_id:
        return _routed("process", "generic", "listing", "process")
    if topic and scheme_id:
        return _routed("factual", scheme_id, topic, "factual")
    if topic and not scheme_id:
        return _routed("incomplete", None, topic, "scheme_required")
    if scheme_id and not topic:
        return _routed("incomplete", scheme_id, None, "topic_required")
    return _routed("out_of_scope", None, None, "unknown")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Label a question for retrieve. Policy is in generate.py."
    )
    parser.add_argument("query", help="User question")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    decision = classify(args.query)
    print(f"intent={decision.intent}")
    print(f"scheme_id={decision.scheme_id}")
    print(f"topic={decision.topic}")
    print(f"allow_retrieve={decision.allow_retrieve}")
    print(f"allow_gemini={decision.allow_gemini}")
    print(f"reason={decision.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
