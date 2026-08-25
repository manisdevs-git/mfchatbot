"""Poll Railway until /v1/ask returns the stamped corpus date."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API = "https://mfchatbot-production-fd5b.up.railway.app"
QUERY = "What is the expense ratio of HDFC Large Cap Fund Direct Growth?"


def expected_as_of() -> str:
    manifest = json.loads((ROOT / "corpus_manifest.json").read_text(encoding="utf-8"))
    return str(manifest["documents"][0]["as_of_date"])


def ask(base: str) -> dict:
    body = json.dumps({"query": QUERY}).encode()
    request = urllib.request.Request(
        f"{base.rstrip('/')}/v1/ask",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    base = args[0] if args else DEFAULT_API
    wanted = expected_as_of()
    print(f"waiting for {base} as_of_date={wanted}")
    for attempt in range(1, 37):
        try:
            payload = ask(base)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            print(f"attempt {attempt}/36: {exc}")
            time.sleep(15)
            continue
        got = payload.get("as_of_date")
        text = str(payload.get("text") or "")[:180]
        print(f"attempt {attempt}/36: as_of_date={got} {text}")
        if got == wanted:
            print("Railway is serving the refreshed corpus.")
            return 0
        time.sleep(15)
    print(f"Railway did not pick up as_of_date={wanted} in time.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
