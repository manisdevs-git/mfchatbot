"""Download only allowlisted Groww URLs from the manifest into data/raw/."""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.validate_manifest import (
    DEFAULT_MANIFEST,
    ManifestError,
    load_manifest,
    validate_manifest,
    validate_url,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT / "data" / "raw"

SAFE_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
TIMEOUT_S = 30
MAX_RETRIES = 3
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
DEFAULT_DELAY_S = 0.75

Downloader = Callable[[str], tuple[str, bytes]]


class FetchError(ValueError):
    """A Groww page could not be downloaded or saved."""


@dataclass(frozen=True)
class FetchResult:
    doc_id: str
    source_url: str
    final_url: str
    path: Path
    bytes_written: int


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class _GrowwOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect that leaves groww.in."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, field="redirect_url")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    https = urllib.request.HTTPSHandler(context=_ssl_context())
    return urllib.request.build_opener(https, _GrowwOnlyRedirectHandler())


def raw_path_for(doc_id: str, raw_dir: Path | None = None) -> Path:
    if not SAFE_DOC_ID.match(doc_id):
        raise FetchError(f"unsafe doc_id for a filename: {doc_id!r}")
    return (raw_dir or DEFAULT_RAW_DIR) / f"{doc_id}.html"


def download_url(url: str) -> tuple[str, bytes]:
    """GET a Groww URL. Returns (final_url, body). Host checks run before and after."""
    validate_url(url)
    request = urllib.request.Request(url, headers=REQUEST_HEADERS, method="GET")
    opener = _opener()
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with opener.open(request, timeout=TIMEOUT_S) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise FetchError(f"{url} returned HTTP {status}")
                final_url = response.geturl() or url
                validate_url(final_url, field="final_url")
                body = response.read()
                if not body:
                    raise FetchError(f"{url} returned an empty body")
                return final_url, body
        except ManifestError:
            raise
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                raise FetchError(f"{url} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                raise FetchError(f"{url} download failed: {exc}") from exc
        time.sleep(0.5 * attempt)

    raise FetchError(f"{url} download failed: {last_error}")


def fetch_one(
    doc: dict,
    raw_dir: Path | None = None,
    *,
    download: Downloader = download_url,
) -> FetchResult:
    """Download one manifest document to data/raw/<doc_id>.html."""
    doc_id = doc["doc_id"]
    source_url = doc["source_url"]
    validate_url(source_url)
    dest = raw_path_for(doc_id, raw_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)

    final_url, body = download(source_url)
    validate_url(final_url, field="final_url")
    if not body:
        raise FetchError(f"{doc_id}: empty response from {source_url}")

    dest.write_bytes(body)
    return FetchResult(
        doc_id=doc_id,
        source_url=source_url,
        final_url=final_url,
        path=dest,
        bytes_written=len(body),
    )


def fetch_corpus(
    manifest_path: Path | None = None,
    raw_dir: Path | None = None,
    *,
    download: Downloader = download_url,
    delay_s: float = DEFAULT_DELAY_S,
) -> list[FetchResult]:
    """Validate the manifest, then download every Groww document."""
    path = manifest_path or DEFAULT_MANIFEST
    manifest = load_manifest(path)
    validate_manifest(manifest)

    out_dir = raw_dir or DEFAULT_RAW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[FetchResult] = []
    documents = manifest["documents"]
    for index, doc in enumerate(documents):
        results.append(fetch_one(doc, out_dir, download=download))
        if delay_s > 0 and index < len(documents) - 1:
            time.sleep(delay_s)
    return results


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Groww manifest URLs into data/raw/ (Phase 2A). No cleaning."
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to corpus_manifest.json",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help="Directory for raw HTML snapshots",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        results = fetch_corpus(args.manifest, args.raw_dir)
    except (OSError, json.JSONDecodeError, ManifestError, FetchError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    hosts = {urlparse(item.final_url).hostname or "" for item in results}
    print(f"OK fetched {len(results)} Groww document(s) into {_rel(args.raw_dir)}")
    for item in results:
        print(
            f"  {item.doc_id}: {item.source_url} -> {_rel(item.path)} "
            f"({item.bytes_written} bytes)"
        )
    print("hosts=" + ",".join(sorted(hosts)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
