"""Shared validation for manifest-selected canonical movie ingestion."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAYOUT_STRATEGIES = {"hash", "industry"}
SHARD_IDS = {"a", "b"}
CANONICAL_INDUSTRIES = {"hollywood", "bollywood", "other_or_ambiguous"}

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
            f"could not read {description} {path}: {error}"
        ) from error


def prepare_shard_load(input_path: Path, manifest_path: Path) -> ShardLoad:
    """Validate corpus checksum, manifest metadata, IDs, and movie contracts."""
    try:
        input_bytes = input_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read corpus {input_path}: {error}") from error

    try:
        movies = json.loads(input_bytes)
    except json.JSONDecodeError as error:
        raise ValueError(f"could not parse corpus {input_path}: {error}") from error
    manifest = load_json(manifest_path, "manifest")

    if not isinstance(movies, list):
        raise ValueError("canonical corpus must be a JSON array")
    if not isinstance(manifest, dict):
        raise ValueError("layout manifest must be a JSON object")

    validate_movies(movies)
    validate_manifest(manifest, movies, input_bytes)

    movies_by_id = {movie["movie_id"]: movie for movie in movies}
    selected_movies = [movies_by_id[movie_id] for movie_id in manifest["movie_ids"]]
    return ShardLoad(manifest=manifest, movies=selected_movies)


def validate_manifest(
    manifest: JsonObject,
    movies: list[JsonObject],
    input_bytes: bytes,
) -> None:
    """Reject a manifest that does not exactly describe the canonical corpus."""
    if manifest.get("schema_version") != "1":
        raise ValueError("unsupported manifest schema version")
    if manifest.get("layout_strategy") not in LAYOUT_STRATEGIES:
        raise ValueError("manifest has an invalid layout strategy")
    if manifest.get("shard_id") not in SHARD_IDS:
        raise ValueError("manifest has an invalid shard ID")

    expected_checksum = hashlib.sha256(input_bytes).hexdigest()
    if manifest.get("input_checksum") != expected_checksum:
        raise ValueError("manifest checksum does not match the canonical corpus")

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
            f"manifest references missing movie IDs: {sorted(missing_ids)}"
        )


def validate_movies(movies: list[JsonObject]) -> None:
    """Validate canonical fields required by both database loaders."""
    movie_ids: set[int] = set()
    credit_ids: set[str] = set()

    for index, movie in enumerate(movies):
        if not isinstance(movie, dict):
            raise ValueError(f"movie at index {index} is not an object")
        movie_id = movie.get("movie_id")
        if not isinstance(movie_id, int) or isinstance(movie_id, bool):
            raise ValueError(f"movie at index {index} has an invalid movie_id")
        if movie_id in movie_ids:
            raise ValueError(f"canonical corpus contains duplicate movie ID {movie_id}")
        movie_ids.add(movie_id)

        for field in ("title", "overview"):
            value = movie.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"movie {movie_id} has blank {field}")
        if movie.get("industry") not in CANONICAL_INDUSTRIES:
            raise ValueError(f"movie {movie_id} has an invalid industry")

        cast = movie.get("cast")
        directors = movie.get("directors")
        if not isinstance(cast, list) or len(cast) > 10:
            raise ValueError(f"movie {movie_id} must have at most ten cast members")
        if not isinstance(directors, list):
            raise ValueError(f"movie {movie_id} directors must be an array")
        for credit in [*directors, *cast]:
            credit_id = credit.get("credit_id") if isinstance(credit, dict) else None
            if not isinstance(credit_id, str) or not credit_id:
                raise ValueError(f"movie {movie_id} has an invalid credit ID")
            if credit_id in credit_ids:
                raise ValueError(
                    f"canonical corpus contains duplicate credit ID {credit_id}"
                )
            credit_ids.add(credit_id)
