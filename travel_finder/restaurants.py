"""
Restaurant search, filter, and ranking.

Search flow:
  1. search_places("[location] restaurant") → up to 20 results
  2. Filter: rating >= 4.5, user_ratings_total >= 50 (soft — flag, don't exclude)
  3. If < 3 results after filter, relax to rating >= 4.0 and flag
  4. Rank: rating (primary) → review count (secondary)
  5. get_place_details for each → GF classify
"""

from __future__ import annotations

from typing import Any

from .gf import GFResult, classify
from .maps import get_maps_url, get_place_details, search_places

_MIN_RATING = 4.5
_MIN_REVIEWS = 50
_RELAXED_RATING = 4.0


def _make_result(raw: dict[str, Any], details: dict[str, Any], gf: GFResult) -> dict[str, Any]:
    """Merge raw search result, place details, and GF classification into a flat dict."""
    return {
        "place_id": raw.get("place_id", ""),
        "name": details.get("name") or raw.get("name", ""),
        "rating": details.get("rating") or raw.get("rating"),
        "reviews": details.get("user_ratings_total") or raw.get("user_ratings_total", 0),
        "address": details.get("formatted_address", ""),
        "website": details.get("website", ""),
        "maps_url": details.get("url") or get_maps_url(raw.get("place_id", "")),
        "types": details.get("types") or raw.get("types", []),
        "gf_tier": gf.tier,
        "gf_label": gf.label,
        "gf_dishes": gf.dishes,
    }


def search_restaurants(
    location: str,
    preferences: str = "",
) -> dict[str, Any]:
    """
    Search, filter, rank, and classify restaurants for a location.

    Returns:
        {
          "results": [list of result dicts, ranked],
          "relaxed": bool,   # True if filters were relaxed
          "shortlist": [top 3-5 results],
          "query": str,
        }
    """
    query = f"{location} restaurant"
    if preferences:
        query += f" {preferences}"

    raw_results = search_places(query)

    # Filter
    filtered = [
        r for r in raw_results
        if (r.get("rating") or 0) >= _MIN_RATING
        and (r.get("user_ratings_total") or 0) >= _MIN_REVIEWS
    ]

    relaxed = False
    if len(filtered) < 3:
        filtered = [
            r for r in raw_results
            if (r.get("rating") or 0) >= _RELAXED_RATING
        ]
        relaxed = True

    # Sort: rating desc, then review count desc
    filtered.sort(
        key=lambda r: (-(r.get("rating") or 0), -(r.get("user_ratings_total") or 0))
    )

    # Enrich with details + GF classification
    enriched: list[dict[str, Any]] = []
    for raw in filtered[:20]:
        place_id = raw.get("place_id", "")
        try:
            details = get_place_details(place_id)
        except Exception:
            details = {}
        gf = classify(
            place_id=place_id,
            website=details.get("website", ""),
            types=details.get("types") or raw.get("types", []),
        )
        enriched.append(_make_result(raw, details, gf))

    return {
        "results": enriched,
        "relaxed": relaxed,
        "shortlist": enriched[:5],
        "query": query,
    }
