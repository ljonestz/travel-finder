"""
Hotel search, filter, chain exclusion, distance-sort, and AI analysis.

Search flow:
  1. geocode(location)  → center (lat, lng)
  2. search_places("[location] boutique hotel [preferences]") → up to 20 raw results
  3. Exclude known chains
  4. Extract lat/lng; compute haversine distance from center
  5. Filter: rating >= 4.5, reviews >= 30 (relax to 4.0 if < 3 qualify)
  6. Sort by distance (ascending)
  7. Top 15 → get_place_details for each
  8. claude_analyzer.analyze_hotels() → AI descriptions
  9. Return top 10 enriched results
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

from .claude_analyzer import analyze_hotels
from .maps import geocode, get_maps_url, get_place_details, haversine, search_places

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
    name_lower = name.lower()
    return any(chain in name_lower for chain in _CHAIN_KEYWORDS)


def _style_tags(name: str, types: list[str]) -> list[str]:
    combined = name.lower() + " " + " ".join(t.lower() for t in types)
    return [tag for tag in _BOUTIQUE_TAGS if tag in combined]


def _extract_lat_lng(raw: dict[str, Any], fallback_lat: float, fallback_lng: float) -> tuple[float, float]:
    loc = raw.get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        return fallback_lat, fallback_lng
    return float(lat), float(lng)


def search_hotels(
    location: str,
    preferences: str = "",
) -> dict[str, Any]:
    """
    Search, filter, rank, and AI-analyse boutique hotels for a location.

    Returns:
        {
          "results":     [top 10, distance-sorted, AI-enriched],
          "shortlist":   [top 5],
          "center_lat":  float,
          "center_lng":  float,
          "relaxed":     bool,
          "query":       str,
        }
    """
    # 1. Geocode
    center_lat, center_lng = geocode(location)

    # 2. Text search
    query = f"{location} boutique hotel"
    if preferences:
        query += f" {preferences}"
    raw_results = search_places(query)

    # 3. Exclude chains
    non_chain = [r for r in raw_results if not _is_chain(r.get("name", ""))]

    # 4. Attach distance
    for r in non_chain:
        lat, lng = _extract_lat_lng(r, center_lat, center_lng)
        r["_lat"] = lat
        r["_lng"] = lng
        r["_dist"] = haversine(center_lat, center_lng, lat, lng)

    # 5. Filter
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

    # 6. Sort by distance (ascending)
    filtered.sort(key=lambda r: r.get("_dist", 9999.0))

    # 7. Enrich with details
    enriched: list[dict[str, Any]] = []
    for raw in filtered[:15]:
        place_id = raw.get("place_id", "")
        try:
            details = get_place_details(place_id)
        except Exception as e:
            _log.warning("get_place_details failed for %s: %s", place_id, e)
            details = {}

        geo = details.get("geometry", {}).get("location", {})
        lat = float(geo["lat"]) if geo.get("lat") is not None else raw["_lat"]
        lng = float(geo["lng"]) if geo.get("lng") is not None else raw["_lng"]
        dist_km = round(haversine(center_lat, center_lng, lat, lng), 1)

        name = details.get("name") or raw.get("name", "")
        types = details.get("types") or raw.get("types", [])

        enriched.append({
            "place_id": place_id,
            "name": name,
            "rating": details.get("rating") or raw.get("rating"),
            "reviews": details.get("user_ratings_total") or raw.get("user_ratings_total", 0),
            "address": details.get("formatted_address", ""),
            "website": details.get("website", ""),
            "maps_url": details.get("url") or get_maps_url(place_id),
            "types": types,
            "price_level": details.get("price_level") or raw.get("price_level"),
            "style_tags": _style_tags(name, types),
            "lat": lat,
            "lng": lng,
            "distance_km": dist_km,
            "description": "",
        })

    # 8. Claude AI analysis for top 10
    top10 = enriched[:10]
    ai_results = analyze_hotels(top10)
    for i, ai in enumerate(ai_results):
        if i < len(top10):
            top10[i]["description"] = ai.get("description", "")

    return {
        "results": top10,
        "shortlist": top10[:5],
        "center_lat": center_lat,
        "center_lng": center_lng,
        "relaxed": relaxed,
        "query": query,
    }
