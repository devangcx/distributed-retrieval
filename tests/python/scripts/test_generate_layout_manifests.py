"""Unit tests for deterministic layout-manifest generation."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.generate_layout_manifests import (
    assign_movies_to_shards,
    build_manifest,
    ensure_valid_source_movies,
    load_movies,
    select_hash_shard,
    select_industry_shard,
    write_strategy_manifests,
)


def build_movies() -> list[dict[str, object]]:
    """Return a small corpus spanning both layout strategies."""
    return [
        {"movie_id": 2, "industry": "hollywood"},
        {"movie_id": 3, "industry": "bollywood"},
        {"movie_id": 4, "industry": "other_or_ambiguous"},
    ]


def test_hash_layout_uses_movie_id_parity() -> None:
    """The hash layout assigns even IDs to A and odd IDs to B."""
    # Arrange
    movies = build_movies()

    # Act
    shard_assignments = assign_movies_to_shards(movies, select_hash_shard)

    # Assert
    assert shard_assignments == {"a": [2, 4], "b": [3]}


def test_industry_layout_routes_only_hollywood_to_a() -> None:
    """The industry layout assigns only Hollywood movies to A."""
    # Arrange
    movies = build_movies()

    # Act
    shard_assignments = assign_movies_to_shards(movies, select_industry_shard)

    # Assert
    assert shard_assignments == {"a": [2], "b": [3, 4]}


def test_duplicate_movie_ids_are_rejected() -> None:
    """Duplicate source IDs fail before a manifest can be written."""
    # Arrange
    movie = build_movies()[0]
    duplicate_movies = [movie, movie]

    # Act
    with pytest.raises(ValueError) as error:
        ensure_valid_source_movies(duplicate_movies)

    # Assert
    assert "duplicate movie IDs" in str(error.value)


def test_invalid_industry_is_rejected() -> None:
    """Unsupported industry values fail before shard assignment."""
    # Arrange
    movies = [{"movie_id": 1, "industry": "unknown"}]

    # Act
    with pytest.raises(ValueError) as error:
        ensure_valid_source_movies(movies)

    # Assert
    assert "invalid industry" in str(error.value)


def test_load_movies_returns_source_file_checksum(tmp_path: Path) -> None:
    """The manifest checksum identifies the exact canonical input bytes."""
    # Arrange
    input_path = tmp_path / "movies.json"
    source_bytes = json.dumps(build_movies()).encode("utf-8")
    input_path.write_bytes(source_bytes)

    # Act
    movies, checksum = load_movies(input_path)

    # Assert
    assert movies == build_movies()
    assert checksum == hashlib.sha256(source_bytes).hexdigest()


def test_build_manifest_records_counts_and_assignment_metadata() -> None:
    """Manifest metadata is sufficient to audit a shard load."""
    # Arrange
    movie_ids = [2, 4]

    # Act
    manifest = build_manifest("hash", "a", movie_ids, "checksum", 3)

    # Assert
    assert manifest == {
        "schema_version": "1",
        "layout_strategy": "hash",
        "shard_id": "a",
        "input_checksum": "checksum",
        "source_record_count": 3,
        "shard_record_count": 2,
        "movie_ids": [2, 4],
    }


def test_write_strategy_manifests_writes_both_shards(tmp_path: Path) -> None:
    """Writing a strategy produces one readable manifest per shard."""
    # Arrange
    shard_assignments = {"a": [2, 4], "b": [3]}

    # Act
    write_strategy_manifests(
        "hash", shard_assignments, "checksum", 3, tmp_path
    )
    shard_a = json.loads((tmp_path / "hash" / "shard_a.json").read_text())
    shard_b = json.loads((tmp_path / "hash" / "shard_b.json").read_text())

    # Assert
    assert shard_a["movie_ids"] == [2, 4]
    assert shard_b["movie_ids"] == [3]
    assert shard_a["source_record_count"] == shard_b["source_record_count"] == 3
