"""
Hotel search, filter, chain exclusion, and ranking.

Search flow:
  1. search_places("[location] boutique hotel") → up to 20 results
  2. Exclude known chains
  3. Filter: rating >= 4.5, user_ratings_total >= 30 (soft)
  4. If < 3 results after filter, relax to rating >= 4.0 and flag
  5. Rank: rating (primary) → review count (secondary)
  6. Enrich with place details
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

from .maps import get_maps_url, get_place_details, search_places

_CHAIN_KEYWORDS = [
    "marriott", "hilton", "sheraton", "accor", "ibis", "novotel", "mercure",
    "ihg", "radisson", "best western", "hyatt", "wyndham", "moxy", "courtyard",
    "holiday inn", "crowne plaza", "doubletree", "hampton inn", "four points",
    "w hotel", "westin", "le meridien", "renaissance", "autograph",
    "sofitel", "pullman", "mgallery",
]

_BOUTIQUE_TAGS = ["boutique", "historic", "design", "independent", "maison", "manor", "chateau"]

_MIN_RATING = 4.5
_MIN_REVIEWS = 30
_RELAXED_RATING = 4.0


def _is_chain(name: str) -> bool:
    """Return True if the hotel name contains a known chain keyword."""
    name_lower = name.lower()
    return any(chain in name_lower for chain in _CHAIN_KEYWORDS)


def _style_tags(name: str, types: list[str]) -> list[str]:
    """Extract boutique/style indicators from name and types."""
    combined = name.lower() + " " + " ".join(t.lower() for t in types)
    return [tag for tag in _BOUTIQUE_TAGS if tag in combined]


def _make_result(raw: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    name = details.get("name") or raw.get("name", "")
    types = details.get("types") or raw.get("types", [])
    return {
        "place_id": raw.get("place_id", ""),
        "name": name,
        "rating": details.get("rating") or raw.get("rating"),
        "reviews": details.get("user_ratings_total") or raw.get("user_ratings_total", 0),
        "address": details.get("formatted_address", ""),
        "website": details.get("website", ""),
        "maps_url": details.get("url") or get_maps_url(raw.get("place_id", "")),
        "price_level": details.get("price_level") or raw.get("price_level"),
        "style_tags": _style_tags(name, types),
    }


def search_hotels(
    location: str,
    preferences: str = "",
) -> dict[str, Any]:
    """
    Search, filter, rank boutique hotels for a location.

    Returns:
        {
          "results": [list of result dicts, ranked],
          "relaxed": bool,
          "shortlist": [top 3-5 results],
          "query": str,
        }
    """
    query = f"{location} boutique hotel"
    if preferences:
        query += f" {preferences}"

    raw_results = search_places(query)

    # Exclude chains
    non_chain = [r for r in raw_results if not _is_chain(r.get("name", ""))]

    # Filter by rating and reviews
    filtered = [
        r for r in non_chain
        if (r.get("rating") or 0) >= _MIN_RATING
        and (r.get("user_ratings_total") or 0) >= _MIN_REVIEWS
    ]

    relaxed = False
    if len(filtered) < 3:
        filtered = [
            r for r in non_chain
            if (r.get("rating") or 0) >= _RELAXED_RATING
        ]
        relaxed = True

    # Sort: rating desc, then reviews desc
    filtered.sort(
        key=lambda r: (-(r.get("rating") or 0), -(r.get("user_ratings_total") or 0))
    )

    # Enrich with details
    enriched: list[dict[str, Any]] = []
    for raw in filtered[:20]:
        place_id = raw.get("place_id", "")
        try:
            details = get_place_details(place_id)
        except Exception as e:
            _log.warning("get_place_details failed for %s: %s", place_id, e)
            details = {}
        enriched.append(_make_result(raw, details))

    return {
        "results": enriched,
        "relaxed": relaxed,
        "shortlist": enriched[:5],
        "query": query,
    }
