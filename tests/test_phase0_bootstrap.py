"""Phase 0 bootstrap checks. Must not weaken Phase 1 files."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "app.py",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "corpus_manifest.json",
    "src/__init__.py",
    "src/pipeline.py",
    "src/guard.py",
    "src/retrieve.py",
    "src/generate.py",
    "src/format.py",
    "src/refuse.py",
    "src/schemes.py",
    "ingest/__init__.py",
    "ingest/validate_manifest.py",
    "ingest/fetch_official.py",
    "ingest/build_index.py",
    "ingest/refresh_corpus.py",
    "ingest/atomic.py",
    "scripts/smoke_gemini.py",
    "tests/test_validate_manifest.py",
    ".github/workflows/refresh-corpus.yml",
    "data/raw/.gitkeep",
    "data/processed/.gitkeep",
    "data/index/.gitkeep",
)

GITIGNORE_MUST_CONTAIN = (
    ".env",
    ".venv/",
    "__pycache__/",
    "data/raw/",
    "data/index/",
    "data/.ingest-staging/",
)


class Phase0BootstrapTests(unittest.TestCase):
    def test_layout_files_exist(self) -> None:
        missing = [rel for rel in REQUIRED_FILES if not (ROOT / rel).is_file()]
        self.assertEqual(missing, [], f"missing Phase 0/1 files: {missing}")

    def test_env_example_has_key_placeholder_only(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY=", text)
        self.assertNotRegex(text, r"GEMINI_API_KEY=\S+")
        self.assertIn("FRONTEND_ORIGINS=", text)

    def test_gitignore_covers_secrets_and_generated_data(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        missing = [token for token in GITIGNORE_MUST_CONTAIN if token not in text]
        self.assertEqual(missing, [], f".gitignore missing: {missing}")

    def test_dotenv_is_gitignored(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "-q", ".env"],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, ".env must be gitignored")

    def test_requirements_include_gemini_client(self) -> None:
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("google-genai", text)
        self.assertIn("python-dotenv", text)
        self.assertIn("fastapi", text)
        self.assertIn("uvicorn", text)

    def test_phase1_manifest_is_groww_only(self) -> None:
        manifest = json.loads((ROOT / "corpus_manifest.json").read_text(encoding="utf-8"))
        urls = [doc["source_url"] for doc in manifest["documents"]]
        self.assertTrue(urls)
        self.assertTrue(all("groww.in" in url for url in urls))
        self.assertFalse(any("hdfcfund.com" in url for url in urls))
        self.assertIn("hdfc-large-cap-fund-direct-growth", json.dumps(manifest))
