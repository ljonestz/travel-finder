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
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from travel_finder.restaurants import search_restaurants
from travel_finder.hotels import search_hotels

load_dotenv()

_log = logging.getLogger(__name__)

app = FastAPI(title="Travel Finder")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/search/restaurants", response_class=HTMLResponse)
def search_restaurants_route(
    request: Request,
    location: str = Form(...),
    preferences: str = Form(""),
):
    try:
        data = search_restaurants(location, preferences)
        return templates.TemplateResponse(
            "partials/restaurants.html",
            {"request": request, **data},
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
    location: str = Form(...),
    preferences: str = Form(""),
):
    try:
        data = search_hotels(location, preferences)
        return templates.TemplateResponse(
            "partials/hotels.html",
            {"request": request, **data},
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
