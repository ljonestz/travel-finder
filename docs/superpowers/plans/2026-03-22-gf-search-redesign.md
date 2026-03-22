# GF-First Restaurant Search Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the restaurant search pipeline so GF suitability ranks first, powered by Serper.dev blog search + Google review scanning + existing menu analysis, with source evidence chips on result cards.

**Architecture:** A new `web_search.py` module queries Serper.dev for GF blog mentions and caches results by city+date. `gf.py` gains a `scan_reviews()` helper. The `analyze_restaurants()` prompt is enriched with `blog_match` and `review_gf_count` per place, and returns a new `gf_sources` list. `restaurants.py` wires everything together and applies GF-first → blended-score → ambiance-match ranking. The loading spinner in `index.html` is replaced with the animated running-girl + GF cake chase.

**Tech Stack:** Python 3.11+, FastAPI, pytest (tests only), Serper.dev REST API, Anthropic SDK, Google Maps Places API, Jinja2 templates, Alpine.js, HTMX

**Branch:** `feat/v2-redesign` — all changes go here; merge to master when complete.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `requirements-dev.txt` | Create | pytest for local testing only |
| `tests/__init__.py` | Create | test package marker |
| `tests/test_web_search.py` | Create | unit tests for web_search module |
| `tests/test_gf.py` | Create | unit tests for scan_reviews() |
| `tests/test_restaurants.py` | Create | unit tests for ranking helpers |
| `travel_finder/web_search.py` | Create | Serper client, name extraction, 24h cache |
| `travel_finder/gf.py` | Modify | add scan_reviews() |
| `travel_finder/claude_analyzer.py` | Modify | add blog_match/review_gf_count to prompt; add gf_sources to output |
| `travel_finder/restaurants.py` | Modify | GF query, web_search call, review scan, new ranking |
| `web/templates/partials/restaurants.html` | Modify | add GF source chips to shortlist cards |
| `web/templates/index.html` | Modify | replace spinner with running-girl + cake animation |
| `.env.example` | Modify | add SERPER_API_KEY |
| `.gitignore` | Modify | add .serper_cache/ |

---

## Task 1: Test infrastructure

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements-dev.txt**

```
pytest>=8.0.0
```

- [ ] **Step 2: Create tests package**

```bash
mkdir -p "C:/Users/wb559324/OneDrive - WBG/Documents/GitHub/travel-finder/tests"
touch "C:/Users/wb559324/OneDrive - WBG/Documents/GitHub/travel-finder/tests/__init__.py"
```

- [ ] **Step 3: Verify pytest runs (empty suite)**

```bash
cd "C:/Users/wb559324/OneDrive - WBG/Documents/GitHub/travel-finder"
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: `no tests ran` (0 errors).

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt tests/__init__.py
git commit -m "chore: add pytest test infrastructure"
```

---

## Task 2: `travel_finder/web_search.py` — Serper client

**Files:**
- Create: `travel_finder/web_search.py`
- Create: `tests/test_web_search.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_web_search.py`:

```python
"""Unit tests for web_search module — all tests mock network calls."""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


def test_extract_names_from_serper_results():
    """Names are extracted from result titles and snippets."""
    from travel_finder.web_search import _extract_names

    results = [
        {"title": "Le Comptoir - Best GF restaurant in Paris", "snippet": "Le Comptoir serves amazing food"},
        {"title": "Top 10 gluten free Paris", "snippet": "Septime is popular with coeliacs"},
        {"title": "", "snippet": ""},
    ]
    names = _extract_names(results)
    assert "le comptoir" in names
    assert "septime" in names


def test_normalise_name_strips_common_suffixes():
    """Name normalisation removes suffixes and punctuation."""
    from travel_finder.web_search import _normalise

    assert _normalise("Le Comptoir Restaurant") == "le comptoir"
    assert _normalise("Café de Flore") == "café de flore"
    assert _normalise("Chez Paul, Bistro") == "chez paul"


def test_search_returns_empty_set_when_no_api_key(monkeypatch):
    """No API key → graceful empty result, no exception."""
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    from travel_finder import web_search
    result = web_search.search_gf_mentions("Paris")
    assert isinstance(result, set)
    assert len(result) == 0


def test_cache_is_used_on_repeat_call(tmp_path, monkeypatch):
    """Second call for same city+date reads cache, not network."""
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr("travel_finder.web_search._CACHE_DIR", str(tmp_path))

    fake_names = {"bistro paul", "le zinc"}
    cache_key = "paris"
    import datetime
    date_str = datetime.date.today().isoformat()
    cache_file = tmp_path / f"{cache_key}_{date_str}.json"
    cache_file.write_text(json.dumps(list(fake_names)))

    from travel_finder import web_search
    with patch("travel_finder.web_search._call_serper") as mock_call:
        result = web_search.search_gf_mentions("Paris")
        mock_call.assert_not_called()
    assert result == fake_names
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/test_web_search.py -v
```

Expected: `ModuleNotFoundError` for `travel_finder.web_search`.

- [ ] **Step 3: Implement `travel_finder/web_search.py`**

```python
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
import urllib.parse
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
    for suffix in _STRIP_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _extract_names(results: list[dict[str, Any]]) -> set[str]:
    """Pull candidate restaurant names from Serper result objects."""
    names: set[str] = set()
    for r in results:
        text = f"{r.get('title', '')} {r.get('snippet', '')}"
        for match in _NAME_RE.findall(text):
            normalised = _normalise(match)
            if len(normalised) > 3:
                names.add(normalised)
    return names


def _cache_path(city_key: str) -> Path:
    date_str = datetime.date.today().isoformat()
    return Path(_CACHE_DIR) / f"{city_key}_{date_str}.json"


def _read_cache(city_key: str) -> set[str] | None:
    p = _cache_path(city_key)
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception:
            pass
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
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/test_web_search.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add travel_finder/web_search.py tests/test_web_search.py
git commit -m "feat: add web_search module — Serper GF blog discovery with 24h cache"
```

---

## Task 3: `travel_finder/gf.py` — add `scan_reviews()`

**Files:**
- Modify: `travel_finder/gf.py`
- Create: `tests/test_gf.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_gf.py`:

```python
"""Unit tests for gf.scan_reviews."""


def test_scan_reviews_counts_gf_keyword_hits():
    from travel_finder.gf import scan_reviews

    reviews = [
        {"text": "Great food, they have a gluten free menu"},
        {"text": "Perfect for coeliacs, very careful with cross-contamination"},
        {"text": "Amazing steak but no mention of dietary needs"},
        {"text": "Staff were very knowledgeable about gluten-free options"},
    ]
    assert scan_reviews(reviews) == 3


def test_scan_reviews_empty_list():
    from travel_finder.gf import scan_reviews
    assert scan_reviews([]) == 0


def test_scan_reviews_handles_missing_text_field():
    from travel_finder.gf import scan_reviews
    reviews = [{"author": "John"}, {"text": "gluten free was great"}]
    assert scan_reviews(reviews) == 1


def test_scan_reviews_case_insensitive():
    from travel_finder.gf import scan_reviews
    reviews = [{"text": "GLUTEN FREE options available"}, {"text": "Celiac-friendly"}]
    assert scan_reviews(reviews) == 2
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
pytest tests/test_gf.py -v
```

Expected: `ImportError` — `scan_reviews` does not exist yet.

- [ ] **Step 3: Add `scan_reviews` to `travel_finder/gf.py`**

Open `travel_finder/gf.py` and add after the existing imports (before `_GF_KEYWORDS` or wherever the module constants start):

```python
# Keywords indicating a review mentions GF options
_REVIEW_GF_KEYWORDS = frozenset([
    "gluten", "coeliac", "celiac", "sans gluten", "gluten-free", "glutenfree",
])


def scan_reviews(reviews: list[dict]) -> int:
    """
    Count how many review objects contain a GF-related keyword.

    Each review is a dict with at minimum a 'text' key (as returned
    by the Google Places API). Returns the count of reviews that
    mention any GF keyword (case-insensitive).
    """
    count = 0
    for r in reviews:
        text = r.get("text", "").lower()
        if any(kw in text for kw in _REVIEW_GF_KEYWORDS):
            count += 1
    return count
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/test_gf.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add travel_finder/gf.py tests/test_gf.py
git commit -m "feat: add scan_reviews() to gf module"
```

---

## Task 4: `travel_finder/claude_analyzer.py` — enrich prompt + add `gf_sources`

**Files:**
- Modify: `travel_finder/claude_analyzer.py`

No new test file needed — `_parse_json_response` is already tested implicitly; the schema change is verified by the integration test in Task 8.

- [ ] **Step 1: Update context dict in `analyze_restaurants()` (line 113–119)**

Replace:
```python
        context.append({
            "index": i,
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "types": p.get("types", []),
            "menu_text": menu_text[:2000] if menu_text else "",
        })
```

With:
```python
        context.append({
            "index": i,
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "types": p.get("types", []),
            "menu_text": menu_text[:2000] if menu_text else "",
            "blog_match": bool(p.get("blog_match", False)),
            "review_gf_count": int(p.get("review_gf_count", 0)),
        })
```

- [ ] **Step 2: Update the prompt string to include new signals and `gf_sources` output**

Replace the `prompt = f"""..."""` block (lines 121–142) with:

```python
    prompt = f"""You are analyzing restaurants for a travel recommendation app. For each restaurant, provide:

1. A 2-sentence description covering ambience, character, and cuisine style.
2. A gluten-free assessment using exactly these tiers:
   - Tier 1 "GF Confirmed": explicit GF label on menu ("sans gluten", "GF", allergy symbols, dedicated GF section) OR blog_match=true AND review_gf_count >= 1
   - Tier 2 "Likely (inferred - not labelled GF)": blog_match=true only OR review_gf_count >= 1 only OR identifiable safe dishes from cuisine type (no pasta/bread/roux/batter/pastry). Always flag as inferred.
   - Tier 3 "GF Unclear": no evidence from any source

3. A gf_sources list — include each evidence type that applies:
   - "blog" if blog_match is true
   - "menu" if the menu text explicitly mentions GF
   - "reviews:N" (e.g. "reviews:3") if review_gf_count > 0
   - "inferred" if tier is 2 and source is cuisine type only
   Leave empty list [] for Tier 3.

Return a JSON array with exactly {len(places)} objects in the same order as input. Schema per object:
{{
  "index": <integer, same as input>,
  "description": "<2-sentence write-up>",
  "gf_tier": <1, 2, or 3>,
  "gf_label": "<GF Confirmed | Likely (inferred - not labelled GF) | GF Unclear>",
  "gf_dishes": ["<dish1>", "<dish2>"],
  "gf_notes": "<explicitly labelled on menu | inferred from menu - not labelled GF | menu not accessible>",
  "gf_sources": ["<source1>", "<source2>"]
}}

Restaurants:
{json.dumps(context, ensure_ascii=False, indent=2)}

Return only the JSON array, no explanation or markdown."""
```

- [ ] **Step 3: Propagate `gf_sources` in the result merge (line 153)**

Replace:
```python
        return [result_map.get(i, _fallback_restaurant(p)) for i, p in enumerate(places)]
```

With:
```python
        output = []
        for i, p in enumerate(places):
            ai = result_map.get(i, _fallback_restaurant(p))
            ai.setdefault("gf_sources", [])
            output.append(ai)
        return output
```

- [ ] **Step 4: Update `_fallback_restaurant` to include `gf_sources`**

Add `"gf_sources": [],` to the return dict in `_fallback_restaurant()`.

- [ ] **Step 5: Commit**

```bash
git add travel_finder/claude_analyzer.py
git commit -m "feat: enrich Claude prompt with blog_match/review_gf_count, add gf_sources output"
```

---

## Task 5: `travel_finder/restaurants.py` — GF-first pipeline

**Files:**
- Modify: `travel_finder/restaurants.py`
- Create: `tests/test_restaurants.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_restaurants.py`:

```python
"""Unit tests for restaurant ranking helpers."""


def test_gf_rank_score_sorts_gf_first():
    """GF Confirmed restaurants always rank above GF Likely, regardless of distance."""
    from travel_finder.restaurants import _rank_key

    confirmed_far = {"gf_tier": 1, "rating": 4.5, "distance_km": 5.0, "name": "A", "types": []}
    likely_close  = {"gf_tier": 2, "rating": 4.9, "distance_km": 0.1, "name": "B", "types": []}
    unclear_close = {"gf_tier": 3, "rating": 5.0, "distance_km": 0.0, "name": "C", "types": []}

    assert _rank_key(confirmed_far, "") < _rank_key(likely_close, "")
    assert _rank_key(likely_close,  "") < _rank_key(unclear_close, "")


def test_gf_rank_score_blended_within_tier():
    """Within the same GF tier, higher rating + closer distance wins."""
    from travel_finder.restaurants import _rank_key

    better = {"gf_tier": 1, "rating": 4.9, "distance_km": 0.5, "name": "A", "types": []}
    worse  = {"gf_tier": 1, "rating": 4.5, "distance_km": 3.0, "name": "B", "types": []}

    assert _rank_key(better, "") < _rank_key(worse, "")


def test_normalise_name_for_blog_match():
    """Name normalisation is consistent with web_search._normalise."""
    from travel_finder.restaurants import _normalise_name

    assert _normalise_name("Le Comptoir Restaurant") == "le comptoir"
    assert _normalise_name("Café de Flore") == "café de flore"
    assert _normalise_name("CHEZ Paul, Bistro") == "chez paul"


def test_blog_match_tags_correctly():
    """A place whose normalised name is in blog_names gets blog_match=True."""
    from travel_finder.restaurants import _tag_blog_match

    blog_names = {"le comptoir", "septime"}
    place_yes = {"name": "Le Comptoir Restaurant"}
    place_no  = {"name": "Random Café"}

    _tag_blog_match(place_yes, blog_names)
    _tag_blog_match(place_no, blog_names)

    assert place_yes["blog_match"] is True
    assert place_no["blog_match"] is False
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/test_restaurants.py -v
```

Expected: `ImportError` — `_rank_key`, `_normalise_name`, `_tag_blog_match` do not exist.

- [ ] **Step 3: Add helper functions to `travel_finder/restaurants.py`**

Add these helpers after the imports, before `search_restaurants`:

```python
from .web_search import search_gf_mentions
from .gf import classify, scan_reviews

# Suffixes stripped for blog-name matching (must match web_search._STRIP_SUFFIXES)
_NAME_SUFFIXES = [
    " restaurant", " restaurants", " café", " cafe",
    " bistro", " brasserie", " bar", " grill",
]


def _normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, strip common restaurant suffixes."""
    s = name.lower().strip(" ,;.")
    for suffix in _NAME_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _tag_blog_match(place: dict[str, Any], blog_names: set[str]) -> None:
    """Set place['blog_match'] = True if normalised name is in blog_names."""
    place["blog_match"] = _normalise_name(place.get("name", "")) in blog_names


def _rank_key(place: dict[str, Any], preferences: str) -> tuple:
    """
    Sort key for GF-first ranking (ascending — lower = better rank).

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
```

Also update the import at the top of the file:

Replace:
```python
from .gf import classify
```
With:
```python
from .gf import classify, scan_reviews
```

And add:
```python
from .web_search import search_gf_mentions
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
pytest tests/test_restaurants.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Rewrite `search_restaurants()` pipeline**

Replace the body of `search_restaurants()` with:

```python
    # 1. Geocode
    center_lat, center_lng = geocode(location)

    # 2. Web search for GF blog mentions (cached, degrades gracefully)
    blog_names = search_gf_mentions(location)

    # 3. Google Places text search — lead with "gluten free" for better GF results
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

    # 6. Fetch place details for top 15 (by raw distance — pre-GF sort)
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
```

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add travel_finder/restaurants.py tests/test_restaurants.py
git commit -m "feat: GF-first pipeline — web search, review scan, new ranking formula"
```

---

## Task 6: `web/templates/partials/restaurants.html` — source chips

**Files:**
- Modify: `web/templates/partials/restaurants.html`

- [ ] **Step 1: Add chip styles at top of file (inside first `<div>` or via inline style)**

After the opening `{% if not results %}` block, inside the `{% else %}` branch, locate the shortlist card loop (`{% for r in shortlist %}`). Find the GF badge block — it currently ends around the `{% if r.gf_notes %}` section. Add the chips block immediately after the badge `<div>`:

```html
      <!-- GF source chips -->
      {% if r.gf_sources %}
      <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px;margin-bottom:2px;">
        {% for src in r.gf_sources %}
          {% if src == 'blog' %}
          <span style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;font-size:0.65rem;font-weight:500;border-radius:999px;padding:1px 7px;">📰 Blog recommended</span>
          {% elif src == 'menu' %}
          <span style="background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;font-size:0.65rem;font-weight:500;border-radius:999px;padding:1px 7px;">✓ Menu label</span>
          {% elif src.startswith('reviews:') %}
          <span style="background:#f9fafb;color:#374151;border:1px solid #e5e7eb;font-size:0.65rem;font-weight:500;border-radius:999px;padding:1px 7px;">💬 {{ src.split(':')[1] }} reviews mention GF</span>
          {% elif src == 'inferred' %}
          <span style="background:#fffbeb;color:#854d0e;border:1px solid #fde68a;font-size:0.65rem;font-weight:500;border-radius:999px;padding:1px 7px;">~ Cuisine inferred</span>
          {% endif %}
        {% endfor %}
      </div>
      {% endif %}
```

Place this block immediately after the closing `</div>` of the GF badge block (before the `<!-- Maps link -->` section).

- [ ] **Step 2: Verify template renders without error on empty gf_sources**

The `{% if r.gf_sources %}` guard ensures no chips render when the list is empty (Tier 3 fallback). Confirm by inspection — no code to run.

- [ ] **Step 3: Commit**

```bash
git add web/templates/partials/restaurants.html
git commit -m "feat: add GF source chips to restaurant shortlist cards"
```

---

## Task 7: `web/templates/index.html` — running animation

**Files:**
- Modify: `web/templates/index.html`

The current `feat/v2-redesign` index.html has a plain indigo spinner (`<div class="spinner-ring">`). Replace it with the animated chase scene.

- [ ] **Step 1: Add animation CSS to the `<style>` block**

Inside the `<style>` tag in `index.html`, add after the existing `.spinner-ring` / `@keyframes spin` rules:

```css
/* ── Loading chase animation ─────────────────────────────── */
@keyframes chase-across {
  0%   { transform: translateX(-130px); opacity: 0; }
  8%   { opacity: 1; }
  85%  { opacity: 1; }
  95%  { transform: translateX(110%); opacity: 0; }
  100% { transform: translateX(-130px); opacity: 0; }
}
@keyframes cake-wobble {
  0%, 100% { transform: rotate(-7deg) translateY(0); }
  50%       { transform: rotate(7deg) translateY(-4px); }
}
@keyframes msg-cycle {
  0%, 18%   { opacity: 1; }
  22%, 100% { opacity: 0; }
}
.chase-group  { animation: chase-across 3.4s linear infinite; display:flex; align-items:flex-end; gap:12px; }
.cake-wobble  { animation: cake-wobble 0.45s ease-in-out infinite; display:block; }
.loading-msg  { position:absolute; left:0; right:0; font-size:0.75rem; color:#6b7280; text-align:center; opacity:0; }
.loading-msg:nth-child(1) { animation: msg-cycle 14s 0s   infinite; }
.loading-msg:nth-child(2) { animation: msg-cycle 14s 3.5s infinite; }
.loading-msg:nth-child(3) { animation: msg-cycle 14s 7s   infinite; }
.loading-msg:nth-child(4) { animation: msg-cycle 14s 10.5s infinite; }
```

- [ ] **Step 2: Replace each spinner in the form submit buttons**

There are two spinner divs in the current index.html (one per form):

```html
<div id="spinner-restaurants" class="htmx-indicator items-center gap-2">
  <div class="spinner-ring"></div>
  <span style="color: #6b7280; font-size: 0.8rem;">Searching&hellip;</span>
</div>
```

and

```html
<div id="spinner-hotels" class="htmx-indicator items-center gap-2">
  <div class="spinner-ring"></div>
  <span style="color: #6b7280; font-size: 0.8rem;">Searching&hellip;</span>
</div>
```

Replace **both** with the animated loading panel below (use id `spinner-restaurants` and `spinner-hotels` respectively, keeping the HTMX indicator wiring intact). Place these panels **after** the closing `</div>` of the search card (after the `</form>` + `</div>` that closes `.card`), not inside the button row:

```html
<!-- Loading panel — restaurants -->
<div id="spinner-restaurants" class="htmx-indicator card mt-4" style="padding:1.5rem 1.25rem;text-align:center;">
  <!-- Chase scene -->
  <div style="position:relative;height:68px;overflow:hidden;margin-bottom:0.75rem;">
    <div style="position:absolute;bottom:10px;left:0;right:0;height:1px;background:#e5e7eb;"></div>
    <div class="chase-group" style="position:absolute;bottom:11px;">

      <!-- Running girl -->
      <svg width="34" height="48" viewBox="0 0 34 48" xmlns="http://www.w3.org/2000/svg">
        <!-- Hair (back) -->
        <ellipse cx="17" cy="13" rx="11" ry="12" fill="#3d1f0a"/>
        <!-- Face -->
        <ellipse cx="17" cy="14" rx="8.5" ry="9.5" fill="#f7d5b0"/>
        <!-- Fringe -->
        <path d="M6 10 Q9 3 17 5 Q25 3 28 10 Q22 7 17 8 Q12 7 6 10 Z" fill="#3d1f0a"/>
        <!-- Hair streaming back (running) -->
        <path d="M6 17 Q-4 21 -2 30" stroke="#3d1f0a" stroke-width="4" fill="none" stroke-linecap="round"/>
        <path d="M5 20 Q-5 28 -3 36" stroke="#4a2510" stroke-width="3" fill="none" stroke-linecap="round"/>
        <!-- Eyes -->
        <circle cx="12" cy="13" r="2" fill="#2c0e04"/>
        <circle cx="22" cy="13" r="2" fill="#2c0e04"/>
        <circle cx="12.7" cy="12.4" r="0.75" fill="white"/>
        <circle cx="22.7" cy="12.4" r="0.75" fill="white"/>
        <!-- Smile -->
        <path d="M13 19 Q17 21.5 21 19" stroke="#c4785a" stroke-width="1.2" fill="none" stroke-linecap="round"/>
        <!-- Body (coat) -->
        <path d="M4 48 Q6 34 11 28 L15 31 L17 38 L19 31 L23 28 Q28 34 30 48 Z" fill="#8b5a2a"/>
        <!-- Left arm pumping back -->
        <line x1="11" y1="31" x2="3" y2="37" stroke="#8b5a2a" stroke-width="2.5" stroke-linecap="round"/>
        <!-- Right arm reaching forward -->
        <line x1="23" y1="30" x2="32" y2="25" stroke="#8b5a2a" stroke-width="2.5" stroke-linecap="round"/>
        <!-- Back leg (extended behind) -->
        <line x1="13" y1="42" x2="4" y2="38" stroke="#6b3a14" stroke-width="3.5" stroke-linecap="round"/>
        <line x1="4"  y1="38" x2="1" y2="46" stroke="#6b3a14" stroke-width="3" stroke-linecap="round"/>
        <!-- Front leg (bent forward) -->
        <line x1="21" y1="41" x2="28" y2="37" stroke="#6b3a14" stroke-width="3.5" stroke-linecap="round"/>
        <line x1="28" y1="37" x2="33" y2="46" stroke="#6b3a14" stroke-width="3" stroke-linecap="round"/>
      </svg>

      <!-- GF cake (wobbling, running away) -->
      <svg class="cake-wobble" width="32" height="40" viewBox="0 0 32 40" xmlns="http://www.w3.org/2000/svg">
        <!-- Cake base -->
        <rect x="3" y="21" width="26" height="13" rx="3" fill="#6366f1"/>
        <!-- Middle layer -->
        <rect x="5" y="13" width="22" height="10" rx="2" fill="#818cf8"/>
        <!-- Icing -->
        <path d="M5 13 Q10 8 16 10 Q22 8 27 13 Z" fill="#ffffff" opacity="0.9"/>
        <!-- Candle -->
        <rect x="14" y="4" width="4" height="9" rx="1" fill="#fbbf24"/>
        <!-- Flame -->
        <ellipse cx="16" cy="3.5" rx="2.5" ry="3.5" fill="#f59e0b"/>
        <ellipse cx="16" cy="4.2" rx="1.2" ry="2" fill="#fef3c7"/>
        <!-- GF label -->
        <text x="6" y="30" font-size="8" font-weight="800" fill="#fff" font-family="sans-serif">GF!</text>
        <!-- Panicked eyes looking back -->
        <circle cx="9"  cy="17" r="2" fill="white"/>
        <circle cx="16" cy="17" r="2" fill="white"/>
        <circle cx="8.2"  cy="17.7" r="1" fill="#1e1b4b"/>
        <circle cx="15.2" cy="17.7" r="1" fill="#1e1b4b"/>
        <!-- Running legs -->
        <line x1="10" y1="34" x2="5"  y2="40" stroke="#4f46e5" stroke-width="2.5" stroke-linecap="round"/>
        <line x1="20" y1="34" x2="26" y2="40" stroke="#4f46e5" stroke-width="2.5" stroke-linecap="round"/>
      </svg>

    </div>
  </div>

  <p style="font-weight:600;color:#111827;font-size:0.88rem;margin:0 0 0.4rem 0;">Hunting down the best GF options&hellip;</p>
  <div style="position:relative;height:1.3rem;margin-bottom:0.6rem;">
    <p class="loading-msg">Reading menus so you don't have to ask the waiter again.</p>
    <p class="loading-msg">Interrogating every restaurant in a 5 km radius simultaneously.</p>
    <p class="loading-msg">Cross-referencing blogs, reviews, and menus. This takes a moment.</p>
    <p class="loading-msg">Good things take time. GF-safe good things take slightly longer.</p>
  </div>
  <p style="font-size:0.75rem;color:#9ca3af;margin:0;">Usually 2&ndash;3 minutes &mdash; fetching menus &amp; asking Claude what&rsquo;s safe&hellip;</p>
</div>
```

Duplicate this block for hotels (change id to `spinner-hotels`, change first message to "Finding your next boutique stay&hellip;").

Also **remove** the old inline spinners from inside the `<div class="flex items-center gap-3 pt-1">` button rows — replace with just the submit button:

```html
<div class="flex items-center gap-3 pt-1">
  <button type="submit" class="btn-primary">Search</button>
</div>
```

- [ ] **Step 3: Verify CSS does not conflict**

Check there is no duplicate `@keyframes spin` — the old `.spinner-ring` CSS can be removed if the ring is no longer used.

- [ ] **Step 4: Commit**

```bash
git add web/templates/index.html
git commit -m "feat: replace spinner with running-girl + GF cake loading animation"
```

---

## Task 8: Config + gitignore

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Add SERPER_API_KEY to `.env.example`**

Add after `ANTHROPIC_API_KEY`:

```
SERPER_API_KEY=your_key_here
```

- [ ] **Step 2: Add `.serper_cache/` to `.gitignore`**

Add:

```
.serper_cache/
```

- [ ] **Step 3: Commit**

```bash
git add .env.example .gitignore
git commit -m "chore: add SERPER_API_KEY to env example and gitignore cache dir"
```

---

## Task 9: Integration verification

No code changes — manual checklist only.

- [ ] **Step 1: Set env vars and run locally**

```bash
cd "C:/Users/wb559324/OneDrive - WBG/Documents/GitHub/travel-finder"
# Ensure .env has GOOGLE_MAPS_API_KEY, ANTHROPIC_API_KEY, SERPER_API_KEY
uvicorn web.app:app --reload
```

- [ ] **Step 2: Search "Paris" — verify GF-first ordering**

Open http://localhost:8000 → search "Paris" on Restaurants tab.

Confirm:
- [ ] GF Confirmed restaurants appear before GF Likely in the shortlist cards
- [ ] At least one card shows a 📰 or 💬 source chip
- [ ] Distance chip still appears on every card
- [ ] "Show all" toggle works on the table
- [ ] Map renders with indigo pins

- [ ] **Step 3: Verify degradation with no SERPER_API_KEY**

Remove `SERPER_API_KEY` from `.env`, restart, search again.

Confirm:
- [ ] App still returns results (no crash)
- [ ] No blog chips appear; other chips (menu, reviews) still show if applicable

- [ ] **Step 4: Verify loading animation**

Submit a search and confirm:
- [ ] Running girl + GF cake chase animation plays
- [ ] Rotating caption messages cycle correctly
- [ ] Animation disappears when results load

- [ ] **Step 5: Run full test suite one final time**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Push branch**

```bash
git push origin feat/v2-redesign
```

---

## Merge to master

After all tasks verified:

```bash
git checkout master
git merge feat/v2-redesign
git push origin master
```

Render will auto-deploy. Add `SERPER_API_KEY` to Render environment variables via the Render dashboard or:

```bash
curl -X PUT "https://api.render.com/v1/services/srv-d6vfq1buibrs73f0ni2g/env-vars" \
  -H "Authorization: Bearer <render_key>" \
  -H "Content-Type: application/json" \
  -d '[{"key":"SERPER_API_KEY","value":"<serper_key>"}]'
```
