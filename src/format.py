"""Citation, last-updated footer, and three-sentence cap.

The model must not pick or invent the URL. This module owns the Source line
and the footer date from the winning chunk's manifest metadata.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from src.schemes import AS_OF_DATE, CATALOG_SCHEME_IDS, SCHEME_TITLES

MAX_SENTENCES = 3
SOURCE_LABEL = "Source:"
FOOTER_LABEL = "Last updated from sources:"

_DECIMAL_DOT = "\u2024"
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)", re.I)
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
_SOURCE_LINE_RE = re.compile(r"^\s*Source:\s+\S+.*$", re.I | re.M)
_FOOTER_LINE_RE = re.compile(r"^\s*Last updated from sources:\s+.*$", re.I | re.M)
_BULLET_RE = re.compile(r"^[\-\u2022*]\s+")


def split_sentences(text: str) -> list[str]:
    """Split on sentence ends and newlines without breaking decimals such as 1.03%."""
    raw = (text or "").strip()
    if not raw:
        return []
    protected = re.sub(r"(?<=\d)\.(?=\d)", _DECIMAL_DOT, raw)
    parts = re.split(r"(?<=[.!?])\s+|\n+", protected)
    sentences: list[str] = []
    for part in parts:
        cleaned = _BULLET_RE.sub("", part.replace(_DECIMAL_DOT, ".").strip())
        if cleaned:
            sentences.append(cleaned)
    return sentences


def cap_sentences(text: str, limit: int = MAX_SENTENCES) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return ""
    return " ".join(sentences[: max(1, int(limit))])


def strip_model_links(text: str) -> str:
    """Remove URLs and Source/footer lines the model is not allowed to emit."""
    body = (text or "").strip()
    if not body:
        return ""
    body = _SOURCE_LINE_RE.sub("", body)
    body = _FOOTER_LINE_RE.sub("", body)
    body = _MD_LINK_RE.sub(r"\1", body)
    body = _URL_RE.sub("", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip(" \t\n:-")


def is_groww_url(url: object) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "groww.in" or host.endswith(".groww.in")


def winning_citation(chunk: dict | None) -> tuple[str | None, str | None]:
    """Return (source_url, as_of_date) from the highest-ranked supporting chunk."""
    if not chunk:
        return None, None
    url = chunk.get("source_url")
    if not is_groww_url(url):
        return None, None
    date = str(chunk.get("as_of_date") or "").strip() or AS_OF_DATE
    return str(url).strip(), date


TOPIC_LABELS = {
    "exit_load": "Exit load",
    "expense_ratio": "Expense ratio",
    "sip": "Minimum SIP",
    "lock_in": "Lock-in",
    "riskometer": "Riskometer",
    "benchmark": "Benchmark",
    "nav": "NAV",
}

TOPIC_CELL_HINTS = {
    "exit_load": ("exit load", "exit-load"),
    "expense_ratio": ("expense ratio", "expense", "ter", "total expense"),
    "sip": ("sip", "minimum sip", "min. for sip", "min sip"),
    "lock_in": ("lock-in", "lock in", "lockin"),
    "riskometer": ("riskometer",),
    "benchmark": ("benchmark",),
    "nav": ("nav", "latest nav", "net asset value"),
}

CATALOG_MISSING = "Not available on the current Groww page."


def _cell_from_chunk(topic: str | None, chunk: dict) -> str:
    """Copy the first supporting sentence for this topic. No paraphrase."""
    units = split_sentences(str(chunk.get("text") or ""))
    if not units:
        return CATALOG_MISSING
    hints = TOPIC_CELL_HINTS.get(topic or "", ())
    if hints:
        for unit in units:
            lowered = unit.lower()
            if any(hint in lowered for hint in hints):
                return cap_sentences(unit, 1)
    return CATALOG_MISSING


def format_catalog(chunks: list[dict], topic: str | None) -> str:
    """One row per in-scope scheme. Catalog answers are not capped at three sentences."""
    by_scheme = {
        str(chunk.get("scheme_id")): chunk
        for chunk in chunks
        if chunk.get("scheme_id") in CATALOG_SCHEME_IDS and is_groww_url(chunk.get("source_url"))
    }
    label = TOPIC_LABELS.get(topic or "", "Fact")
    lines = [
        f"| Scheme | {label} | Source |",
        "| --- | --- | --- |",
    ]
    dates: list[str] = []
    for scheme_id in CATALOG_SCHEME_IDS:
        chunk = by_scheme.get(scheme_id)
        title = SCHEME_TITLES[scheme_id]
        if not chunk:
            lines.append(f"| {title} | {CATALOG_MISSING} | — |")
            continue
        fact = _cell_from_chunk(topic, chunk).replace("|", "/")
        url, date = winning_citation(chunk)
        if date:
            dates.append(date)
        source = url or "—"
        lines.append(f"| {title} | {fact} | {source} |")
    footer_date = dates[0] if dates else AS_OF_DATE
    lines.append("")
    lines.append(f"{FOOTER_LABEL} {footer_date}")
    return "\n".join(lines)


def format_response(body: str, chunk: dict | None = None) -> str:
    """Cap the body, drop extra links, and append exactly one Groww citation."""
    cleaned = cap_sentences(strip_model_links(body))
    url, date = winning_citation(chunk)
    if not url:
        return cleaned
    parts = [cleaned] if cleaned else []
    parts.append(f"{SOURCE_LABEL} {url}")
    parts.append(f"{FOOTER_LABEL} {date}")
    return "\n\n".join(parts)
