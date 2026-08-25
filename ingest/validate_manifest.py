"""Validate corpus_manifest.json: Groww hosts only for this project."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "corpus_manifest.json"

REQUIRED_FIELDS = (
    "doc_id",
    "scheme_id",
    "doc_type",
    "source_url",
    "source_title",
    "as_of_date",
    "topic_tags",
)
ALLOWED_DOC_TYPES = frozenset({"groww_scheme", "groww_help"})
REQUIRED_SCHEME_IDS = (
    "hdfc-mid-cap-fund-direct-growth",
    "hdfc-small-cap-fund-direct-growth",
    "hdfc-gold-etf-fund-of-fund-direct-plan-growth",
    "hdfc-large-cap-fund-direct-growth",
    "hdfc-elss-tax-saver-fund-direct-plan-growth",
)
ALLOWED_SCHEME_IDS = frozenset(REQUIRED_SCHEME_IDS) | {"generic"}

ALLOWED_HOST_SUFFIXES = ("groww.in",)

BLOCKED_HOST_SUBSTRINGS = (
    "hdfcfund.com",
    "valueresearchonline.com",
    "moneycontrol.com",
    "morningstar",
    "etmoney.com",
    "kuvera.in",
    "paytmmoney.com",
    "blogspot.",
    "medium.com",
    "wordpress.",
    "substack.com",
)


class ManifestError(ValueError):
    """Corpus manifest failed validation."""


def load_manifest(path: Path | None = None) -> dict:
    manifest_path = path or DEFAULT_MANIFEST
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def is_blocked_host(host: str) -> bool:
    return any(blocked in host for blocked in BLOCKED_HOST_SUBSTRINGS)


def is_allowed_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def validate_url(url: str, *, field: str = "source_url") -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ManifestError(f"{field} is not an absolute http(s) URL: {url!r}")
    host = _host(url)
    if is_blocked_host(host):
        raise ManifestError(f"{field} uses a blocked non-Groww host: {url}")
    if not is_allowed_host(host):
        raise ManifestError(f"{field} host {host!r} is not groww.in: {url}")


def _validate_date(value: str, *, doc_id: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestError(f"{doc_id}: as_of_date must be YYYY-MM-DD: {value!r}") from exc


def validate_manifest(manifest: dict) -> list[str]:
    """Return a short summary list if valid; raise ManifestError otherwise."""
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ManifestError("manifest.documents must be a non-empty list")

    seen_ids: set[str] = set()
    scheme_docs: dict[str, set[str]] = {sid: set() for sid in REQUIRED_SCHEME_IDS}
    has_process = False
    has_education = False

    for index, doc in enumerate(documents):
        if not isinstance(doc, dict):
            raise ManifestError(f"documents[{index}] must be an object")
        missing = [key for key in REQUIRED_FIELDS if key not in doc]
        if missing:
            raise ManifestError(f"documents[{index}] missing fields: {missing}")

        doc_id = doc["doc_id"]
        if not isinstance(doc_id, str) or not doc_id:
            raise ManifestError(f"documents[{index}] has an empty doc_id")
        if doc_id in seen_ids:
            raise ManifestError(f"duplicate doc_id: {doc_id}")
        seen_ids.add(doc_id)

        scheme_id = doc["scheme_id"]
        if scheme_id not in ALLOWED_SCHEME_IDS:
            raise ManifestError(f"{doc_id}: unknown scheme_id {scheme_id!r}")

        doc_type = doc["doc_type"]
        if doc_type not in ALLOWED_DOC_TYPES:
            raise ManifestError(f"{doc_id}: invalid doc_type {doc_type!r}")

        if not isinstance(doc["source_title"], str) or not doc["source_title"].strip():
            raise ManifestError(f"{doc_id}: source_title is required")

        tags = doc["topic_tags"]
        if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) and tag for tag in tags):
            raise ManifestError(f"{doc_id}: topic_tags must be a non-empty list of strings")

        _validate_date(doc["as_of_date"], doc_id=doc_id)
        validate_url(doc["source_url"])

        if scheme_id in scheme_docs:
            scheme_docs[scheme_id].add(doc_type)
        if scheme_id == "generic" and "statements" in tags:
            has_process = True
        if scheme_id == "generic" and "education" in tags:
            has_education = True

        if doc_type == "groww_help" and scheme_id != "generic":
            raise ManifestError(f"{doc_id}: groww_help documents must use scheme_id generic")
        if doc_type == "groww_scheme" and scheme_id == "generic":
            raise ManifestError(f"{doc_id}: groww_scheme documents cannot use scheme_id generic")

    missing_schemes = [
        scheme_id
        for scheme_id, types in scheme_docs.items()
        if "groww_scheme" not in types
    ]
    if missing_schemes:
        raise ManifestError(
            "each in-scope scheme needs a Groww scheme page; incomplete: "
            + ", ".join(missing_schemes)
        )
    if not has_process:
        raise ManifestError("need at least one Groww help document tagged statements")
    if not has_education:
        raise ManifestError("need at least one Groww primer tagged education")

    return [
        f"documents={len(documents)}",
        f"schemes={len(REQUIRED_SCHEME_IDS)}",
        "process=yes",
        "education=yes",
        "hosts=groww-only",
    ]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]) if args else DEFAULT_MANIFEST
    try:
        summary = validate_manifest(load_manifest(path))
    except (OSError, json.JSONDecodeError, ManifestError) as exc:
        print(f"FAIL {path}: {exc}", file=sys.stderr)
        return 1
    print(f"OK {path}: " + ", ".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
