"""
Gluten-free classification — three tiers.

Tier 1 CONFIRMED  — website HTML contains gluten-related text
Tier 2 LIKELY     — inferred from cuisine types (safe heuristics)
                    Always labelled as "Likely (inferred - not labelled GF)"
Tier 3 UNCLEAR    — no website + no safe-cuisine match
"""

from __future__ import annotations

import ssl
import urllib.request
from typing import NamedTuple

_SSL_CTX = ssl._create_unverified_context()

# Cuisine keywords → best inferred GF dishes
_SAFE_CUISINES: dict[str, list[str]] = {
    "lebanese": ["grilled meats", "mezze", "hummus", "kibbeh"],
    "middle_eastern": ["grilled meats", "mezze", "hummus"],
    "japanese": ["sashimi", "grilled fish", "rice dishes"],
    "mexican": ["grilled meats", "tacos (corn)", "rice and beans"],
    "steakhouse": ["cote de boeuf", "grilled steak", "grilled fish"],
    "grill": ["grilled meats", "grilled fish"],
    "seafood": ["grilled fish", "steamed shellfish", "sashimi"],
    "swiss": ["perch fillets", "sole grillee", "rosti (check)"],
    "french": ["sole grillee", "grilled fish", "steak"],
    "thai": ["rice dishes", "grilled meats", "som tam (check sauce)"],
    "indian": ["dal", "rice dishes", "grilled tandoori meats"],
    "ethiopian": ["injera-free options", "tibs", "grilled meats"],
    "peruvian": ["ceviche", "grilled meats", "rice dishes"],
    "korean": ["grilled meats (bulgogi)", "bibimbap (check)"],
}

_GF_KEYWORDS = [
    "gluten", "gluten-free", "gluten free", "coeliac", "celiac", "without gluten",
    "sans gluten", "glutenvrij", "senza glutine",
]


class GFResult(NamedTuple):
    tier: int          # 1, 2, or 3
    label: str         # display label
    dishes: list[str]  # best GF dish suggestions (empty for tier 3)


def _fetch_html(url: str, timeout: int = 6) -> str:
    """Fetch website HTML; return empty string on any error."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            raw = resp.read(40_000)  # first 40 KB
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _check_website(website: str) -> bool:
    """Return True if GF keywords found in website HTML."""
    html = _fetch_html(website).lower()
    return any(kw in html for kw in _GF_KEYWORDS)


def _infer_from_cuisine(types: list[str]) -> list[str] | None:
    """
    Match Google Maps types against safe-cuisine heuristics.
    Returns dish list if matched, None otherwise.
    """
    types_lower = [t.lower() for t in types]
    for keyword, dishes in _SAFE_CUISINES.items():
        if any(keyword in t for t in types_lower):
            return dishes
    return None


def classify(
    place_id: str,
    website: str,
    types: list[str],
) -> GFResult:
    """
    Classify a restaurant for gluten-free options.

    Args:
        place_id: Google Maps place ID (unused currently, reserved for future)
        website:  Restaurant website URL (may be empty)
        types:    Google Maps place types list

    Returns:
        GFResult with tier, label, and dish suggestions
    """
    # Tier 1: explicit GF mention on website
    if website and _check_website(website):
        return GFResult(
            tier=1,
            label="GF Confirmed",
            dishes=[],
        )

    # Tier 2: infer from cuisine
    dishes = _infer_from_cuisine(types)
    if dishes:
        return GFResult(
            tier=2,
            label="Likely (inferred - not labelled GF)",
            dishes=dishes,
        )

    # Tier 3: unclear
    return GFResult(
        tier=3,
        label="GF Unclear",
        dishes=[],
    )
