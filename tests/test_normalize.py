"""Phase 2B: raw Groww HTML becomes plain text plus a manifest sidecar."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ingest.normalize import (
    NormalizeError,
    html_to_text,
    normalize_corpus,
    normalize_one,
    sidecar_for,
)
from ingest.validate_manifest import ManifestError

ROOT = Path(__file__).resolve().parents[1]
LARGE_CAP_RAW = ROOT / "data" / "raw" / "groww-large-cap-direct-growth.html"


SCHEME_HTML = """<!doctype html>
<html>
  <body>
    <nav>Stocks Invest in Stocks</nav>
    <header>Groww header</header>
    <div class="returnCalculator_box__aaa">
      <h3>Return calculator</h3>
      <p>Would've become ₹11,77,693</p>
    </div>
    <div class="compareSimilarFunds_table__bbb">
      <h3>Compare similar funds</h3>
      <p>Invesco India Largecap Fund Direct Growth</p>
    </div>
    <div class="fundDetails_row__ccc">NAV: 21 Aug '26 ₹1,245.13 Min. for SIP ₹100 Expense ratio 1.03%</div>
    <div class="minInvestments_box__ddd">
      <h3>Minimum investments</h3>
      <p>Min. for SIP ₹100</p>
    </div>
    <div class="exitLoadStampDutyTax_box__eee">Exit load of 1% if redeemed within 1 year</div>
    <footer class="footerTopSection_grid__fff">Download the App</footer>
    <script id="__NEXT_DATA__">__NEXT_JSON__</script>
  </body>
</html>
"""

SCHEME_NEXT = {
    "props": {
        "pageProps": {
            "mfServerSideData": {
                "scheme_name": "HDFC Large Cap Fund Direct Growth",
                "expense_ratio": "1.03",
                "min_sip_investment": 100,
                "min_investment_amount": 100,
                "exit_load": "Exit load of 1% if redeemed within 1 year",
                "benchmark_name": "NIFTY 100 Total Return Index",
                "lock_in": {"years": 0, "months": 0, "days": 0},
                "return_stats": [{"risk": "Very High"}],
            }
        }
    }
}

PRIMER_HTML = """<!doctype html>
<html>
  <body>
    <div class="dropdownUI_menu__aaa">Invest in Stocks Invest in stocks, ETFs</div>
    <div class="SeoSidebarV2Links_box__bbb">Related calculators</div>
    <h1>Expense Ratio leftover chrome</h1>
    <footer class="footer_otherLinks__ccc">Download the App</footer>
    <script id="__NEXT_DATA__">__NEXT_JSON__</script>
  </body>
</html>
"""

PRIMER_NEXT = {
    "props": {
        "pageProps": {
            "glossaryData": {
                "title": "Expense Ratio",
                "content": "<p>Expense ratio is the annual maintenance charge levied by mutual funds.</p>",
                "faqs": [
                    {
                        "question": "How To Calculate Expense Ratio In Mutual Fund?",
                        "answer": "<p>Total expenses are divided by the total assets of the funds.</p>",
                    }
                ],
            }
        }
    }
}


def _scheme_doc(**overrides: object) -> dict:
    base = {
        "doc_id": "groww-large-cap-direct-growth",
        "scheme_id": "hdfc-large-cap-fund-direct-growth",
        "doc_type": "groww_scheme",
        "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "source_title": "Groww — HDFC Large Cap Fund Direct Growth",
        "as_of_date": "2026-08-21",
        "topic_tags": ["expense_ratio", "exit_load", "sip"],
    }
    base.update(overrides)
    return base


class HtmlToTextTests(unittest.TestCase):
    def test_keeps_scheme_facts_and_drops_advice_chrome(self) -> None:
        html = SCHEME_HTML.replace("__NEXT_JSON__", json.dumps(SCHEME_NEXT))
        text = html_to_text(html)
        self.assertIn("Expense ratio: 1.03%", text)
        self.assertIn("Minimum SIP: ₹100", text)
        self.assertIn("Exit load of 1% if redeemed within 1 year", text)
        self.assertNotIn("Would've become", text)
        self.assertNotIn("Compare similar funds", text)
        self.assertNotIn("Invesco India Largecap", text)
        self.assertNotIn("Download the App", text)
        self.assertNotIn("Invest in Stocks", text)

    def test_primer_uses_glossary_html_not_sidebar(self) -> None:
        html = PRIMER_HTML.replace("__NEXT_JSON__", json.dumps(PRIMER_NEXT))
        text = html_to_text(html)
        self.assertIn("Expense ratio is the annual maintenance charge", text)
        self.assertIn("How To Calculate Expense Ratio In Mutual Fund?", text)
        self.assertNotIn("Related calculators", text)
        self.assertNotIn("Invest in Stocks", text)


class NormalizeOneTests(unittest.TestCase):
    def test_writes_text_and_sidecar(self) -> None:
        html = SCHEME_HTML.replace("__NEXT_JSON__", json.dumps(SCHEME_NEXT))
        with tempfile.TemporaryDirectory() as raw_tmp, tempfile.TemporaryDirectory() as out_tmp:
            raw_dir = Path(raw_tmp)
            processed_dir = Path(out_tmp)
            (raw_dir / "groww-large-cap-direct-growth.html").write_text(html, encoding="utf-8")
            result = normalize_one(_scheme_doc(), raw_dir, processed_dir)
            text = result.text_path.read_text(encoding="utf-8")
            meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
            self.assertTrue(result.text_path.name.endswith(".txt"))
            self.assertTrue(result.meta_path.name.endswith(".meta.json"))
            self.assertIn("1.03%", text)
            self.assertEqual(meta["scheme_id"], "hdfc-large-cap-fund-direct-growth")
            self.assertEqual(
                meta["source_url"],
                "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            )
            self.assertEqual(meta["as_of_date"], "2026-08-21")
            self.assertEqual(meta["topic_tags"], ["expense_ratio", "exit_load", "sip"])

    def test_refuses_non_groww_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, tempfile.TemporaryDirectory() as out_tmp:
            with self.assertRaises(ManifestError):
                normalize_one(
                    _scheme_doc(source_url="https://www.hdfcfund.com/factsheet"),
                    Path(raw_tmp),
                    Path(out_tmp),
                )

    def test_missing_raw_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp, tempfile.TemporaryDirectory() as out_tmp:
            with self.assertRaises(NormalizeError):
                normalize_one(_scheme_doc(), Path(raw_tmp), Path(out_tmp))

    def test_sidecar_rejects_off_host(self) -> None:
        with self.assertRaises(ManifestError):
            sidecar_for(_scheme_doc(source_url="https://www.moneycontrol.com/fund"))


class LiveLargeCapTests(unittest.TestCase):
    @unittest.skipUnless(LARGE_CAP_RAW.is_file(), "Phase 2A Large Cap snapshot is missing")
    def test_large_cap_exit_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = normalize_corpus(
                ROOT / "corpus_manifest.json",
                ROOT / "data" / "raw",
                Path(tmp),
            )
            by_id = {item.doc_id: item for item in results}
            self.assertIn("groww-large-cap-direct-growth", by_id)
            text = by_id["groww-large-cap-direct-growth"].text_path.read_text(encoding="utf-8")
            self.assertIn("1.03", text)
            self.assertRegex(text, r"(Minimum SIP: ₹100|Min\. for SIP ₹100)")
            self.assertRegex(text.lower(), r"exit load of 1%")
            self.assertNotIn("Would've become", text)
            self.assertNotIn("Compare similar funds", text)
            meta = json.loads(by_id["groww-large-cap-direct-growth"].meta_path.read_text(encoding="utf-8"))
            self.assertIn("groww.in", meta["source_url"])


if __name__ == "__main__":
    unittest.main()
