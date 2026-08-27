"""Validate and load one manifest-selected movie shard into PostgreSQL.

Dry-run usage does not import a PostgreSQL driver or connect to a database:

    python scripts/load_postgres.py \
        --manifest data/processed/partitions/hash/shard_a.json \
        --dry-run

Database usage requires ``psycopg`` and an explicit URL environment variable:

    python scripts/load_postgres.py \
        --manifest data/processed/partitions/hash/shard_a.json \
        --database-url-env POSTGRES_SHARD_A_URL
"""

import argparse
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

DEFAULT_INPUT_PATH = Path("data/processed/movies.json")
LAYOUT_SCHEMAS = {"hash": "hash_layout", "industry": "industry_layout"}
SHARD_DATABASE_URL_ENV = {
    "a": "POSTGRES_SHARD_A_URL",
    "b": "POSTGRES_SHARD_B_URL",
}
DATABASE_INDUSTRIES = {
    "hollywood": "hollywood",
    "bollywood": "bollywood",
    "other_or_ambiguous": "other",
}

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ShardLoad:
    """Validated corpus records and the manifest that selected them."""

    manifest: JsonObject
    movies: list[JsonObject]


def positive_manifest_count(value: object, field: str) -> int:
    """Validate a non-negative integer manifest count without accepting bool."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"manifest {field} must be a non-negative integer")
    return value


def load_json(path: Path, description: str) -> object:
    """Read one UTF-8 JSON artifact with a contextual parse error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"could not read {description} {path}: {error}") from error


def prepare_shard_load(input_path: Path, manifest_path: Path) -> ShardLoad:
    """Validate corpus checksum, manifest metadata, IDs, and movie contracts."""
    try:
        input_bytes = input_path.read_bytes()
    except OSError as error:
        raise ValueError(
            f"could not read corpus {input_path}: {error}") from error

    try:
        movies = json.loads(input_bytes)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"could not parse corpus {input_path}: {error}") from error
    manifest = load_json(manifest_path, "manifest")

    if not isinstance(movies, list):
        raise ValueError("canonical corpus must be a JSON array")
    if not isinstance(manifest, dict):
        raise ValueError("partition manifest must be a JSON object")

    validate_movies(movies)
    validate_manifest(manifest, movies, input_bytes)

    movies_by_id = {movie["movie_id"]: movie for movie in movies}
    selected_movies = [movies_by_id[movie_id]
                       for movie_id in manifest["movie_ids"]]
    return ShardLoad(manifest=manifest, movies=selected_movies)


def validate_manifest(
    manifest: JsonObject,
    movies: list[JsonObject],
    input_bytes: bytes,
) -> None:
    """Reject a manifest that does not exactly describe the canonical corpus."""
    if manifest.get("schema_version") != "1":
        raise ValueError("unsupported manifest schema version")
    if manifest.get("partition_strategy") not in LAYOUT_SCHEMAS:
        raise ValueError("manifest has an invalid partition strategy")
    if manifest.get("shard_id") not in SHARD_DATABASE_URL_ENV:
        raise ValueError("manifest has an invalid shard ID")

    expected_checksum = hashlib.sha256(input_bytes).hexdigest()
    if manifest.get("input_checksum") != expected_checksum:
        raise ValueError(
            "manifest checksum does not match the canonical corpus")

    source_record_count = positive_manifest_count(
        manifest.get("source_record_count"), "source_record_count"
    )
    shard_record_count = positive_manifest_count(
        manifest.get("shard_record_count"), "shard_record_count"
    )
    movie_ids = manifest.get("movie_ids")
    if not isinstance(movie_ids, list) or any(
        not isinstance(movie_id, int) or isinstance(movie_id, bool)
        for movie_id in movie_ids
    ):
        raise ValueError("manifest movie_ids must be an integer array")
    if source_record_count != len(movies):
        raise ValueError("manifest source count does not match the corpus")
    if shard_record_count != len(movie_ids):
        raise ValueError("manifest shard count does not match its movie IDs")
    if len(movie_ids) != len(set(movie_ids)):
        raise ValueError("manifest contains duplicate movie IDs")

    source_ids = {movie["movie_id"] for movie in movies}
    missing_ids = set(movie_ids) - source_ids
    if missing_ids:
        raise ValueError(
            f"manifest references missing movie IDs: {sorted(missing_ids)}")


def validate_movies(movies: list[JsonObject]) -> None:
    """Validate fields needed for deterministic relational loading."""
    movie_ids: set[int] = set()
    credit_ids: set[str] = set()

    for index, movie in enumerate(movies):
        if not isinstance(movie, dict):
            raise ValueError(f"movie at index {index} is not an object")
        movie_id = movie.get("movie_id")
        if not isinstance(movie_id, int) or isinstance(movie_id, bool):
            raise ValueError(f"movie at index {index} has an invalid movie_id")
        if movie_id in movie_ids:
            raise ValueError(
                f"canonical corpus contains duplicate movie ID {movie_id}")
        movie_ids.add(movie_id)

        for field in ("title", "overview"):
            value = movie.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"movie {movie_id} has blank {field}")
        if movie.get("industry") not in DATABASE_INDUSTRIES:
            raise ValueError(f"movie {movie_id} has an invalid industry")

        cast = movie.get("cast")
        directors = movie.get("directors")
        if not isinstance(cast, list) or len(cast) > 10:
            raise ValueError(
                f"movie {movie_id} must have at most ten cast members")
        if not isinstance(directors, list):
            raise ValueError(f"movie {movie_id} directors must be an array")
        for credit in [*directors, *cast]:
            credit_id = credit.get("credit_id") if isinstance(
                credit, dict) else None
            if not isinstance(credit_id, str) or not credit_id:
                raise ValueError(f"movie {movie_id} has an invalid credit ID")
            if credit_id in credit_ids:
                raise ValueError(
                    f"canonical corpus contains duplicate credit ID {credit_id}")
            credit_ids.add(credit_id)


def database_industry(canonical_industry: str) -> str:
    """Map the canonical industry label to the finalized PostgreSQL enum."""
    return DATABASE_INDUSTRIES[canonical_industry]


def load_postgres_shard(database_url: str, shard_load: ShardLoad) -> None:
    """Replace one layout schema with its complete manifest-selected shard."""
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as error:
        raise RuntimeError(
            "psycopg is required for database loading; install scripts/requirements.txt"
        ) from error

    schema = LAYOUT_SCHEMAS[shard_load.manifest["partition_strategy"]]
    run_id = uuid.uuid4()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            # The schema is selected from a fixed internal mapping,
            # not user text. So no SQL injection risk here.
            cursor.execute(f"SET search_path TO {schema}")
            insert_ingestion_run(cursor, run_id, shard_load.manifest)
        connection.commit()

        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    replace_shard(cursor, shard_load.movies)
        except Exception as error:
            with connection.cursor() as cursor:
                finish_ingestion_run(
                    cursor, run_id, "failed", Jsonb({"error": str(error)}))
            connection.commit()
            raise
        else:
            with connection.cursor() as cursor:
                finish_ingestion_run(cursor, run_id, "completed", Jsonb({}))
            connection.commit()


def insert_ingestion_run(cursor: Any, run_id: uuid.UUID, manifest: JsonObject) -> None:
    """Create an audit record before changing shard data."""
    cursor.execute(
        """
        INSERT INTO ingestion_runs (
            run_id, source_name, schema_version, input_checksum,
            source_record_count, shard_record_count, partition_strategy,
            shard_id, status, started_at
        ) VALUES (%s, 'movies.json', %s, %s, %s, %s, %s, %s,
                  'running', CURRENT_TIMESTAMP)
        """,
        (
            run_id,
            manifest["schema_version"],
            manifest["input_checksum"],
            manifest["source_record_count"],
            manifest["shard_record_count"],
            manifest["partition_strategy"],
            manifest["shard_id"],
        ),
    )


def replace_shard(cursor: Any, movies: list[JsonObject]) -> None:
    """Clear and rebuild one layout schema within the active transaction."""
    # PostgreSQL TRUNCATE is transactional. If an insert fails, rollback
    # restores the previous shard rather than leaving an empty schema.
    cursor.execute("TRUNCATE movies, genres, people, countries CASCADE")
    insert_movies(cursor, movies)
    insert_dimensions_and_relationships(cursor, movies)


def insert_movies(cursor: Any, movies: list[JsonObject]) -> None:
    """Batch insert canonical movie scalar fields into an empty schema."""
    rows = [
        (
            movie["movie_id"],
            movie["title"],
            movie.get("original_title"),
            movie.get("original_language"),
            movie.get("release_date"),
            movie["overview"],
            movie.get("runtime_minutes"),
            Decimal(str(movie["vote_average"]))
            if movie.get("vote_average") is not None
            else None,
            movie.get("vote_count", 0),
            database_industry(movie["industry"]),
        )
        for movie in movies
    ]
    cursor.executemany(
        """
        INSERT INTO movies (
            movie_id, title, original_title, original_language, release_date,
            overview, runtime_minutes, vote_average, vote_count, industry
        ) VALUES (%s, %s, %s, %s, %s::date, %s, %s, %s, %s,
                  %s::industry_type)
        """,
        rows,
    )


def insert_dimensions_and_relationships(cursor: Any, movies: list[JsonObject]) -> None:
    """Insert deduplicated dimensions before their movie relationships."""
    genres: dict[int, str] = {}
    countries: dict[str, str] = {}
    people: dict[int, str] = {}
    movie_genres = []
    movie_countries = []
    credits = []

    for movie in movies:
        movie_id = movie["movie_id"]
        for genre in movie.get("genres", []):
            genres[genre["genre_id"]] = genre["name"]
            movie_genres.append((movie_id, genre["genre_id"]))
        for country in movie.get("countries", []):
            countries[country["country_code"]] = country["name"]
            movie_countries.append((movie_id, country["country_code"]))
        credits.extend(credit_rows(movie, people))

    insert_named_dimensions(cursor, "genres", "genre_id", genres.items())
    insert_named_dimensions(cursor, "countries",
                            "country_code", countries.items())
    insert_named_dimensions(cursor, "people", "person_id", people.items())
    cursor.executemany(
        "INSERT INTO movie_genres (movie_id, genre_id) VALUES (%s, %s)",
        movie_genres,
    )
    cursor.executemany(
        "INSERT INTO movie_countries (movie_id, country_code) VALUES (%s, %s)",
        movie_countries,
    )
    cursor.executemany(
        """
        INSERT INTO movie_credits (
            credit_id, movie_id, person_id, credit_type,
            character_name, cast_order
        ) VALUES (%s, %s, %s, %s::credit_type, %s, %s)
        """,
        credits,
    )


def insert_named_dimensions(
    cursor: Any,
    table: str,
    identifier_column: str,
    rows: Iterable[tuple[object, str]],
) -> None:
    """Insert a fixed, internally selected ID/name dimension table."""
    allowed_dimensions = {
        ("genres", "genre_id"),
        ("countries", "country_code"),
        ("people", "person_id"),
    }
    if (table, identifier_column) not in allowed_dimensions:
        raise ValueError("unsupported dimension table")
    cursor.executemany(
        f"""
        INSERT INTO {table} ({identifier_column}, name) VALUES (%s, %s)
        """,
        list(rows),
    )


def credit_rows(movie: JsonObject, people: dict[int, str]) -> list[tuple[object, ...]]:
    """Build actor and director rows while collecting their people records."""
    rows = []
    for director in movie.get("directors", []):
        people[director["person_id"]] = director["name"]
        rows.append(
            (
                director["credit_id"],
                movie["movie_id"],
                director["person_id"],
                "director",
                None,
                None,
            )
        )
    for cast_member in movie.get("cast", []):
        people[cast_member["person_id"]] = cast_member["name"]
        rows.append(
            (
                cast_member["credit_id"],
                movie["movie_id"],
                cast_member["person_id"],
                "actor",
                cast_member.get("character_name"),
                cast_member.get("cast_order"),
            )
        )
    return rows


def finish_ingestion_run(
    cursor: Any,
    run_id: uuid.UUID,
    status: str,
    details: object,
) -> None:
    """Record completion or failure after the data transaction finishes."""
    cursor.execute(
        """
        UPDATE ingestion_runs
        SET status = %s::ingestion_status,
            completed_at = CURRENT_TIMESTAMP,
            details = %s
        WHERE run_id = %s
        """,
        (status, details, run_id),
    )


def parse_args() -> argparse.Namespace:
    """Parse dry-run or explicit database-loading options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database-url-env")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Validate one shard and optionally replace its PostgreSQL schema data."""
    load_dotenv()
    args = parse_args()
    shard_load = prepare_shard_load(args.input, args.manifest)
    manifest = shard_load.manifest
    print(
        f"Validated {len(shard_load.movies)} movies for "
        f"{manifest['partition_strategy']} shard {manifest['shard_id']} "
        f"(source: {manifest['input_checksum']})"
    )

    if args.dry_run:
        print("Dry run complete; PostgreSQL was not changed.")
        return

    expected_environment = SHARD_DATABASE_URL_ENV[manifest["shard_id"]]
    if args.database_url_env != expected_environment:
        raise ValueError(
            f"manifest shard {manifest['shard_id']} must use {expected_environment}"
        )
    database_url = os.getenv(expected_environment)
    if not database_url:
        raise RuntimeError(
            f"environment variable {expected_environment} is missing")
    load_postgres_shard(database_url, shard_load)
    print("PostgreSQL shard load completed.")


if __name__ == "__main__":
    main()
