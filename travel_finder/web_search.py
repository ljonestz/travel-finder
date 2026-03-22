"""
Serper.dev client for GF blog/site discovery.

Queries Serper for "gluten free restaurants [city]" and
"site:findmeglutenfree.com [city]", extracts restaurant name
candidates from result titles/snippets, and caches results for
24h to avoid redundant API calls.

Degrades gracefully: returns empty set if SERPER_API_KEY is unset
or if the network call fails.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import ssl
import urllib.request
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_SSL_CTX = ssl._create_unverified_context()

# Cache directory — adjacent to this file; gitignored
_CACHE_DIR = str(Path(__file__).parent.parent / ".serper_cache")

_SERPER_URL = "https://google.serper.dev/search"

# Suffixes stripped when normalising restaurant names for matching
_STRIP_SUFFIXES = [
    " restaurant", " restaurants", " café", " cafe",
    " bistro", " brasserie", " bar", " grill",
]

# Regex: extract capitalised multi-word phrases likely to be proper names
_NAME_RE = re.compile(r"[A-ZÀ-Ý][a-zà-ÿA-ZÀ-Ý'\-]+(?: [A-ZÀ-Ý][a-zà-ÿA-ZÀ-Ý'\-]+){0,4}")


def _normalise(name: str) -> str:
    """Lowercase + strip common suffixes + strip trailing punctuation."""
    s = name.lower().strip(" ,;.")
    # Remove interior punctuation (commas, semicolons) that may precede a suffix
    s = re.sub(r"[,;]\s*", " ", s).strip()
    for suffix in _STRIP_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s.strip(" ,;.")


def _extract_names(results: list[dict[str, Any]]) -> set[str]:
    """Pull candidate restaurant names from Serper result objects.

    Adds both full multi-word phrase matches and each individual capitalised
    word within those phrases, so single-word names (e.g. 'Septime') are
    captured even when the regex grabs a longer compound like 'Paris Septime'.
    """
    names: set[str] = set()
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        for match in _NAME_RE.findall(text):
            # Add the full phrase
            normalised = _normalise(match)
            if len(normalised) > 3:
                names.add(normalised)
            # Also add each capitalised token individually
            for token in match.split():
                tok_norm = _normalise(token)
                if len(tok_norm) > 4:
                    names.add(tok_norm)
    return names


def _cache_path(city_key: str) -> Path:
    date_str = datetime.date.today().isoformat()
    return Path(_CACHE_DIR) / f"{city_key}_{date_str}.json"


def _read_cache(city_key: str) -> set[str] | None:
    p = _cache_path(city_key)
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception as e:
            _log.warning("web_search: cache read failed for %s: %s", p, e)
    return None


def _write_cache(city_key: str, names: set[str]) -> None:
    try:
        Path(_CACHE_DIR).mkdir(parents=True, exist_ok=True)
        _cache_path(city_key).write_text(json.dumps(list(names)))
    except Exception as e:
        _log.warning("web_search: cache write failed: %s", e)


def _call_serper(query: str, api_key: str) -> list[dict[str, Any]]:
    """POST to Serper and return organic results list."""
    payload = json.dumps({"q": query, "num": 10}).encode()
    req = urllib.request.Request(
        _SERPER_URL,
        data=payload,
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    return data.get("organic", [])


def search_gf_mentions(location: str) -> set[str]:
    """
    Return a set of normalised restaurant name strings mentioned in GF
    blog/site results for the given location.

    Returns an empty set if SERPER_API_KEY is not set or any error occurs.
    """
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return set()

    city_key = re.sub(r"[^a-z0-9]", "_", location.lower())[:40]

    cached = _read_cache(city_key)
    if cached is not None:
        _log.debug("web_search: cache hit for %s", city_key)
        return cached

    all_results: list[dict] = []
    queries = [
        f"gluten free restaurants {location}",
        f"site:findmeglutenfree.com {location}",
    ]
    for q in queries:
        try:
            all_results.extend(_call_serper(q, api_key))
        except Exception as e:
            _log.warning("web_search: Serper call failed for %r: %s", q, e)

    names = _extract_names(all_results)
    _write_cache(city_key, names)
    _log.info("web_search: found %d name candidates for %s", len(names), location)
    return names


def search_restaurant_menu(name: str, location: str) -> str:
    """
    Search Serper for a specific restaurant's menu and GF information.

    Returns combined title+snippet text from the top 5 results, or empty
    string if SERPER_API_KEY is not set or the call fails.
    """
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return ""
    query = f"{name} {location} gluten free menu"
    try:
        results = _call_serper(query, api_key)
        parts = []
        for r in results[:5]:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            if title or snippet:
                parts.append(f"{title}: {snippet}")
        return " | ".join(parts)
    except Exception as e:
        _log.warning("web_search: restaurant menu search failed for %r: %s", name, e)
        return ""
