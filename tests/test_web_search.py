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
