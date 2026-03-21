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

_GF_KEYWORDS = re.compile(
    r"gluten[- ]?free|sans[- ]gluten|celiac|coeliac|\bGF\b|gluten[- ]friendly",
    re.IGNORECASE,
)

from .claude_analyzer import analyze_restaurants
from .gf import classify
from .maps import geocode, get_maps_url, get_place_details, haversine, search_places

_MIN_RATING = 4.5
_MIN_REVIEWS = 50
_RELAXED_RATING = 4.0

# GF tier weights for shortlist scoring (GF quality >> rating >> proximity)
_GF_SCORE = {1: 40.0, 2: 20.0, 3: 0.0}


def _normalize_name(name: str) -> str:
    """Strip location/branch suffixes for deduplication (e.g. 'White Rabbit - Brunch & Co' → 'white rabbit')."""
    name = name.lower()
    for sep in (" - ", " – ", " | ", " / "):
        if sep in name:
            name = name.split(sep)[0]
    return name.strip()


def _dedup_shortlist(candidates: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    """Return top-n unique restaurants (by normalised name, keeping highest-scoring)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in candidates:
        key = _normalize_name(r.get("name", ""))
        if key not in seen:
            seen.add(key)
            out.append(r)
        if len(out) == n:
            break
    return out


def _shortlist_score(r: dict[str, Any]) -> float:
    """Combined score prioritising GF tier, then rating, then proximity."""
    gf   = _GF_SCORE.get(r.get("gf_tier", 3), 0.0)
    rate = (r.get("rating") or 4.0) * 4.0   # 4.5 → 18, 5.0 → 20
    dist = -r.get("distance_km", 5.0) * 0.4  # mild distance penalty
    return gf + rate + dist


def _extract_lat_lng(raw: dict[str, Any], fallback_lat: float, fallback_lng: float) -> tuple[float, float]:
    """Pull lat/lng from a raw search result's geometry field."""
    loc = raw.get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        return fallback_lat, fallback_lng
    return float(lat), float(lng)


def _merge_searches(*result_lists: list[dict]) -> list[dict]:
    """Merge multiple search result lists, deduplicating by place_id. Earlier lists take priority."""
    seen: set[str] = set()
    merged: list[dict] = []
    for results in result_lists:
        for r in results:
            pid = r.get("place_id", "")
            if pid and pid not in seen:
                seen.add(pid)
                merged.append(r)
    return merged


def search_restaurants(
    location: str,
    preferences: str = "",
) -> dict[str, Any]:
    """
    Search, filter, rank, and AI-analyse restaurants for a location.

    Runs two searches — GF-specific first, then general — so gluten-free
    friendly places surface even when they wouldn't rank in a generic search.
    Deduplicates by chain name at enrichment time to avoid duplicate branches.

    Returns:
        {
          "results":     [top 10, distance-sorted, AI-enriched, chain-deduped],
          "shortlist":   [top 5 by GF+rating+distance],
          "center_lat":  float,
          "center_lng":  float,
          "relaxed":     bool,
          "query":       str,
        }
    """
    # 1. Geocode the location to a center point
    center_lat, center_lng = geocode(location)

    # 2. Two searches: GF-focused first so GF places surface prominently,
    #    then general (with any extra preferences) for breadth.
    gf_query = f"{location} gluten free restaurant"
    general_query = f"{location} restaurant"
    if preferences:
        general_query += f" {preferences}"

    gf_raw      = search_places(gf_query)
    general_raw = search_places(general_query)

    # Track which place_ids came from the GF search (used in sort)
    gf_place_ids = {r.get("place_id") for r in gf_raw}

    # Merge, GF results first, dedup by place_id
    raw_results = _merge_searches(gf_raw, general_raw)

    # 3. Attach distance + GF-search flag to every result
    for r in raw_results:
        lat, lng = _extract_lat_lng(r, center_lat, center_lng)
        r["_lat"] = lat
        r["_lng"] = lng
        r["_dist"] = haversine(center_lat, center_lng, lat, lng)
        r["_from_gf_search"] = r.get("place_id") in gf_place_ids

    # 4. Filter by rating and reviews
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

    # 5. Sort: GF-search results first, then by distance within each group
    filtered.sort(key=lambda r: (0 if r.get("_from_gf_search") else 1, r.get("_dist", 9999.0)))

    # 6. Fetch place details — skip same chain (dedup by normalised name here
    #    to avoid wasting API calls on duplicate branches of the same restaurant)
    enriched: list[dict[str, Any]] = []
    seen_chains: set[str] = set()

    for raw in filtered[:25]:  # inspect up to 25 candidates to fill 15 unique chains
        place_id = raw.get("place_id", "")
        raw_name = raw.get("name", "")
        chain_key = _normalize_name(raw_name)

        if chain_key in seen_chains:
            continue  # skip duplicate branch
        seen_chains.add(chain_key)

        try:
            details = get_place_details(place_id)
        except Exception as e:
            _log.warning("get_place_details failed for %s: %s", place_id, e)
            details = {}

        # Prefer details geometry over raw geometry
        geo = details.get("geometry", {}).get("location", {})
        lat = float(geo["lat"]) if geo.get("lat") is not None else raw["_lat"]
        lng = float(geo["lng"]) if geo.get("lng") is not None else raw["_lng"]
        dist_km = round(haversine(center_lat, center_lng, lat, lng), 1)

        types = details.get("types") or raw.get("types", [])
        website = details.get("website", "")

        # Extract review snippets — GF-mentioning reviews prioritised for Claude
        raw_reviews = details.get("reviews") or []
        review_texts = [rv.get("text", "") for rv in raw_reviews if rv.get("text")]
        gf_review_snippets = [t[:300] for t in review_texts if _GF_KEYWORDS.search(t)]
        all_snippets = gf_review_snippets or [t[:200] for t in review_texts[:3]]

        editorial = (details.get("editorial_summary") or {}).get("overview", "")

        # 7. Algorithmic GF (fallback if Claude fails)
        gf = classify(place_id=place_id, website=website, types=types)

        enriched.append({
            "place_id": place_id,
            "name": details.get("name") or raw_name,
            "rating": details.get("rating") or raw.get("rating"),
            "reviews": details.get("user_ratings_total") or raw.get("user_ratings_total", 0),
            "address": details.get("formatted_address", ""),
            "website": website,
            "maps_url": details.get("url") or get_maps_url(place_id),
            "types": types,
            "lat": lat,
            "lng": lng,
            "distance_km": dist_km,
            "editorial_summary": editorial,
            "review_snippets": all_snippets,
            "from_gf_search": raw.get("_from_gf_search", False),
            # Algorithmic GF (overwritten by Claude analysis below)
            "gf_tier": gf.tier,
            "gf_label": gf.label,
            "gf_dishes": gf.dishes,
            "gf_notes": "",
            "description": "",
        })

        if len(enriched) == 15:
            break

    # 8. Claude AI analysis for top 10 (by current sort order)
    to_analyze = enriched[:10]
    ai_results = analyze_restaurants(to_analyze)
    for i, ai in enumerate(ai_results):
        if i < len(to_analyze):
            to_analyze[i]["description"] = ai.get("description", "")
            to_analyze[i]["gf_tier"]     = ai.get("gf_tier",  to_analyze[i]["gf_tier"])
            to_analyze[i]["gf_label"]    = ai.get("gf_label", to_analyze[i]["gf_label"])
            to_analyze[i]["gf_dishes"]   = ai.get("gf_dishes", to_analyze[i]["gf_dishes"])
            to_analyze[i]["gf_notes"]    = ai.get("gf_notes", "")

    # Results table: sort by distance (chains already deduped above)
    results = sorted(to_analyze, key=lambda r: r.get("distance_km", 9999.0))

    # Shortlist: re-rank top 5 by GF quality + rating + proximity
    shortlist = sorted(results, key=_shortlist_score, reverse=True)[:5]

    return {
        "results": results,
        "shortlist": shortlist,
        "center_lat": center_lat,
        "center_lng": center_lng,
        "relaxed": relaxed,
        "query": gf_query,
    }
