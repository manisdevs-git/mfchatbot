"""Phase 0 smoke: call gemini-3.5-flash-lite. Sends no corpus and no PII."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print(
            "FAIL: GEMINI_API_KEY is missing. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        return 1

    from google import genai

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents="Reply with the single word OK.",
    )
    text = (response.text or "").strip()
    print(text)
    if "OK" not in text.upper():
        print("FAIL: unexpected model reply", file=sys.stderr)
        return 1
    print("OK gemini-3.5-flash-lite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
