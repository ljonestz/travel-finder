# Gluten Free P

P's guide to gluten-free fine dining. Finds top-rated restaurants and boutique hotels ranked by distance, with AI-powered GF menu analysis.

Live: **https://glutenfreep.onrender.com**

---

## What it does

**Restaurants**
- Searches Google Maps for high-rated restaurants (≥ 4.5 stars, ≥ 50 reviews) near your location
- Ranks by distance, with GF suitability as a tiebreaker
- Classifies each restaurant into three GF tiers:
  - ✅ **GF Confirmed** — explicit GF labelling found on menu
  - 🟡 **Likely (inferred)** — safe dishes identifiable from menu/cuisine type
  - ⬜ **GF Unclear** — no accessible menu or evidence
- Shows evidence sources per restaurant: `blog` · `menu` · `reviews:N` · `inferred`
- AI descriptions (Claude Sonnet) covering ambience, character, and cuisine style
- Interactive map with pins for top 10 results
- Collapsible top-10 table

**Hotels**
- Searches for boutique hotels (no chains) near your location
- AI descriptions focusing on character, design, and what makes each special
- Same map + table layout as restaurants

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 · FastAPI · Uvicorn |
| Templates | Jinja2 · HTMX · Alpine.js · Tailwind CSS (Play CDN) |
| AI | Claude Sonnet (`claude-sonnet-4-6`) via Anthropic SDK |
| Search | Google Maps Places API · Serper.dev (GF blog search) |
| Frontend | PWA-ready · mobile-optimised · GPS location support |

---

## Local setup

```bash
# 1. Clone and install
git clone https://github.com/ljonestz/travel-finder.git
cd travel-finder
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env and add your API keys (see below)

# 3. Run
uvicorn web.app:app --reload
# Open http://localhost:8000
```

### Required environment variables

| Variable | Where to get it |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Google Cloud Console — enable Places API + Geocoding API |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `SERPER_API_KEY` | serper.dev (free tier available) |

---

## How search works

1. **Geocode** the query location via Google Maps Geocoding API
2. **GF-prefixed Places search** — `"gluten free restaurant {location}"` to bias toward GF-friendly places
3. **Score each result** — fetch details, scan reviews for GF keywords, check website HTML for GF labels, query Serper for blog mentions
4. **Claude analysis** — single API call per batch of up to 10 restaurants: fetches menu HTML (with Serper fallback for JS-rendered sites), returns descriptions + GF tier + evidence sources
5. **Rank** — primary sort by GF tier, secondary by blended rating+distance score
6. **Background polling** — search runs as a server-side job; client polls every 2s so locking the phone does not cancel the search

---

## Project structure

```
travel_finder/
  maps.py            - Google Maps API client (geocode, place search, details)
  restaurants.py     - restaurant search pipeline + GF ranking
  hotels.py          - hotel search pipeline
  gf.py              - algorithmic GF classification (fallback)
  claude_analyzer.py - AI analysis via Claude Sonnet
  web_search.py      - Serper.dev client (GF blog search, menu search, 24h cache)

web/
  app.py             - FastAPI routes + background job store
  templates/
    index.html       - page shell (search form, loading animation)
    partials/
      restaurants.html  - results: shortlist cards + map + top-10 table
      hotels.html       - same layout for hotels
      polling.html      - loading fragment (HTMX polling)
      error.html        - error partial
  static/
    manifest.json    - PWA manifest
    icon.svg         - home screen icon

tests/               - pytest unit tests (13 tests)
```

---

## Deployment (Render)

The app is deployed on Render as a Web Service.

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn web.app:app --host 0.0.0.0 --port $PORT`
- **Python version:** 3.11.9 (set in `runtime.txt`)
- **Environment variables:** set `GOOGLE_MAPS_API_KEY`, `ANTHROPIC_API_KEY`, `SERPER_API_KEY` in the Render dashboard

---

## Running tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```
