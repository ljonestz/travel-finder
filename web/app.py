"""
Travel Finder Web App — FastAPI + Jinja2 + HTMX.

Routes:
    GET  /                                    → index.html (page shell)
    POST /search/restaurants                  → starts background job, returns polling fragment
    POST /search/hotels                       → starts background job, returns polling fragment
    GET  /search/poll/restaurants/{job_id}    → poll job status; returns results when done
    GET  /search/poll/hotels/{job_id}         → poll job status; returns results when done
    GET  /health                              → {"status": "ok"}
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import traceback
import uuid
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
# Jinja2's LRU cache uses a tuple containing a dict as its cache key,
# which Python 3.14 now rejects as unhashable. Disabling the cache
# works around the bug with negligible performance impact at this scale.
templates.env.cache = None

# ---------------------------------------------------------------------------
# Background job store
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}


def _cleanup_jobs() -> None:
    """Remove jobs older than 1 hour to prevent unbounded memory growth."""
    cutoff = time.time() - 3600
    stale = [jid for jid, j in list(_jobs.items()) if j.get("created", 0) < cutoff]
    for jid in stale:
        _jobs.pop(jid, None)


async def _run_in_background(job_id: str, fn, *args) -> None:
    """Run a blocking search function in a thread pool and store the result."""
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, fn, *args)
        _jobs[job_id].update(status="done", result=result)
    except Exception as exc:
        _log.error("Background job %s failed: %s", job_id, exc)
        _jobs[job_id].update(status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception as e:
        tb = traceback.format_exc()
        _log.error("Index route failed: %s", tb)
        return HTMLResponse(f"<pre>{tb}</pre>", status_code=500)


@app.post("/search/restaurants", response_class=HTMLResponse)
async def search_restaurants_route(
    request: Request,
    query: str = Form(...),
):
    try:
        _cleanup_jobs()
        location, preferences = _split_query(query)
        job_id = str(uuid.uuid4())
        _jobs[job_id] = {"status": "running", "result": None, "created": time.time()}
        asyncio.create_task(_run_in_background(job_id, search_restaurants, location, preferences))
        return templates.TemplateResponse(
            "partials/polling.html",
            {"request": request, "job_type": "restaurants", "job_id": job_id},
        )
    except Exception as e:
        _log.error("Failed to start restaurant search: %s", e)
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": f"Search failed to start: {e}"},
        )


@app.get("/search/poll/restaurants/{job_id}", response_class=HTMLResponse)
async def poll_restaurants(request: Request, job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Search session expired — please search again."},
        )
    if job["status"] == "running":
        return templates.TemplateResponse(
            "partials/polling.html",
            {"request": request, "job_type": "restaurants", "job_id": job_id},
        )
    if job["status"] == "error":
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Search failed. Check that GOOGLE_MAPS_API_KEY is set correctly."},
        )
    data = job["result"]
    maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    return templates.TemplateResponse(
        "partials/restaurants.html",
        {"request": request, "maps_api_key": maps_api_key, **data},
    )


@app.post("/search/hotels", response_class=HTMLResponse)
async def search_hotels_route(
    request: Request,
    query: str = Form(...),
):
    try:
        _cleanup_jobs()
        location, preferences = _split_query(query)
        job_id = str(uuid.uuid4())
        _jobs[job_id] = {"status": "running", "result": None, "created": time.time()}
        asyncio.create_task(_run_in_background(job_id, search_hotels, location, preferences))
        return templates.TemplateResponse(
            "partials/polling.html",
            {"request": request, "job_type": "hotels", "job_id": job_id},
        )
    except Exception as e:
        _log.error("Failed to start hotel search: %s", e)
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": f"Search failed to start: {e}"},
        )


@app.get("/search/poll/hotels/{job_id}", response_class=HTMLResponse)
async def poll_hotels(request: Request, job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Search session expired — please search again."},
        )
    if job["status"] == "running":
        return templates.TemplateResponse(
            "partials/polling.html",
            {"request": request, "job_type": "hotels", "job_id": job_id},
        )
    if job["status"] == "error":
        return templates.TemplateResponse(
            "partials/error.html",
            {"request": request, "error": "Search failed. Check that GOOGLE_MAPS_API_KEY is set correctly."},
        )
    data = job["result"]
    maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    return templates.TemplateResponse(
        "partials/hotels.html",
        {"request": request, "maps_api_key": maps_api_key, **data},
    )


@app.get("/health")
def health():
    return {"status": "ok"}
