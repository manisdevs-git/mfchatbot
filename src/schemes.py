"""scheme_id aliases and Groww page URLs. Used by retrieve routing and search."""

from __future__ import annotations

SCHEME_IDS = (
    "hdfc-mid-cap-fund-direct-growth",
    "hdfc-small-cap-fund-direct-growth",
    "hdfc-gold-etf-fund-of-fund-direct-plan-growth",
    "hdfc-large-cap-fund-direct-growth",
    "hdfc-elss-tax-saver-fund-direct-plan-growth",
)

# Display order for all-scheme catalog tables.
CATALOG_SCHEME_IDS = (
    "hdfc-large-cap-fund-direct-growth",
    "hdfc-mid-cap-fund-direct-growth",
    "hdfc-small-cap-fund-direct-growth",
    "hdfc-gold-etf-fund-of-fund-direct-plan-growth",
    "hdfc-elss-tax-saver-fund-direct-plan-growth",
)

SCHEME_URLS = {
    "hdfc-mid-cap-fund-direct-growth": "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
    "hdfc-small-cap-fund-direct-growth": "https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth",
    "hdfc-gold-etf-fund-of-fund-direct-plan-growth": (
        "https://groww.in/mutual-funds/hdfc-gold-etf-fund-of-fund-direct-plan-growth"
    ),
    "hdfc-large-cap-fund-direct-growth": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    "hdfc-elss-tax-saver-fund-direct-plan-growth": (
        "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth"
    ),
}

SCHEME_TITLES = {
    "hdfc-mid-cap-fund-direct-growth": "HDFC Mid Cap Fund Direct Growth",
    "hdfc-small-cap-fund-direct-growth": "HDFC Small Cap Fund Direct Growth",
    "hdfc-gold-etf-fund-of-fund-direct-plan-growth": "HDFC Gold ETF Fund of Fund Direct Plan Growth",
    "hdfc-large-cap-fund-direct-growth": "HDFC Large Cap Fund Direct Growth",
    "hdfc-elss-tax-saver-fund-direct-plan-growth": "HDFC ELSS Tax Saver Fund Direct Plan Growth",
}

AS_OF_DATE = "2026-08-21"

# Longer aliases first so "gold etf" wins over a later short token.
ALIASES: tuple[tuple[str, str], ...] = (
    ("hdfc large cap fund direct growth", "hdfc-large-cap-fund-direct-growth"),
    ("hdfc mid cap fund direct growth", "hdfc-mid-cap-fund-direct-growth"),
    ("hdfc small cap fund direct growth", "hdfc-small-cap-fund-direct-growth"),
    ("hdfc gold etf fund of fund direct plan growth", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
    ("hdfc elss tax saver fund direct plan growth", "hdfc-elss-tax-saver-fund-direct-plan-growth"),
    ("hdfc large cap", "hdfc-large-cap-fund-direct-growth"),
    ("hdfc mid cap", "hdfc-mid-cap-fund-direct-growth"),
    ("hdfc small cap", "hdfc-small-cap-fund-direct-growth"),
    ("hdfc gold etf", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
    ("hdfc elss", "hdfc-elss-tax-saver-fund-direct-plan-growth"),
    ("gold etf fof", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
    ("gold etf", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
    ("gold fof", "hdfc-gold-etf-fund-of-fund-direct-plan-growth"),
    ("tax saver", "hdfc-elss-tax-saver-fund-direct-plan-growth"),
    ("large cap", "hdfc-large-cap-fund-direct-growth"),
    ("mid cap", "hdfc-mid-cap-fund-direct-growth"),
    ("midcap", "hdfc-mid-cap-fund-direct-growth"),
    ("small cap", "hdfc-small-cap-fund-direct-growth"),
    ("smallcap", "hdfc-small-cap-fund-direct-growth"),
    ("elss", "hdfc-elss-tax-saver-fund-direct-plan-growth"),
)

PRIMER_EXPENSE_RATIO = "https://groww.in/p/expense-ratio"
PRIMER_EXIT_LOAD = "https://groww.in/p/exit-load-in-mutual-funds"
PRIMER_RISKOMETER = "https://groww.in/p/riskometer"
AMFI_INVESTOR_URL = "https://www.amfiindia.com/investor"
AMFI_RISKS_URL = (
    "https://www.amfiindia.com/investor-corner/knowledge-center/risks-in-mutual-funds.html"
)
EDUCATION_URL = AMFI_RISKS_URL


def fold(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").replace("—", " ").split())


def resolve_scheme_ids(query: str) -> list[str]:
    """Return distinct in-scope scheme_ids mentioned in the query, longest alias first."""
    folded = fold(query)
    found: list[str] = []
    seen: set[str] = set()
    for alias, scheme_id in ALIASES:
        if alias in folded and scheme_id not in seen:
            seen.add(scheme_id)
            found.append(scheme_id)
    return found


def scheme_url(scheme_id: str | None) -> str | None:
    if not scheme_id:
        return None
    return SCHEME_URLS.get(scheme_id)
