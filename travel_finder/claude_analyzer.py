"""
AI-powered restaurant and hotel analysis using Claude claude-sonnet-4-6.

Analyzes up to 10 places in a single API call, returning descriptions
and (for restaurants) structured GF-tier assessments.

Falls back to algorithmic GF data already in the place dict if the
Claude call fails or ANTHROPIC_API_KEY is not set.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)
_SSL_CTX = ssl._create_unverified_context()


# ---------------------------------------------------------------------------
# HTML fetching
# ---------------------------------------------------------------------------

def _fetch_html(url: str, timeout: int = 6) -> str:
    """Fetch page HTML; return empty string on any error."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            raw = resp.read(40_000)
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _fetch_menu_text(website: str) -> str:
    """
    Try to fetch useful menu text from a restaurant website.
    Attempts homepage, then /menu and /carte sub-paths.
    Returns up to 3000 chars of the best HTML found.
    """
    if not website:
        return ""
    html = _fetch_html(website)
    if not html:
        base = website.rstrip("/")
        for path in ["/menu", "/carte", "/menu-carte"]:
            html = _fetch_html(base + path)
            if html:
                break
    return html[:3000] if html else ""


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def _get_client():
    """Create Anthropic client with SSL verification disabled for proxy compat."""
    import anthropic
    import httpx  # anthropic SDK depends on httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    # verify=False handles WBG proxy and Render environments alike
    http_client = httpx.Client(verify=False)
    return anthropic.Anthropic(api_key=api_key, http_client=http_client)


def _parse_json_response(raw: str) -> list[dict]:
    """Strip optional markdown fences and parse JSON array."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        # parts[1] is the content between first pair of fences
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# Restaurant analysis
# ---------------------------------------------------------------------------

def analyze_restaurants(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Analyze up to 10 restaurants in a single Claude claude-sonnet-4-6 call.

    Fetches menu HTML for each place, then asks Claude to return:
      - description: 2-sentence write-up (ambience, character, cuisine style)
      - gf_tier: 1 / 2 / 3
      - gf_label: exact label string
      - gf_dishes: list of 1-2 specific dish names
      - gf_notes: short explanation

    Falls back to the algorithmic GF data already in each place dict if
    the API call fails.
    """
    if not places:
        return []

    # Fetch menu text for each place
    context: list[dict] = []
    for i, p in enumerate(places):
        menu_text = _fetch_menu_text(p.get("website", ""))
        context.append({
            "index": i,
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "types": p.get("types", []),
            "menu_text": menu_text[:2000] if menu_text else "",
        })

    prompt = f"""You are analyzing restaurants for a travel recommendation app. For each restaurant, provide:

1. A 2-sentence description covering ambience, character, and cuisine style.
2. A gluten-free assessment using exactly these tiers:
   - Tier 1 "GF Confirmed": menu explicitly mentions gluten-free ("sans gluten", "GF", allergy symbols, dedicated GF section)
   - Tier 2 "Likely (inferred - not labelled GF)": no explicit GF label but identifiable safe dishes exist (avoid pasta/bread/roux/batter/pastry). Always flag this as inferred.
   - Tier 3 "GF Unclear": menu inaccessible AND cuisine types give no safe inference

Return a JSON array with exactly {len(places)} objects in the same order as input. Schema per object:
{{
  "index": <integer, same as input>,
  "description": "<2-sentence write-up>",
  "gf_tier": <1, 2, or 3>,
  "gf_label": "<GF Confirmed | Likely (inferred - not labelled GF) | GF Unclear>",
  "gf_dishes": ["<dish1>", "<dish2>"],
  "gf_notes": "<explicitly labelled on menu | inferred from menu - not labelled GF | menu not accessible>"
}}

Restaurants:
{json.dumps(context, ensure_ascii=False, indent=2)}

Return only the JSON array, no explanation or markdown."""

    try:
        client = _get_client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        analyzed = _parse_json_response(message.content[0].text)
        result_map = {item["index"]: item for item in analyzed}
        return [result_map.get(i, _fallback_restaurant(p)) for i, p in enumerate(places)]
    except Exception as e:
        _log.warning("Claude restaurant analysis failed: %s — using algorithmic GF fallback", e)
        return [_fallback_restaurant(p) for p in places]


def _fallback_restaurant(p: dict[str, Any]) -> dict[str, Any]:
    """Return algorithmic GF data already computed in the place dict."""
    tier = p.get("gf_tier", 3)
    notes_map = {
        1: "explicitly labelled on menu",
        2: "inferred from cuisine types - not labelled GF",
        3: "menu not accessible",
    }
    return {
        "description": "",
        "gf_tier": tier,
        "gf_label": p.get("gf_label", "GF Unclear"),
        "gf_dishes": p.get("gf_dishes", []),
        "gf_notes": notes_map.get(tier, "menu not accessible"),
    }


# ---------------------------------------------------------------------------
# Hotel analysis
# ---------------------------------------------------------------------------

def analyze_hotels(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Analyze up to 10 hotels in a single Claude claude-sonnet-4-6 call.

    Returns a list of dicts with:
      - description: 2-sentence write-up (character, style, what makes it special)

    Falls back to empty description strings on failure.
    """
    if not places:
        return []

    context: list[dict] = []
    for i, p in enumerate(places):
        context.append({
            "index": i,
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "types": p.get("types", []),
            "style_tags": p.get("style_tags", []),
            "price_level": p.get("price_level"),
        })

    prompt = f"""You are analyzing boutique hotels for a travel recommendation app. For each hotel, write a 2-sentence description focusing on its character, style, and what makes it special — e.g. historic building, design aesthetic, unique location, intimate atmosphere.

Return a JSON array with exactly {len(places)} objects in the same order as input. Schema per object:
{{
  "index": <integer, same as input>,
  "description": "<2-sentence write-up>"
}}

Hotels:
{json.dumps(context, ensure_ascii=False, indent=2)}

Return only the JSON array, no explanation or markdown."""

    try:
        client = _get_client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        analyzed = _parse_json_response(message.content[0].text)
        result_map = {item["index"]: item for item in analyzed}
        return [result_map.get(i, {"description": ""}) for i in range(len(places))]
    except Exception as e:
        _log.warning("Claude hotel analysis failed: %s — using empty descriptions", e)
        return [{"description": ""} for _ in places]
