"""
AI-powered restaurant and hotel analysis using Claude claude-sonnet-4-6.

Analyses up to 10 places in a single API call, returning:
  - restaurants: description + thorough GF assessment (focused on main courses)
  - hotels: description only

GF assessment tries, in order:
  1. JSON-LD structured data on restaurant website (most reliable)
  2. Menu pages at common URL paths (homepage, /menu, /carte, /food, /dining…)
  3. Cuisine-type heuristics (safe cuisines → Tier 2 inferred)
  4. Falls back to algorithmic GF data already in the place dict

Uses ssl._create_unverified_context / httpx verify=False for WBG proxy compat.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)
_SSL_CTX = ssl._create_unverified_context()

# Cuisine types where main-course GF inference is relatively safe
_SAFE_CUISINE_MAINS: dict[str, list[str]] = {
    "japanese":       ["sashimi", "grilled fish", "yakitori", "beef tataki"],
    "lebanese":       ["grilled meats", "mezze plates", "shish taouk", "kibbeh nayyeh"],
    "middle_eastern": ["grilled meats", "mezze", "hummus plate"],
    "steakhouse":     ["grilled steak", "cote de boeuf", "grilled fish"],
    "grill":          ["grilled meats", "grilled fish"],
    "seafood":        ["grilled fish", "steamed shellfish", "whole sea bass"],
    "peruvian":       ["ceviche", "grilled meats", "lomo saltado"],
    "mexican":        ["grilled meats", "tacos (corn tortilla)", "ceviche", "guacamole"],
    "korean":         ["grilled beef (bulgogi)", "galbi ribs", "bibimbap (check sauce)"],
    "thai":           ["grilled meats", "som tam (check sauce)", "green papaya salad", "pad see ew (rice noodles)"],
    "indian":         ["tandoori meats", "dal makhani", "saag paneer", "lamb rogan josh", "chicken tikka"],
    "swiss":          ["perch fillets", "grilled trout", "rosti (check preparation)"],
    "greek":          ["grilled fish", "souvlaki", "lamb chops", "grilled octopus"],
    "turkish":        ["grilled meats (kebab)", "lamb chops", "grilled fish"],
    "ethiopian":      ["tibs (grilled meats)", "kitfo (beef tartare)", "grilled fish"],
    "brazilian":      ["churrasco (grilled meats)", "grilled fish", "picanha"],
    "argentinian":    ["grilled steak", "asado", "grilled chicken"],
    "vietnamese":     ["pho (rice noodles, check broth)", "grilled meats", "fresh spring rolls (rice paper)"],
    "moroccan":       ["grilled meats", "lamb tagine (check thickener)", "brochettes"],
    "georgian":       ["grilled meats", "grilled fish", "lobiani (bean bread — NOT GF, skip)"],
}

# Cuisine types where GF inference is risky without menu evidence
_RISKY_CUISINE_KEYWORDS = [
    "french", "italian", "pasta", "pizza", "bakery", "boulangerie",
    "chinese", "dim_sum", "dumpling", "ramen", "udon", "soba",
    "brasserie", "bistro",
]


# ---------------------------------------------------------------------------
# HTML / structured data helpers
# ---------------------------------------------------------------------------

def _fetch_html(url: str, timeout: int = 6) -> str:
    """Fetch page HTML; return empty string on any error."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=timeout) as resp:
            raw = resp.read(60_000)
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _html_to_text(html: str) -> str:
    """Strip HTML tags and return readable plain text."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>",  " ", text,  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    return text.strip()


def _extract_json_ld(html: str) -> str:
    """
    Extract schema.org JSON-LD structured data (Restaurant / Menu / MenuItem).
    Returns a compact text summary, or empty string if nothing useful found.
    """
    matches = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    parts: list[str] = []
    for raw in matches:
        try:
            data = json.loads(raw.strip())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                typ = item.get("@type", "")
                if typ not in ("Restaurant", "FoodEstablishment", "Menu",
                               "MenuSection", "MenuItem", "CafeOrCoffeeShop"):
                    continue
                chunk: list[str] = []
                for field in ("name", "description", "servesCuisine"):
                    if item.get(field):
                        chunk.append(f"{field}: {item[field]}")
                if item.get("hasMenu"):
                    chunk.append(f"hasMenu: {str(item['hasMenu'])[:400]}")
                if chunk:
                    parts.append(" | ".join(chunk))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return "\n".join(parts) if parts else ""


def _score_menu_relevance(text: str) -> int:
    """Return a relevance score for how menu-like a page is."""
    lower = text.lower()
    score = 0
    for kw in ["menu", "starter", "main", "mains", "entrée", "plat", "dish",
               "dessert", "appetizer", "à la carte", "carte"]:
        score += lower.count(kw)
    if "gluten" in lower:
        score += 10
    if "sans gluten" in lower or "gluten free" in lower or "gluten-free" in lower:
        score += 20
    return score


def _fetch_menu_text(website: str) -> str:
    """
    Aggressively fetch menu content from a restaurant website.

    Tries the homepage first (for JSON-LD), then a broad set of menu URL paths.
    Returns up to 5000 chars of the best text found.
    """
    if not website:
        return ""

    base = website.rstrip("/")
    menu_paths = [
        "",                              # homepage — often has JSON-LD
        "/menu", "/menus", "/carte",
        "/food", "/dining", "/eat",
        "/our-menu", "/menu-carte",
        "/en/menu", "/fr/menu", "/fr/carte",
        "/en/food", "/la-carte",
        "/restaurant", "/gastronomy",
    ]

    best_text = ""
    best_score = -1

    for path in menu_paths:
        url = base + path if path else base
        html = _fetch_html(url)
        if not html:
            continue

        # 1. Try JSON-LD first — most reliable source
        json_ld = _extract_json_ld(html)
        if json_ld and len(json_ld) > 80:
            return json_ld[:5000]

        # 2. Plain text extraction + scoring
        text = _html_to_text(html)
        score = _score_menu_relevance(text)
        if score > best_score:
            best_score = score
            best_text = text
            if score >= 20:  # explicit GF mention — stop here
                break

    return best_text[:5000] if best_text else ""


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def _get_client():
    """Anthropic client with SSL verification disabled for proxy/Render compat."""
    import anthropic
    import httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(verify=False))


def _parse_json_response(raw: str) -> list[dict]:
    """Strip optional markdown fences and parse JSON array."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# Restaurant analysis
# ---------------------------------------------------------------------------

def analyze_restaurants(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Analyse up to 10 restaurants in a single Claude call.

    Fetches menu content for each place (website HTML + JSON-LD), then asks
    Claude for a description and thorough GF assessment focused on main courses.

    Falls back to algorithmic GF data already in each place dict if the call fails.
    """
    if not places:
        return []

    context: list[dict] = []
    for i, p in enumerate(places):
        menu_text = _fetch_menu_text(p.get("website", ""))
        types_lower = " ".join(p.get("types", [])).lower()

        # Pre-flag risky cuisines so Claude knows to be conservative
        is_risky = any(kw in types_lower for kw in _RISKY_CUISINE_KEYWORDS)
        safe_cuisine_hint = next(
            (mains for kw, mains in _SAFE_CUISINE_MAINS.items() if kw in types_lower),
            None,
        )

        context.append({
            "index": i,
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "types": p.get("types", []),
            "editorial_summary": p.get("editorial_summary", ""),
            "cuisine_is_risky_for_gf": is_risky,
            "safe_cuisine_typical_mains": safe_cuisine_hint,
            "menu_text": menu_text[:3000] if menu_text else "",
            "review_snippets": p.get("review_snippets", []),
        })

    prompt = f"""You are a specialist in gluten-free dining, analysing restaurants for a discerning food app.

For each restaurant below, provide:

1. **description**: 3 sentences. Cover ambience and character (1 sentence), cuisine style and what stands out about the menu (1 sentence), and a specific reason why this is or isn't a good GF dining option (1 sentence).

2. **GF assessment** — focused on MAIN COURSES, using exactly these tiers. Work through them in order; stop when determined:

   **Tier 1 "GF Confirmed"**: The restaurant's own menu/website explicitly uses GF labels — "sans gluten", "gluten-free", "GF", allergy matrix, or a dedicated GF section. Name the specific labelled main course dishes found.

   **Tier 2 "Likely (inferred - not labelled GF)"**: No explicit GF label BUT either:
   (a) Menu text shows identifiable safe main course options — no pasta, bread, flour-based sauces, breadcrumbs, pastry, beer batter, couscous, or roux; OR
   (b) The cuisine type naturally produces GF-safe mains even without menu access — use `safe_cuisine_typical_mains` as your guide.

   Cuisine defaults (apply even without menu access unless there is a specific reason not to):
   - Japanese: sashimi, grilled fish, yakitori → Tier 2
   - Steakhouse / grill / BBQ: grilled steak, grilled fish → Tier 2
   - Lebanese / Middle Eastern: grilled meats, mezze → Tier 2
   - Seafood: grilled or steamed fish and shellfish → Tier 2
   - Peruvian: ceviche, grilled meats → Tier 2
   - Mexican: corn tortilla tacos, grilled meats, ceviche → Tier 2
   - Indian: tandoori meats, dal, curry (check thickeners) → Tier 2
   - Korean: bulgogi, galbi → Tier 2
   - Greek / Turkish: grilled meats, grilled fish → Tier 2
   - Thai: grilled meats, rice noodle dishes → Tier 2
   - Brazilian / Argentinian churrasco: grilled meats → Tier 2
   - Vietnamese: pho (rice noodles), fresh spring rolls → Tier 2

   If `cuisine_is_risky_for_gf` is true (French brasserie, Italian, Chinese dim sum, bakery): require actual menu evidence before assigning Tier 2.

   List 1–2 specific likely-safe mains. ALWAYS flag as inferred.

   **Tier 3 "GF Unclear"**: Menu inaccessible AND cuisine type gives no reliable GF inference AND no helpful reviewer mentions. Do NOT use Tier 3 for cuisines listed above.

   **Additional evidence sources** — check these in order if menu text is missing:
   - `editorial_summary`: Google's own description of the restaurant — use it to identify cuisine type even when types[] is vague
   - `review_snippets`: real customer reviews. If ANY snippet mentions "gluten-free", "sans gluten", "celiac", "coeliac" or "GF" positively, upgrade to Tier 1 (if they confirm it's labelled/catered for) or Tier 2 (if they just mention it exists). Quote the reviewer briefly in gf_notes.
   - If review_snippets describe the restaurant type/cuisine (e.g. "amazing Italian pasta"), use that to inform your tier decision.

   **Important**: Aim for Tier 1 or Tier 2 wherever honestly justifiable. Tier 3 should be a last resort. It is better to say "Likely GF — inferred from cuisine type" than to leave someone without guidance.

Return a JSON array with exactly {len(places)} objects (same order). Schema per object:
{{
  "index": <integer>,
  "description": "<3-sentence write-up including a GF-relevant observation>",
  "gf_tier": <1, 2, or 3>,
  "gf_label": "<GF Confirmed | Likely (inferred - not labelled GF) | GF Unclear>",
  "gf_dishes": ["<specific main course dish>", "<second dish if known>"],
  "gf_notes": "<explicitly labelled on menu | inferred from menu - not labelled GF | inferred from cuisine type - not labelled GF | menu not accessible>"
}}

Restaurants:
{json.dumps(context, ensure_ascii=False, indent=2)}

Return only the JSON array."""

    try:
        client = _get_client()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3500,
            messages=[{"role": "user", "content": prompt}],
        )
        analyzed = _parse_json_response(message.content[0].text)
        result_map = {item["index"]: item for item in analyzed}
        return [result_map.get(i, _fallback_restaurant(p)) for i, p in enumerate(places)]
    except Exception as e:
        _log.warning("Claude restaurant analysis failed: %s — using algorithmic GF fallback", e)
        return [_fallback_restaurant(p) for p in places]


def _fallback_restaurant(p: dict[str, Any]) -> dict[str, Any]:
    tier = p.get("gf_tier", 3)
    notes = {1: "explicitly labelled on menu",
             2: "inferred from cuisine types - not labelled GF",
             3: "menu not accessible"}
    return {
        "description": "",
        "gf_tier": tier,
        "gf_label": p.get("gf_label", "GF Unclear"),
        "gf_dishes": p.get("gf_dishes", []),
        "gf_notes": notes.get(tier, "menu not accessible"),
    }


# ---------------------------------------------------------------------------
# Hotel analysis
# ---------------------------------------------------------------------------

def analyze_hotels(places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Analyse up to 10 hotels in a single Claude call.
    Returns a list of dicts with `description` only.
    """
    if not places:
        return []

    context = [
        {"index": i, "name": p.get("name", ""), "address": p.get("address", ""),
         "types": p.get("types", []), "style_tags": p.get("style_tags", []),
         "price_level": p.get("price_level")}
        for i, p in enumerate(places)
    ]

    prompt = f"""You are analysing boutique hotels for a discerning travel app. For each hotel, write a 2-sentence description focusing on character, style, and what makes it special — e.g. historic building, unique architecture, intimate scale, location.

Return a JSON array with exactly {len(places)} objects (same order):
{{ "index": <integer>, "description": "<2-sentence write-up>" }}

Hotels:
{json.dumps(context, ensure_ascii=False, indent=2)}

Return only the JSON array."""

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
