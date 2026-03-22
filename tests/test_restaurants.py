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
