"""Focused tests for validation shared by PostgreSQL and Qdrant ingestion."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ingestion_validation import (
    prepare_shard_load,
    validate_manifest,
    validate_movies,
)


def movie(movie_id: int = 10) -> dict[str, object]:
    """Return a minimal valid canonical movie."""
    return {
        "movie_id": movie_id,
        "title": "Movie",
        "overview": "Overview",
        "industry": "hollywood",
        "directors": [],
        "cast": [],
    }


def manifest_for(
    movies: list[dict[str, object]],
    movie_ids: list[int] | None = None,
) -> tuple[dict[str, object], bytes]:
    """Return matching manifest metadata and canonical source bytes."""
    input_bytes = json.dumps(movies).encode("utf-8")
    selected_ids = movie_ids if movie_ids is not None else [
        record["movie_id"] for record in movies
    ]
    manifest = {
        "schema_version": "1",
        "layout_strategy": "hash",
        "shard_id": "a",
        "input_checksum": hashlib.sha256(input_bytes).hexdigest(),
        "source_record_count": len(movies),
        "shard_record_count": len(selected_ids),
        "movie_ids": selected_ids,
    }
    return manifest, input_bytes


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("schema_version", "2", "schema version"),
        ("layout_strategy", "random", "layout strategy"),
        ("shard_id", "c", "shard ID"),
    ],
)
def test_validate_manifest_rejects_unsupported_identity_fields(
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    """Only the supported manifest version, layouts, and shards are accepted."""
    # Arrange
    movies = [movie()]
    manifest, input_bytes = manifest_for(movies)
    manifest[field] = invalid_value

    # Act
    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest, movies, input_bytes)

    # Assert is performed by the exception match.


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("source_record_count", 2, "source count"),
        ("shard_record_count", 2, "shard count"),
    ],
)
def test_validate_manifest_rejects_incorrect_counts(
    field: str,
    invalid_value: int,
    message: str,
) -> None:
    """Manifest counts must match the source and selected ID array."""
    # Arrange
    movies = [movie()]
    manifest, input_bytes = manifest_for(movies)
    manifest[field] = invalid_value

    # Act
    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest, movies, input_bytes)

    # Assert is performed by the exception match.


@pytest.mark.parametrize("invalid_count", [-1, True, "1"])
def test_validate_manifest_rejects_non_integer_counts(
    invalid_count: object,
) -> None:
    """Counts must be non-negative integers and must not accept booleans."""
    # Arrange
    movies = [movie()]
    manifest, input_bytes = manifest_for(movies)
    manifest["source_record_count"] = invalid_count

    # Act
    with pytest.raises(ValueError, match="non-negative integer"):
        validate_manifest(manifest, movies, input_bytes)

    # Assert is performed by the exception match.


def test_validate_manifest_rejects_duplicate_movie_ids() -> None:
    """One manifest cannot select the same canonical movie twice."""
    # Arrange
    movies = [movie()]
    manifest, input_bytes = manifest_for(movies, [10, 10])

    # Act
    with pytest.raises(ValueError, match="duplicate movie IDs"):
        validate_manifest(manifest, movies, input_bytes)

    # Assert is performed by the exception match.


def test_validate_manifest_rejects_missing_movie_ids() -> None:
    """Every selected ID must exist in the checksummed canonical corpus."""
    # Arrange
    movies = [movie()]
    manifest, input_bytes = manifest_for(movies, [999])

    # Act
    with pytest.raises(ValueError, match="missing movie IDs"):
        validate_manifest(manifest, movies, input_bytes)

    # Assert is performed by the exception match.


def test_validate_movies_rejects_duplicate_movie_ids() -> None:
    """Canonical movie IDs must be globally unique."""
    # Arrange
    duplicate_movies = [movie(), movie()]

    # Act
    with pytest.raises(ValueError, match="duplicate movie ID"):
        validate_movies(duplicate_movies)

    # Assert is performed by the exception match.


@pytest.mark.parametrize("field", ["title", "overview"])
def test_validate_movies_rejects_blank_required_text(field: str) -> None:
    """Title and overview must contain searchable text."""
    # Arrange
    record = movie()
    record[field] = "  \n "

    # Act
    with pytest.raises(ValueError, match=f"blank {field}"):
        validate_movies([record])

    # Assert is performed by the exception match.


def test_validate_movies_rejects_invalid_industry() -> None:
    """Canonical industry labels must use the agreed source vocabulary."""
    # Arrange
    record = movie()
    record["industry"] = "unknown"

    # Act
    with pytest.raises(ValueError, match="invalid industry"):
        validate_movies([record])

    # Assert is performed by the exception match.


def test_validate_movies_rejects_more_than_ten_cast_members() -> None:
    """The canonical cast limit is part of the embedding contract."""
    # Arrange
    record = movie()
    record["cast"] = [
        {"credit_id": f"cast-{index}"} for index in range(11)
    ]

    # Act
    with pytest.raises(ValueError, match="at most ten cast members"):
        validate_movies([record])

    # Assert is performed by the exception match.


def test_validate_movies_rejects_invalid_credit_id() -> None:
    """Every retained director and cast credit needs a stable ID."""
    # Arrange
    record = movie()
    record["directors"] = [{"credit_id": ""}]

    # Act
    with pytest.raises(ValueError, match="invalid credit ID"):
        validate_movies([record])

    # Assert is performed by the exception match.


def test_validate_movies_rejects_duplicate_credit_ids() -> None:
    """Credit IDs must remain unique across the complete corpus."""
    # Arrange
    first_movie = movie(10)
    second_movie = movie(20)
    first_movie["directors"] = [{"credit_id": "same-credit"}]
    second_movie["cast"] = [{"credit_id": "same-credit"}]

    # Act
    with pytest.raises(ValueError, match="duplicate credit ID"):
        validate_movies([first_movie, second_movie])

    # Assert is performed by the exception match.


@pytest.mark.parametrize(
    ("corpus_content", "manifest_content", "message"),
    [
        ("{", "{}", "could not parse corpus"),
        (json.dumps([movie()]), "{", "could not read manifest"),
    ],
)
def test_prepare_shard_load_rejects_malformed_json(
    tmp_path: Path,
    corpus_content: str,
    manifest_content: str,
    message: str,
) -> None:
    """Malformed artifacts fail with a contextual parse error."""
    # Arrange
    input_path = tmp_path / "movies.json"
    manifest_path = tmp_path / "manifest.json"
    input_path.write_text(corpus_content, encoding="utf-8")
    manifest_path.write_text(manifest_content, encoding="utf-8")

    # Act
    with pytest.raises(ValueError, match=message):
        prepare_shard_load(input_path, manifest_path)

    # Assert is performed by the exception match.


@pytest.mark.parametrize(
    ("corpus_value", "manifest_value", "message"),
    [
        ({"movie_id": 10}, {}, "canonical corpus must be a JSON array"),
        ([movie()], [], "layout manifest must be a JSON object"),
    ],
)
def test_prepare_shard_load_rejects_invalid_top_level_types(
    tmp_path: Path,
    corpus_value: object,
    manifest_value: object,
    message: str,
) -> None:
    """Corpus and manifest JSON must use their documented top-level types."""
    # Arrange
    input_path = tmp_path / "movies.json"
    manifest_path = tmp_path / "manifest.json"
    input_path.write_text(json.dumps(corpus_value), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest_value), encoding="utf-8")

    # Act
    with pytest.raises(ValueError, match=message):
        prepare_shard_load(input_path, manifest_path)

    # Assert is performed by the exception match.
