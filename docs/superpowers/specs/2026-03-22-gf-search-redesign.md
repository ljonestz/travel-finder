# GF-First Restaurant Search Redesign

**Date:** 2026-03-22
**Status:** Approved
**Scope:** Restaurants only — hotels unchanged

---

## Overview

Redesign the restaurant search pipeline to prioritise gluten-free suitability as the primary ranking signal. Currently the app ranks by distance first and uses GF tier as a secondary filter. The new design inverts this: GF evidence quality ranks first, a blended rating+proximity score second, and ambiance match third.

A new web search layer (Serper.dev) adds GF blog and specialist-site recommendations as the highest-confidence signal. Google review text is scanned for GF keyword mentions. Claude synthesises all signals into a tier + source-evidence list. The UI exposes this evidence via small source chips on each result card.

A fun animated loading graphic (running girl chasing a GF cake) replaces the plain spinner in the current v2 design.

---

## Goals

- Surface the most GF-safe restaurants first, not just the closest
- Make the GF confidence level transparent (show the source of each rating)
- Keep cost per search ≤ $0.30
- Degrade gracefully if Serper API key is missing

---

## Non-Goals

- Hotels are not changed
- No changes to the map section or table layout
- No real-time scraping of findmeglutenfree.com (web search surfaces those pages as results automatically)

---

## Architecture

### Data Sources (parallel, per search)

| Source | How | Signal produced |
|---|---|---|
| Serper.dev web search | 2 queries: `"gluten free restaurants [city]"` + `"site:findmeglutenfree.com [city]"` | Set of restaurant name strings from result titles/snippets |
| Google Places API | Text search: `"gluten free restaurant [city] [prefs]"` + Place Details (website, 5 reviews) | Places with rating, reviews, website HTML |
| Review text scan | Keyword scan of up to 5 reviews per place | `review_gf_count: int` |
| Cuisine inference | Existing heuristics in `gf.py` | Fallback safe dishes |

### GF Tier Assignment (Claude)

Claude receives per-place context: `blog_match`, `review_gf_count`, menu HTML, cuisine types.

| Evidence | Tier |
|---|---|
| Explicit GF label on menu **or** blog match + ≥1 confirming review | 1 — GF Confirmed |
| Blog match only **or** review mentions only **or** safe cuisine type | 2 — GF Likely (inferred) |
| No evidence | 3 — GF Unclear |

### Ranking Formula

```
primary   = gf_tier                                          # 1 < 2 < 3 (lower = better)
secondary = -(rating * 0.6 + 1/(dist_km + 0.5) * 0.4)      # negate: higher score = better
tertiary  = -count_pref_keyword_matches(name, types, prefs)  # negate: more matches = better
sort key  = (primary, secondary, tertiary)                   # sort ascending on all three
```

### GF Source Evidence (`gf_sources: list[str]`)

Populated by Claude from available signals. Maps to UI chips:

| Value | Chip |
|---|---|
| `"blog"` | 📰 Blog recommended (blue) |
| `"menu"` | ✓ Menu label (green) |
| `"reviews:N"` | 💬 N reviews mention GF (gray) |
| `"inferred"` | ~ Cuisine inferred (amber) |

---

## Files Changed

### New: `travel_finder/web_search.py`

```
search_gf_mentions(location: str) -> set[str]
```

- Calls Serper.dev `/search` endpoint with 2 queries
- Extracts restaurant name candidates from `title` and `snippet` fields
- Caches results to `.serper_cache/{city}_{date}.json` (24h TTL)
- Returns empty set if `SERPER_API_KEY` not set (graceful degradation)
- Uses `ssl._create_unverified_context()` for WBG proxy compatibility

### Updated: `travel_finder/gf.py`

Add:
```
scan_reviews(reviews: list[str]) -> int
```
Counts review strings containing any of: `gluten`, `coeliac`, `celiac`, `sans gluten`, `gluten-free`. Returns count.

No changes to existing `classify()`.

### Updated: `travel_finder/claude_analyzer.py`

Each place dict in the prompt gains two new fields (menu HTML is already included in the existing prompt — no change needed there):
- `blog_match: bool`
- `review_gf_count: int`

Claude output gains:
- `gf_sources: list[str]`

Tier assignment instructions updated per the table above.

### Updated: `travel_finder/restaurants.py`

Pipeline changes:
1. Call `web_search.search_gf_mentions(location)` → `blog_names: set[str]`
2. For each enriched place, set `blog_match = name_normalised in blog_names`
3. Scan reviews: `review_gf_count = gf.scan_reviews(details.get("reviews", []))`
4. Pass `blog_match` and `review_gf_count` into Claude analyzer
5. After Claude returns, apply new ranking formula (replaces distance-only sort)
6. Propagate `gf_sources` to result dict

Name normalisation for blog matching: lowercase, strip punctuation, strip common suffixes (` restaurant`, ` café`, ` cafe`, ` bistro`).

### Updated: `web/templates/partials/restaurants.html`

Shortlist cards gain source chips below the GF badge:

```html
{% for src in r.gf_sources %}
  {% if src == 'blog' %}
    <span class="chip-blue">📰 Blog recommended</span>
  {% elif src == 'menu' %}
    <span class="chip-green">✓ Menu label</span>
  {% elif src.startswith('reviews:') %}
    <span class="chip-gray">💬 {{ src.split(':')[1] }} reviews mention GF</span>
  {% elif src == 'inferred' %}
    <span class="chip-amber">~ Cuisine inferred</span>
  {% endif %}
{% endfor %}
```

Full-results table rows: unchanged (GF badge only).

### Updated: `web/templates/index.html` — Loading Animation

Replace the plain indigo spinner with an animated SVG scene:

- **Girl figure** (left): running pose with proper stride legs (back leg extended behind, front leg bent forward), arms pumping, hair streaming back. Warm brown palette matching existing character design.
- **GF cake** (right, slightly ahead of girl): wobble animation, panicked eyes looking back, `GF!` label, running legs. Same design as master branch.
- **Chase animation**: whole group translates left-to-right, resets. Duration ~3.4s, linear, infinite.
- **Caption**: rotating messages below ("Interrogating every menu...", "An AI is reading menus so you don't have to ask the waiter...", etc.)
- **Ground line**: subtle `#e5e7eb` horizontal rule.

### Config

- `.env.example`: add `SERPER_API_KEY=your_key_here`
- Render deployment: add `SERPER_API_KEY` env var

---

## Cost Per Search

| Item | Cost |
|---|---|
| Google Places text search + 10× details | ~$0.19 |
| Serper.dev (2 queries) | ~$0.02 |
| Claude claude-sonnet-4-6 (1 batch, 10 places) | ~$0.04 |
| **Total** | **~$0.25** |

Repeat searches for the same city within 24h skip the Serper call (cache hit) → ~$0.23.

---

## Error Handling / Degradation

| Failure | Behaviour |
|---|---|
| `SERPER_API_KEY` missing | `blog_match = False` for all places; proceed normally |
| Serper call fails | Log warning; treat as empty result set |
| Claude call fails | Fall back to algorithmic GF from `gf.py` (existing behaviour) |
| Review text unavailable | `review_gf_count = 0` |

---

## Verification

1. Search "Paris gluten free" → GF Confirmed results appear before GF Likely, regardless of distance
2. A result with `blog_match=True` shows 📰 chip
3. A result with reviews mentioning GF shows 💬 chip with correct count
4. Removing `SERPER_API_KEY` from env → app still works, no chips show for blog source
5. Loading animation shows running girl chasing wobbling cake
6. Cost per search ≈ $0.25 (check Serper + Anthropic dashboards)
