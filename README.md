# Travel Finder

A web app for finding top-rated restaurants (with gluten-free classification) and boutique hotels using the Google Maps API.

## Stack
- Python FastAPI + Uvicorn
- Jinja2 + HTMX + Alpine.js + Tailwind CSS (Play CDN)
- Google Maps Places API

## Setup

1. Copy `.env.example` to `.env` and add your `GOOGLE_MAPS_API_KEY`
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `uvicorn web.app:app --reload`
4. Open http://localhost:8000

## Deploy
Hosted on Render — set `GOOGLE_MAPS_API_KEY` in the Render dashboard environment variables.
