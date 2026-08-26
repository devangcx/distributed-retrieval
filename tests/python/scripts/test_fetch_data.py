"""Tests for transforming cached TMDB responses into canonical movies."""

import json
from pathlib import Path

import pytest

import scripts.fetch_data as fetch_data
from scripts.fetch_data import (
    discover_movies,
    fetch_discovery_page,
    normalize_usable_movie,
    prepare_cached_movies,
    prepare_movies,
)


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


def test_discover_movies_collects_all_available_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pagination stops at TMDB's reported total and preserves page order."""
    # Arrange
    requested_pages = []

    def fake_fetch_page(
        client: object,
        country: str,
        language: str,
        page: int,
        raw_dir: Path,
    ) -> dict[str, object]:
        requested_pages.append(page)
        return {"page": page, "total_pages": 3, "results": [{"id": page}]}

    monkeypatch.setattr(fetch_data, "fetch_discovery_page", fake_fetch_page)

    # Act
    movies = discover_movies(object(), "US", "en", 10, tmp_path)

    # Assert
    assert requested_pages == [1, 2, 3]
    assert [movie["id"] for movie in movies] == [1, 2, 3]


def test_fetch_discovery_page_reuses_raw_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A resumed discovery run does not request an already cached page."""
    # Arrange
    api_calls = []

    def fake_tmdb_get(
        client: object,
        path: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        api_calls.append((path, params))
        return {"page": 1, "total_pages": 1, "results": [{"id": 42}]}

    monkeypatch.setattr(fetch_data, "tmdb_get", fake_tmdb_get)

    # Act
    first_response = fetch_discovery_page(object(), "US", "en", 1, tmp_path)
    second_response = fetch_discovery_page(object(), "US", "en", 1, tmp_path)

    # Assert
    assert first_response == second_response
    assert len(api_calls) == 1
    assert list(tmp_path.rglob("page_001.json"))


def test_prepare_movies_is_deterministic_and_stops_at_usable_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Candidate insertion order cannot change the selected canonical records."""
    # Arrange
    fetched_ids = []

    def fake_fetch_movie(client: object, movie_id: int, raw_dir: Path) -> dict[str, object]:
        fetched_ids.append(movie_id)
        movie = build_raw_movie(movie_id)
        if movie_id == 2:
            movie["overview"] = ""
        return movie

    monkeypatch.setattr(fetch_data, "fetch_movie", fake_fetch_movie)
    candidates = {7: {"id": 7}, 2: {"id": 2}, 5: {"id": 5}, 3: {"id": 3}}

    # Act
    movies = prepare_movies(object(), candidates, tmp_path, target_size=2)

    # Assert
    assert fetched_ids == [2, 3, 5]
    assert [movie["movie_id"] for movie in movies] == [3, 5]


def test_normalization_retains_at_most_ten_cast_members() -> None:
    """The canonical contract keeps only the first ten billing positions."""
    # Arrange
    raw_movie = build_raw_movie()
    raw_movie["credits"]["cast"] = [
        {
            "id": 200 + order,
            "name": f"Actor {order}",
            "credit_id": f"actor-credit-{order}",
            "character": "Character",
            "order": order,
        }
        for order in reversed(range(12))
    ]

    # Act
    movie = normalize_usable_movie(raw_movie)

    # Assert
    assert movie is not None
    assert [cast_member["cast_order"] for cast_member in movie["cast"]] == list(
        range(10)
    )
