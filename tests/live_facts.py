"""Live Groww corpus checks: pin fact shape, not today's numbers.

Fixture tests may still use canned values such as 1.03%. Tests that read
data/raw or data/processed after a real fetch must not.
"""

from __future__ import annotations

import re
from unittest import TestCase

EXPENSE_RATIO_LINE = re.compile(r"Expense ratio:\s+\d+(?:\.\d+)?%", re.I)
MIN_SIP = re.compile(r"(?:Minimum SIP:\s+₹|Min\. for SIP\s*₹?)[\d,]+", re.I)
EXIT_LOAD = re.compile(r"exit load", re.I)
NAV_AMOUNT = re.compile(r"NAV:.*₹[\d,]+(?:\.\d+)?", re.I | re.DOTALL)
ADVICE_CHROME = ("Would've become", "Compare similar funds")


def assert_live_scheme_facts(test: TestCase, text: str) -> None:
    """Require labeled scheme facts. Values may change on the next Groww fetch."""
    test.assertRegex(text, EXPENSE_RATIO_LINE)
    test.assertRegex(text, MIN_SIP)
    test.assertRegex(text, EXIT_LOAD)
    test.assertRegex(text, NAV_AMOUNT)
    for chrome in ADVICE_CHROME:
        test.assertNotIn(chrome, text)
