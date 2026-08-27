"""Tests for offline validation and row preparation by the PostgreSQL loader."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.load_postgres import database_industry, prepare_shard_load, replace_shard
from scripts.setup_postgres_layouts import run_diesel_migrations


def movie(movie_id: int = 10) -> dict[str, object]:
    """Build a minimal canonical movie accepted by the loader."""
    return {
        "movie_id": movie_id,
        "title": "Movie",
        "original_title": "Movie",
        "original_language": "en",
        "release_date": "2020-01-01",
        "overview": "Overview",
        "runtime_minutes": 100,
        "vote_average": 7.0,
        "vote_count": 20,
        "industry": "hollywood",
        "genres": [],
        "countries": [],
        "directors": [],
        "cast": [],
    }


def write_artifacts(
    tmp_path: Path,
    movies: list[dict[str, object]],
    movie_ids: list[int],
) -> tuple[Path, Path]:
    """Write matching corpus and manifest fixtures."""
    input_path = tmp_path / "movies.json"
    input_bytes = json.dumps(movies).encode("utf-8")
    input_path.write_bytes(input_bytes)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "partition_strategy": "hash",
                "shard_id": "a",
                "input_checksum": hashlib.sha256(input_bytes).hexdigest(),
                "source_record_count": len(movies),
                "shard_record_count": len(movie_ids),
                "movie_ids": movie_ids,
            }
        ),
        encoding="utf-8",
    )
    return input_path, manifest_path


def test_prepare_shard_load_selects_manifest_movies(tmp_path: Path) -> None:
    """Only manifest-selected records proceed to database loading."""
    # Arrange
    input_path, manifest_path = write_artifacts(
        tmp_path,
        [movie(2), movie(4), movie(6)],
        [2, 6],
    )

    # Act
    shard_load = prepare_shard_load(input_path, manifest_path)

    # Assert
    assert [record["movie_id"] for record in shard_load.movies] == [2, 6]


def test_prepare_shard_load_rejects_checksum_mismatch(tmp_path: Path) -> None:
    """A manifest cannot silently select records from another corpus version."""
    # Arrange
    input_path, manifest_path = write_artifacts(tmp_path, [movie()], [10])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["input_checksum"] = "incorrect"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Act
    with pytest.raises(ValueError, match="checksum"):
        prepare_shard_load(input_path, manifest_path)

    # Assert is performed by the exception match.


def test_database_industry_maps_ambiguous_label_explicitly() -> None:
    """The canonical and finalized PostgreSQL enum labels remain distinct."""
    # Arrange
    canonical_industry = "other_or_ambiguous"

    # Act
    database_value = database_industry(canonical_industry)

    # Assert
    assert database_value == "other"


def test_run_diesel_migrations_targets_schema_through_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration setup keeps credentials out of command arguments."""
    # Arrange
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **options: object) -> None:
        captured["command"] = command
        captured["options"] = options

    monkeypatch.setattr("scripts.setup_postgres_layouts.subprocess.run", fake_run)

    # Act
    run_diesel_migrations("postgresql://secret", "hash_layout")

    # Assert
    assert captured["command"] == ["diesel", "migration", "run"]
    environment = captured["options"]["env"]
    assert environment["DATABASE_URL"] == "postgresql://secret"
    assert environment["PGOPTIONS"] == "-c search_path=hash_layout"


def test_replace_shard_truncates_before_inserting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-time ingestion clears the active schema before rebuilding it."""
    # Arrange
    events: list[str] = []

    class FakeCursor:
        def execute(self, query: str) -> None:
            events.append(query)

    monkeypatch.setattr(
        "scripts.load_postgres.insert_movies",
        lambda cursor, movies: events.append("insert movies"),
    )
    monkeypatch.setattr(
        "scripts.load_postgres.insert_dimensions_and_relationships",
        lambda cursor, movies: events.append("insert relationships"),
    )

    # Act
    replace_shard(FakeCursor(), [movie()])

    # Assert
    assert events == [
        "TRUNCATE movies, genres, people, countries CASCADE",
        "insert movies",
        "insert relationships",
    ]
