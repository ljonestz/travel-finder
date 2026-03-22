"""
Travel Finder Web App — FastAPI + Jinja2 + HTMX.

Routes:
    GET  /                          → index.html (page shell)
    POST /search/restaurants        → partials/restaurants.html
    POST /search/hotels             → partials/hotels.html
    GET  /health                    → {"status": "ok"}
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from travel_finder.restaurants import search_restaurants
from travel_finder.hotels import search_hotels

load_dotenv()

_log = logging.getLogger(__name__)

app = FastAPI(title="Travel Finder")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _split_query(query: str) -> tuple[str, str]:
    """
    Split a combined query string into (location, preferences).
    Splits on the first em-dash (—) or, if absent, the first comma.
    Examples:
      "Paris — gluten free, terrace" → ("Paris", "gluten free, terrace")
      "Lisbon, seafood"              → ("Lisbon", "seafood")
      "Tokyo"                        → ("Tokyo", "")
    """
    if "\u2014" in query:  # em-dash
        loc, _, prefs = query.partition("\u2014")
    elif "," in query:
        loc, _, prefs = query.partition(",")
    else:
        loc, prefs = query, ""
    return loc.strip(), prefs.strip()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/search/restaurants", response_class=HTMLResponse)
def search_restaurants_route(
    request: Request,
    query: str = Form(...),
):
    location, preferences = _split_query(query)
    maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    try:
        data = search_restaurants(location, preferences)
        return templates.TemplateResponse(
            "partials/restaurants.html",
            {"request": request, "maps_api_key": maps_api_key, **data},
        )
    except Exception as e:
        _log.error("Restaurant search failed: %s", e)
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Search failed. Check that GOOGLE_MAPS_API_KEY is set correctly."},
        )


@app.post("/search/hotels", response_class=HTMLResponse)
def search_hotels_route(
    request: Request,
    query: str = Form(...),
):
    location, preferences = _split_query(query)
    maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    try:
        data = search_hotels(location, preferences)
        return templates.TemplateResponse(
            "partials/hotels.html",
            {"request": request, "maps_api_key": maps_api_key, **data},
        )
    except Exception as e:
        _log.error("Hotel search failed: %s", e)
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Search failed. Check that GOOGLE_MAPS_API_KEY is set correctly."},
        )


@app.get("/health")
def health():
    return {"status": "ok"}
