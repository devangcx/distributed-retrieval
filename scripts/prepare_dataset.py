"""Discover TMDB movies, cache their raw metadata, and build a JSON dataset."""

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

BASE_URL = "https://api.themoviedb.org/3"

# Data directories and output paths
RAW_DIR = Path("data/raw/movies")
PROCESSED_DIR = Path("data/processed")
OUTPUT_PATH = PROCESSED_DIR / "movies.json"

# Request configuration
MAX_REQUEST_ATTEMPTS = 6
REQUEST_TIMEOUT_SECONDS = 30
EARLIEST_RELEASE_DATE = "1990-01-01"
LATEST_RELEASE_DATE = "2026-08-01"

# Environment variable for the TMDB API token
TMDB_TOKEN_ENV_VAR = "TMDB_API_READ_ACCESS_TOKEN"

JsonObject = dict[str, Any]


def create_tmdb_client(token: str) -> httpx.Client:
    """Create an authenticated HTTP client for the TMDB API."""
    return httpx.Client(
        base_url=BASE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def tmdb_get(
    client: httpx.Client,
    path: str,
    params: dict[str, object] | None = None,
) -> JsonObject:
    """Request a TMDB resource, retrying rate limits and server errors."""
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        response = client.get(path, params=params)

        if response.status_code == 429:
            # TMDB supplies a delay when its request quota has been
            # exceeded.
            wait_seconds = int(response.headers.get("Retry-After", 2))

        elif response.status_code >= 500:
            # Exponential backoff gives transient server failures time to
            # clear.
            wait_seconds = 2**attempt
        else:
            response.raise_for_status()
            return response.json()

        if attempt < MAX_REQUEST_ATTEMPTS - 1:
            print(
                f"TMDB request failed (status {response.status_code}), "
                f"retrying in {wait_seconds} seconds: {path}"
            )
            time.sleep(wait_seconds)

    raise RuntimeError(f"TMDB request failed after retries: {path}")


def discover_movies(
    client: httpx.Client,
    country: str,
    language: str,
) -> list[JsonObject]:
    """Discover popular movies for an origin country and original language."""
    response = tmdb_get(
        client,
        "/discover/movie",
        {
            "include_adult": "false",
            "include_video": "false",
            "language": "en-US",
            "sort_by": "popularity.desc",
            "with_origin_country": country,
            "with_original_language": language,
            "primary_release_date.gte": EARLIEST_RELEASE_DATE,
            "primary_release_date.lte": LATEST_RELEASE_DATE,
            "vote_count.gte": 20,
            "page": 1,
        },
    )
    return response["results"]


def determine_industry_proxy(language: str | None, countries: list[str]) -> str:
    """Classify a movie using its language and production-country metadata."""
    if language == "hi" and "IN" in countries:
        return "bollywood"
    if language == "en" and "US" in countries:
        return "hollywood"
    return "other_or_ambiguous"


def extract_directors(credits: JsonObject) -> list[JsonObject]:
    """Extract directors from a TMDB credits response."""
    return [
        {"person_id": person["id"], "name": person["name"]}
        for person in credits.get("crew", [])
        if person.get("job") == "Director"
    ]


def extract_cast(credits: JsonObject) -> list[JsonObject]:
    """Extract the first ten billed cast members from TMDB credits."""
    cast_by_billing_order = sorted(
        credits.get("cast", []),
        key=lambda person: person.get("order", 9999),
    )
    return [
        {
            "person_id": person["id"],
            "name": person["name"],
            "character": person.get("character"),
            "order": person.get("order"),
        }
        for person in cast_by_billing_order[:10]
    ]


def normalize_movie(movie: JsonObject) -> JsonObject:
    """Convert detailed TMDB metadata into the dataset's movie schema."""
    production_countries = movie.get("production_countries", [])
    country_codes = [country["iso_3166_1"] for country in production_countries]
    language = movie.get("original_language")
    release_date = movie.get("release_date") or None
    credits = movie.get("credits", {})

    return {
        "movie_id": movie["id"],
        "title": movie.get("title"),
        "original_title": movie.get("original_title"),
        "release_date": release_date,
        "release_year": int(release_date[:4]) if release_date else None,
        "genres": movie.get("genres", []),
        "directors": extract_directors(credits),
        "cast": extract_cast(credits),
        "overview": movie.get("overview"),
        "original_language": language,
        "production_countries": production_countries,
        "production_companies": movie.get("production_companies", []),
        "runtime": movie.get("runtime"),
        "vote_average": movie.get("vote_average"),
        "vote_count": movie.get("vote_count"),
        "popularity": movie.get("popularity"),
        "industry_proxy": determine_industry_proxy(language, country_codes),
    }


def discover_candidates(client: httpx.Client) -> dict[int, JsonObject]:
    """Discover and deduplicate US English and Indian Hindi movies by ID."""
    discovery_groups = (
        discover_movies(client, country="US", language="en"),
        discover_movies(client, country="IN", language="hi"),
    )
    return {
        movie["id"]: movie
        for discovery_group in discovery_groups
        for movie in discovery_group
    }


def fetch_movie(client: httpx.Client, movie_id: int, raw_dir: Path) -> JsonObject:
    """Load detailed movie metadata from the cache or fetch and cache it."""
    raw_path = raw_dir / f"{movie_id}.json"
    if raw_path.exists():
        return json.loads(raw_path.read_text(encoding="utf-8"))

    movie = tmdb_get(
        client,
        f"/movie/{movie_id}",
        {"append_to_response": "credits", "language": "en-US"},
    )
    raw_path.write_text(
        json.dumps(movie, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Pace uncached detail requests to reduce the chance of rate limiting.
    time.sleep(0.1)
    return movie


def prepare_movies(
    client: httpx.Client,
    movies: dict[int, JsonObject],
    raw_dir: Path,
) -> list[JsonObject]:
    """Fetch, filter, and normalize all discovered movie candidates."""
    normalized_movies = []
    candidate_count = len(movies)

    for index, movie_id in enumerate(movies, start=1):
        movie = fetch_movie(client, movie_id, raw_dir)
        if movie.get("overview"):
            normalized_movies.append(normalize_movie(movie))
        print(f"{index}/{candidate_count}: {movie.get('title')}")

    return normalized_movies


def write_movies(movies: list[JsonObject], output_path: Path) -> None:
    """Write normalized movies as a UTF-8 JSON array."""
    output_path.write_text(
        json.dumps(movies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_tmdb_token() -> str:
    """Load and return the required TMDB API token from the environment."""
    load_dotenv()
    token = os.getenv(TMDB_TOKEN_ENV_VAR)
    if not token:
        raise RuntimeError(f"{TMDB_TOKEN_ENV_VAR} is missing from .env")
    return token


def main() -> None:
    """Build the processed movie dataset from TMDB discovery results."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    with create_tmdb_client(get_tmdb_token()) as client:
        candidates = discover_candidates(client)
        normalized_movies = prepare_movies(client, candidates, RAW_DIR)

    write_movies(normalized_movies, OUTPUT_PATH)
    print(f"\nSaved {len(normalized_movies)} usable movies to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
