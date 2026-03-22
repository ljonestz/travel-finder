"""
Restaurant search, filter, distance-sort, and AI analysis.

Search flow:
  1. geocode(location)  → center (lat, lng)
  2. search_places("[location] restaurant [preferences]") → up to 20 raw results
  3. Extract lat/lng from geometry.location; compute haversine distance from center
  4. Filter: rating >= 4.5, reviews >= 50 (relax to 4.0 if < 3 qualify)
  5. Sort by distance (ascending)
  6. Top 15 → get_place_details for each
  7. Algorithmic GF classify (fallback)
  8. claude_analyzer.analyze_restaurants() → AI descriptions + GF assessment
  9. Return top 10 enriched results
"""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

from .claude_analyzer import analyze_restaurants
from .gf import classify, scan_reviews
from .maps import geocode, get_maps_url, get_place_details, haversine, search_places
from .web_search import search_gf_mentions

_MIN_RATING = 4.5
_MIN_REVIEWS = 50
_RELAXED_RATING = 4.0

# Suffixes stripped for blog-name matching (must match web_search._STRIP_SUFFIXES)
_NAME_SUFFIXES = [
    " restaurant", " restaurants", " café", " cafe",
    " bistro", " brasserie", " bar", " grill",
]


def _normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, strip common restaurant suffixes."""
    s = name.lower().strip(" ,;.")
    s = re.sub(r"[,;]\s*", " ", s).strip()
    for suffix in _NAME_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s.strip(" ,;.")


def _tag_blog_match(place: dict[str, Any], blog_names: set[str]) -> None:
    """Set place['blog_match'] = True if normalised name is in blog_names."""
    place["blog_match"] = _normalise_name(place.get("name", "")) in blog_names


def _rank_key(place: dict[str, Any], preferences: str) -> tuple:
    """
    Sort key for GF-first ranking (ascending - lower = better rank).

    primary   = gf_tier (1 best, 3 worst)
    secondary = -(rating * 0.6 + 1/(dist_km + 0.5) * 0.4)  [negate: higher = better]
    tertiary  = -pref_match_count  [negate: more matches = better]
    """
    tier = place.get("gf_tier", 3)
    rating = float(place.get("rating") or 0)
    dist = float(place.get("distance_km") or 9999)
    blend = rating * 0.6 + (1.0 / (dist + 0.5)) * 0.4

    prefs_lower = preferences.lower()
    combined = (place.get("name", "") + " " + " ".join(place.get("types", []))).lower()
    pref_hits = sum(1 for word in prefs_lower.split() if word and word in combined)

    return (tier, -round(blend, 4), -pref_hits)


def _extract_lat_lng(raw: dict[str, Any], fallback_lat: float, fallback_lng: float) -> tuple[float, float]:
    """Pull lat/lng from a raw search result's geometry field."""
    loc = raw.get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        return fallback_lat, fallback_lng
    return float(lat), float(lng)


def search_restaurants(
    location: str,
    preferences: str = "",
) -> dict[str, Any]:
    """
    Search, filter, rank, and AI-analyse restaurants for a location.

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

    # 2. Web search for GF blog mentions (cached, degrades gracefully)
    blog_names = search_gf_mentions(location)

    # 3. Google Places text search - lead with "gluten free" for better GF results
    query = f"gluten free restaurant {location}"
    if preferences:
        query += f" {preferences}"
    raw_results = search_places(query)

    # 4. Attach distance
    for r in raw_results:
        lat, lng = _extract_lat_lng(r, center_lat, center_lng)
        r["_lat"] = lat
        r["_lng"] = lng
        r["_dist"] = haversine(center_lat, center_lng, lat, lng)

    # 5. Filter by rating and reviews
    filtered = [
        r for r in raw_results
        if (r.get("rating") or 0) >= _MIN_RATING
        and (r.get("user_ratings_total") or 0) >= _MIN_REVIEWS
    ]
    relaxed = False
    if len(filtered) < 3:
        filtered = [r for r in raw_results if (r.get("rating") or 0) >= _RELAXED_RATING]
        relaxed = True

    # 6. Fetch place details for top 15 (pre-sort by distance for consistent input)
    filtered.sort(key=lambda r: r.get("_dist", 9999.0))
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

        types = details.get("types") or raw.get("types", [])
        website = details.get("website", "")
        reviews = details.get("reviews") or []

        # Algorithmic GF (fallback if Claude fails)
        gf = classify(place_id=place_id, website=website, types=types)

        place: dict[str, Any] = {
            "place_id": place_id,
            "name": details.get("name") or raw.get("name", ""),
            "rating": details.get("rating") or raw.get("rating"),
            "reviews": details.get("user_ratings_total") or raw.get("user_ratings_total", 0),
            "address": details.get("formatted_address", ""),
            "website": website,
            "maps_url": details.get("url") or get_maps_url(place_id),
            "types": types,
            "lat": lat,
            "lng": lng,
            "distance_km": dist_km,
            "gf_tier": gf.tier,
            "gf_label": gf.label,
            "gf_dishes": gf.dishes,
            "gf_notes": "",
            "gf_sources": [],
            "description": "",
            "review_gf_count": scan_reviews(reviews),
        }
        _tag_blog_match(place, blog_names)
        enriched.append(place)

    # 7. Claude AI analysis for top 10 (pre-sort by distance for consistent input)
    top10 = enriched[:10]
    ai_results = analyze_restaurants(top10)
    for i, ai in enumerate(ai_results):
        if i < len(top10):
            top10[i]["description"]  = ai.get("description", "")
            top10[i]["gf_tier"]      = ai.get("gf_tier",  top10[i]["gf_tier"])
            top10[i]["gf_label"]     = ai.get("gf_label", top10[i]["gf_label"])
            top10[i]["gf_dishes"]    = ai.get("gf_dishes", top10[i]["gf_dishes"])
            top10[i]["gf_notes"]     = ai.get("gf_notes", "")
            top10[i]["gf_sources"]   = ai.get("gf_sources", [])

    # 8. GF-first ranking
    top10.sort(key=lambda p: _rank_key(p, preferences))

    return {
        "results": top10,
        "shortlist": top10[:5],
        "center_lat": center_lat,
        "center_lng": center_lng,
        "relaxed": relaxed,
        "query": query,
    }
