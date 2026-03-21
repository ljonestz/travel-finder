"""
Google Maps Places API client.

Uses urllib + ssl._create_unverified_context so it works on
WBG-managed machines and Render without certificate issues.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import urllib.parse
import urllib.request
from typing import Any

_BASE = "https://maps.googleapis.com/maps/api"
_SSL_CTX = ssl._create_unverified_context()


def _api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY environment variable is not set")
    return key


def _get(url: str) -> dict[str, Any]:
    """HTTP GET + JSON decode."""
    with urllib.request.urlopen(url, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode())


def geocode(address: str) -> tuple[float, float]:
    """
    Geocode an address string to (lat, lng).
    Raises RuntimeError if no result is returned.
    """
    params = urllib.parse.urlencode({
        "address": address,
        "key": _api_key(),
    })
    url = f"{_BASE}/geocode/json?{params}"
    data = _get(url)
    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"Geocoding returned no results for: {address!r}")
    loc = results[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance between two points in km (Haversine formula)."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def search_places(query: str, radius: int = 5000) -> list[dict[str, Any]]:
    """
    Text search via Places API.
    Returns up to 20 result dicts (each has place_id, name, rating,
    user_ratings_total, geometry, types, price_level).
    """
    params = urllib.parse.urlencode({
        "query": query,
        "radius": radius,
        "key": _api_key(),
    })
    url = f"{_BASE}/place/textsearch/json?{params}"
    data = _get(url)
    return data.get("results", [])


def get_place_details(place_id: str) -> dict[str, Any]:
    """
    Fetch detailed fields for a single place.
    Returns dict with: name, website, rating, user_ratings_total,
    url (Google Maps link), types, price_level, formatted_address, geometry.
    """
    fields = ",".join([
        "name", "website", "rating", "user_ratings_total",
        "url", "types", "price_level", "formatted_address", "geometry",
    ])
    params = urllib.parse.urlencode({
        "place_id": place_id,
        "fields": fields,
        "key": _api_key(),
    })
    url = f"{_BASE}/place/details/json?{params}"
    data = _get(url)
    return data.get("result", {})


def get_maps_url(place_id: str) -> str:
    """Return a direct Google Maps URL for a place_id."""
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"
