"""
Google Maps Places API client.

Uses urllib + ssl._create_unverified_context so it works on
WBG-managed machines and Render without certificate issues.
"""

from __future__ import annotations

import json
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
    url (Google Maps link), types, price_level, formatted_address.
    """
    fields = ",".join([
        "name", "website", "rating", "user_ratings_total",
        "url", "types", "price_level", "formatted_address",
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
