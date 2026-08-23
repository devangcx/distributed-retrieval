"""Tests for transforming cached TMDB responses into canonical movies."""

import json
from pathlib import Path

import pytest

from scripts.fetch_data import normalize_usable_movie, prepare_cached_movies


def build_raw_movie(movie_id: int = 10) -> dict[str, object]:
    """Return the smallest representative detailed TMDB movie response."""
    return {
        "id": movie_id,
        "title": "Test Movie",
        "original_title": "Test Movie",
        "original_language": "en",
        "release_date": "2020-01-02",
        "overview": "A useful overview.",
        "runtime": 120,
        "vote_average": 7.26,
        "vote_count": 50,
        "genres": [{"id": 28, "name": "Action"}],
        "production_countries": [
            {"iso_3166_1": "US", "name": "United States of America"}
        ],
        "credits": {
            "crew": [
                {
                    "id": 100,
                    "name": "Test Director",
                    "credit_id": "director-credit",
                    "job": "Director",
                }
            ],
            "cast": [
                {
                    "id": 200,
                    "name": "Test Actor",
                    "credit_id": "actor-credit",
                    "character": "Lead",
                    "order": 0,
                }
            ],
        },
    }


def test_normalize_usable_movie_matches_database_contract() -> None:
    """Normalization uses database field names and stable credit IDs."""
    # Arrange
    raw_movie = build_raw_movie()

    # Act
    movie = normalize_usable_movie(raw_movie)

    # Assert
    assert movie is not None
    assert movie["runtime_minutes"] == 120
    assert movie["vote_average"] == 7.3
    assert movie["industry"] == "hollywood"
    assert movie["genres"] == [{"genre_id": 28, "name": "Action"}]
    assert movie["countries"] == [
        {"country_code": "US", "name": "United States of America"}
    ]
    assert movie["directors"][0]["credit_id"] == "director-credit"
    assert movie["cast"][0]["credit_id"] == "actor-credit"


@pytest.mark.parametrize("field", ["title", "overview"])
def test_normalize_usable_movie_rejects_blank_required_text(field: str) -> None:
    """Blank required text is rejected before reaching PostgreSQL."""
    # Arrange
    raw_movie = build_raw_movie()
    raw_movie[field] = "   "

    # Act
    movie = normalize_usable_movie(raw_movie)

    # Assert
    assert movie is None


def test_prepare_cached_movies_is_sorted_and_skips_unusable_records(
    tmp_path: Path,
) -> None:
    """Cache rebuilding is deterministic and excludes unusable movies."""
    # Arrange
    unusable_movie = build_raw_movie(movie_id=3)
    unusable_movie["overview"] = ""
    cached_movies = [build_raw_movie(movie_id=20), build_raw_movie(movie_id=5)]
    for movie in [*cached_movies, unusable_movie]:
        cache_path = tmp_path / f"{movie['id']}.json"
        cache_path.write_text(json.dumps(movie), encoding="utf-8")

    # Act
    movies = prepare_cached_movies(tmp_path)

    # Assert
    assert [movie["movie_id"] for movie in movies] == [5, 20]
