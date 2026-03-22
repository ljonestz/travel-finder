"""Unit tests for gf.scan_reviews."""


def test_scan_reviews_counts_gf_keyword_hits():
    from travel_finder.gf import scan_reviews

    reviews = [
        {"text": "Great food, they have a gluten free menu"},
        {"text": "Perfect for coeliacs, very careful with cross-contamination"},
        {"text": "Amazing steak but no mention of dietary needs"},
        {"text": "Staff were very knowledgeable about gluten-free options"},
    ]
    assert scan_reviews(reviews) == 3


def test_scan_reviews_empty_list():
    from travel_finder.gf import scan_reviews
    assert scan_reviews([]) == 0


def test_scan_reviews_handles_missing_text_field():
    from travel_finder.gf import scan_reviews
    reviews = [{"author": "John"}, {"text": "gluten free was great"}]
    assert scan_reviews(reviews) == 1


def test_scan_reviews_case_insensitive():
    from travel_finder.gf import scan_reviews
    reviews = [{"text": "GLUTEN FREE options available"}, {"text": "Celiac-friendly"}]
    assert scan_reviews(reviews) == 2
