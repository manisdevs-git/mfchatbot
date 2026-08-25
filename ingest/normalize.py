"""Turn raw Groww HTML into plain text. Phase 2B — no chunking, no vectors."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Tag

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.fetch_official import SAFE_DOC_ID, raw_path_for
from ingest.validate_manifest import (
    DEFAULT_MANIFEST,
    ManifestError,
    load_manifest,
    validate_manifest,
    validate_url,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT / "data" / "raw"
DEFAULT_PROCESSED_DIR = ROOT / "data" / "processed"

DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "form",
    "button",
    "nav",
    "header",
    "footer",
)

# CSS-module prefixes on Groww scheme / help / primer pages.
DROP_CLASS_PREFIXES = frozenset(
    {
        "compareSimilarFunds",
        "dropdownUI",
        "footer",
        "footerTopSection",
        "header",
        "header2025",
        "holdings",
        "lazyload-placeholder",
        "lazyload-wrapper",
        "letterLinks",
        "loader14Active",
        "loggedOut",
        "mfGraph",
        "mint-btn",
        "returnCalculator",
        "returnsAndRankings",
        "rodal",
        "SeoSidebarV2Links",
    }
)

KEEP_CLASS_PREFIXES = (
    "fundDetails",
    "minInvestments",
    "exitLoadStampDutyTax",
    "investmentObjective",
    "hnsLayout",
    "qap761",
    "answerWrapper",
)

DROP_HEADING_RE = re.compile(
    r"^(return calculator|compare similar funds|returns and rankings|holdings\b.*)$",
    re.I,
)

DROP_LINE_RE = re.compile(
    r"(?i)^("
    r"was the answer helpful\??|"
    r"download the app|"
    r"invest in stocks|"
    r"compare similar funds|"
    r"customer support|"
    r"en|हि"
    r")$"
)

DROP_SUBSTRINGS = (
    "would've become",
    "would’ve become",
    "compare similar funds",
    "historic returns",
)

SIDECAR_FIELDS = (
    "doc_id",
    "scheme_id",
    "doc_type",
    "source_url",
    "source_title",
    "as_of_date",
    "topic_tags",
)


class NormalizeError(ValueError):
    """A raw Groww snapshot could not be turned into trusted text."""


@dataclass(frozen=True)
class NormalizeResult:
    doc_id: str
    source_url: str
    text_path: Path
    meta_path: Path
    chars_written: int


def processed_paths(doc_id: str, processed_dir: Path | None = None) -> tuple[Path, Path]:
    if not SAFE_DOC_ID.match(doc_id):
        raise NormalizeError(f"unsafe doc_id for a filename: {doc_id!r}")
    folder = processed_dir or DEFAULT_PROCESSED_DIR
    return folder / f"{doc_id}.txt", folder / f"{doc_id}.meta.json"


def sidecar_for(doc: dict) -> dict:
    validate_url(doc["source_url"])
    return {field: doc[field] for field in SIDECAR_FIELDS}


def _class_prefixes(tag: Tag) -> set[str]:
    prefixes: set[str] = set()
    for cls in tag.get("class") or []:
        prefixes.add(cls)
        prefixes.add(cls.split("_")[0])
    return prefixes


def _has_prefix(tag: Tag, prefixes: tuple[str, ...] | frozenset[str]) -> bool:
    tokens = _class_prefixes(tag)
    return any(token in tokens for token in prefixes)


def parse_next_data(soup: BeautifulSoup) -> dict:
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return {}
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return {}
    page = payload.get("props", {}).get("pageProps")
    return page if isinstance(page, dict) else {}


def _html_fragment_to_text(html: str) -> str:
    if not html or not str(html).strip():
        return ""
    return _visible_text(BeautifulSoup(str(html), "html.parser"))


def _pct(value: object) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    return text if text.endswith("%") else f"{text}%"


def _inr(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        number = int(value) if float(value) == int(value) else value
        return f"₹{number}"
    text = str(value).strip()
    return text if text.startswith("₹") else f"₹{text}"


def _nav_amount(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, dict):
        return _nav_amount(value.get("value") or value.get("amount") or value.get("nav"))
    if isinstance(value, (int, float)):
        return f"₹{float(value):.2f}"
    text = str(value).strip()
    return text if text.startswith("₹") else f"₹{text}"


def _format_nav(data: dict) -> str:
    """Snapshot NAV from Groww JSON. Empty when the page has no NAV field."""
    amount = ""
    as_on = ""
    raw = data.get("nav")
    if isinstance(raw, dict):
        amount = _nav_amount(raw)
        as_on = str(raw.get("date") or raw.get("as_on") or raw.get("nav_date") or "").strip()
    elif raw not in (None, ""):
        amount = _nav_amount(raw)
    if not amount:
        for key in ("scheme_nav", "latest_nav", "current_nav", "nav_value"):
            if data.get(key) not in (None, ""):
                amount = _nav_amount(data[key])
                break
    if not as_on:
        for key in ("nav_date", "latest_nav_date", "nav_as_on", "as_on"):
            if data.get(key):
                as_on = str(data[key]).strip()
                break
    if not amount:
        return ""
    if as_on:
        return f"NAV: {amount} as of {as_on}"
    return f"NAV: {amount}"


def _format_lock_in(value: object) -> str:
    if not isinstance(value, dict):
        return str(value) if value else ""
    parts: list[str] = []
    mapping = (("years", "year"), ("months", "month"), ("days", "day"))
    for key, noun in mapping:
        amount = value.get(key) or 0
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            continue
        if amount:
            parts.append(f"{amount} {noun}{'s' if amount != 1 else ''}")
    return ", ".join(parts)


def extract_scheme_facts(page_props: dict) -> list[str]:
    data = page_props.get("mfServerSideData")
    if not isinstance(data, dict):
        return []

    lines: list[str] = []
    name = data.get("scheme_name")
    if name:
        lines.append(str(name))

    expense = _pct(data.get("expense_ratio"))
    if expense:
        lines.append(f"Expense ratio: {expense}")
    nav_line = _format_nav(data)
    if nav_line:
        lines.append(nav_line)

    rows = (
        ("Minimum SIP", _inr(data.get("min_sip_investment"))),
        ("Minimum lumpsum", _inr(data.get("min_investment_amount"))),
        ("Exit load", data.get("exit_load") or ""),
        ("Benchmark", data.get("benchmark_name") or data.get("benchmark") or ""),
        ("Lock-in", _format_lock_in(data.get("lock_in"))),
    )
    for label, value in rows:
        text = str(value).strip()
        if text:
            lines.append(f"{label}: {text}")

    risk = ""
    stats = data.get("return_stats")
    if isinstance(stats, list) and stats and isinstance(stats[0], dict):
        risk = str(stats[0].get("risk") or "").strip()
    if not risk:
        risk = str(data.get("nfo_risk") or "").strip()
    if risk:
        lines.append(f"Riskometer: {risk}")
    return lines


def extract_primer_article(page_props: dict) -> str:
    glossary = page_props.get("glossaryData")
    if not isinstance(glossary, dict):
        return ""
    parts: list[str] = []
    title = glossary.get("title")
    if title:
        parts.append(str(title).strip())
    content = _html_fragment_to_text(str(glossary.get("content") or ""))
    if content:
        parts.append(content)
    for faq in glossary.get("faqs") or []:
        if not isinstance(faq, dict):
            continue
        question = str(faq.get("question") or "").strip()
        answer = _html_fragment_to_text(str(faq.get("answer") or ""))
        if question:
            parts.append(f"Q: {question}")
        if answer:
            parts.append(answer)
    return "\n".join(part for part in parts if part)


def _help_title(page_props: dict) -> str:
    question_id = page_props.get("questionId")
    if not isinstance(question_id, str) or not question_id:
        return ""
    slug = re.sub(r"--\d+$", "", question_id)
    words = [word for word in slug.replace("_", "-").split("-") if word]
    if not words:
        return ""
    title = " ".join(words)
    title = title[:1].upper() + title[1:]
    return title.replace("elss", "ELSS")


def extract_keep_sections(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    seen: set[int] = set()
    for prefix in KEEP_CLASS_PREFIXES:
        for tag in soup.find_all(True):
            if not isinstance(tag, Tag) or not _has_prefix(tag, (prefix,)):
                continue
            identity = id(tag)
            if identity in seen:
                continue
            if any(id(parent) in seen for parent in tag.parents if isinstance(parent, Tag)):
                continue
            text = _visible_text(tag)
            if text:
                chunks.append(text)
                seen.add(identity)
    return "\n".join(chunks)


def _drop_chrome(soup: BeautifulSoup) -> None:
    for tag_name in DROP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in list(soup.find_all(True)):
        if not isinstance(tag, Tag) or tag.name in {"html", "body"}:
            continue
        if getattr(tag, "decomposed", False) or tag.attrs is None:
            continue
        if tag.get("role") in {"navigation", "banner", "contentinfo"}:
            tag.decompose()
            continue
        if _has_prefix(tag, DROP_CLASS_PREFIXES):
            tag.decompose()

    for heading in list(soup.find_all(re.compile(r"^h[1-6]$"))):
        if not isinstance(heading, Tag) or getattr(heading, "decomposed", False):
            continue
        title = " ".join(heading.get_text(" ", strip=True).split())
        if not DROP_HEADING_RE.match(title):
            continue
        parent = heading.parent
        if isinstance(parent, Tag) and parent.name not in {"body", "html"}:
            parent.decompose()
        else:
            heading.decompose()


def _visible_text(node: BeautifulSoup | Tag) -> str:
    return "\n".join(
        line.strip()
        for line in node.get_text("\n", strip=True).splitlines()
        if line.strip()
    )


def _drop_noise_lines(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or DROP_LINE_RE.match(stripped):
            continue
        low = stripped.lower()
        if any(token in low for token in DROP_SUBSTRINGS):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _collapse(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(html: str) -> str:
    """Extract factual visible text; drop Groww chrome, compare tables, and calculators."""
    soup = BeautifulSoup(html, "html.parser")
    page_props = parse_next_data(soup)

    parts: list[str] = []
    facts = extract_scheme_facts(page_props)
    if facts:
        parts.append("\n".join(facts))
    primer = extract_primer_article(page_props)
    if primer:
        parts.append(primer)
    help_title = _help_title(page_props)
    if help_title:
        parts.append(help_title)
    keep = extract_keep_sections(soup)
    if keep:
        parts.append(keep)

    _drop_chrome(soup)
    fallback = _drop_noise_lines(_visible_text(soup))
    body = _drop_noise_lines("\n\n".join(part for part in parts if part.strip()))
    if len(body) < 200 and fallback:
        body = f"{body}\n\n{fallback}".strip() if body else fallback
    return _collapse(body)


def normalize_one(
    doc: dict,
    raw_dir: Path | None = None,
    processed_dir: Path | None = None,
) -> NormalizeResult:
    """Write data/processed/<doc_id>.txt and a manifest sidecar."""
    doc_id = doc["doc_id"]
    source_url = doc["source_url"]
    validate_url(source_url)

    raw_file = raw_path_for(doc_id, raw_dir)
    if not raw_file.is_file():
        raise NormalizeError(f"{doc_id}: missing raw snapshot {raw_file}")

    text = html_to_text(raw_file.read_text(encoding="utf-8", errors="replace"))
    title = str(doc.get("source_title") or "").strip()
    if title and title.lower() not in text.lower():
        text = f"{title}\n\n{text}".strip() if text else title
    if not text.strip():
        raise NormalizeError(f"{doc_id}: no usable text after normalize")

    text_path, meta_path = processed_paths(doc_id, processed_dir)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text + "\n", encoding="utf-8")
    meta_path.write_text(
        json.dumps(sidecar_for(doc), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return NormalizeResult(
        doc_id=doc_id,
        source_url=source_url,
        text_path=text_path,
        meta_path=meta_path,
        chars_written=len(text),
    )


def normalize_corpus(
    manifest_path: Path | None = None,
    raw_dir: Path | None = None,
    processed_dir: Path | None = None,
) -> list[NormalizeResult]:
    """Validate the manifest, then normalize every raw Groww snapshot."""
    path = manifest_path or DEFAULT_MANIFEST
    manifest = load_manifest(path)
    validate_manifest(manifest)

    out_dir = processed_dir or DEFAULT_PROCESSED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    return [normalize_one(doc, raw_dir, out_dir) for doc in manifest["documents"]]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize Groww HTML snapshots into data/processed/ (Phase 2B)."
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to corpus_manifest.json",
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        results = normalize_corpus(args.manifest, args.raw_dir, args.processed_dir)
    except (OSError, json.JSONDecodeError, ManifestError, NormalizeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OK normalized {len(results)} Groww document(s) into {_rel(args.processed_dir)}")
    for item in results:
        print(
            f"  {item.doc_id}: {_rel(item.text_path)} + {_rel(item.meta_path)} "
            f"({item.chars_written} chars)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
