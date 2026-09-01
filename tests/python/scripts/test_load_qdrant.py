"""Tests for deterministic Qdrant text and manifest preparation."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.load_qdrant import (
    cache_file_paths,
    full_document_text,
    generate_dense_cache,
    load_contract,
    metadata_sparse_text,
    movie_ids_checksum,
    normalize_text,
    overview_text,
    prepare_layouts,
    read_float_vectors,
    write_float_vector,
)


def movie(movie_id: int, industry: str = "hollywood") -> dict[str, object]:
    """Build a complete canonical movie fixture."""
    return {
        "movie_id": movie_id,
        "title": "  A   Movie ",
        "original_title": "Original Movie",
        "original_language": "en",
        "release_date": "2020-01-02",
        "overview": "An  overview.\nWith whitespace.",
        "runtime_minutes": 100,
        "vote_average": 7.0,
        "vote_count": 20,
        "industry": industry,
        "genres": [{"genre_id": 18, "name": "Drama"}],
        "countries": [{"country_code": "US", "name": "United States"}],
        "directors": [{
            "credit_id": f"director-{movie_id}",
            "person_id": movie_id + 100,
            "name": "Director Name",
        }],
        "cast": [{
            "credit_id": f"cast-{movie_id}",
            "person_id": movie_id + 200,
            "name": "Actor Name",
            "character_name": "The Hero",
            "cast_order": 0,
        }],
    }


def test_text_templates_are_explicit_and_deterministic() -> None:
    """Dense and sparse documents differ only by the overview line."""
    record = movie(2)

    metadata = metadata_sparse_text(record)
    full_document = full_document_text(record)

    assert overview_text(record) == "An overview. With whitespace."
    assert metadata == "\n".join([
        "Title: A Movie",
        "Original title: Original Movie",
        "Release year: 2020",
        "Genres: Drama",
        "Industry: hollywood",
        "Countries: United States",
        "Directors: Director Name",
        "Cast: Actor Name as The Hero",
    ])
    assert full_document == f"{metadata}\nOverview: An overview. With whitespace."


def test_normalize_text_uses_visible_missing_marker() -> None:
    """Missing fields cannot become ambiguous empty template values."""
    assert normalize_text(None) == "[unknown]"
    assert normalize_text(" \n ") == "[unknown]"


def test_contract_rejects_a_model_change(tmp_path: Path) -> None:
    """A model change requires an explicit new supported contract."""
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps({"dense_model": "different"}))

    with pytest.raises(ValueError, match="contract_version"):
        load_contract(contract_path)


def test_contract_vector_names_are_not_hardcoded(tmp_path: Path) -> None:
    """Renaming contract keys changes the Qdrant vector names."""
    contract = json.loads(Path("config/qdrant_contract.json").read_text())
    contract["vectors"] = {
        "plot_semantics": "canonical overview",
        "movie_semantics": "full movie document",
        "metadata_terms": "movie metadata without overview",
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    loaded_contract = load_contract(contract_path)

    assert loaded_contract.overview_vector_name == "plot_semantics"
    assert loaded_contract.full_document_vector_name == "movie_semantics"
    assert loaded_contract.metadata_sparse_vector_name == "metadata_terms"


def test_float32_embedding_artifact_round_trip(tmp_path: Path) -> None:
    """Committed dense artifacts preserve fixed dimensions and movie order."""
    vector_path = tmp_path / "vectors.f32"
    with vector_path.open("wb") as stream:
        write_float_vector(stream, [1.0, 2.0, 3.0])
        write_float_vector(stream, [4.0, 5.0, 6.0])

    vectors = read_float_vectors(vector_path, start=0, count=2, dimensions=3)

    assert vectors == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]


def test_complete_dense_cache_prevents_another_api_request(tmp_path: Path) -> None:
    """A committed compatible cache is reused before importing OpenAI."""
    contract = load_contract(Path("config/qdrant_contract.json"))
    movies = [movie(2)]
    source_checksum = "source-checksum"
    metadata_path, overview_path, full_document_path = cache_file_paths(
        tmp_path)
    for vector_path in (overview_path, full_document_path):
        with vector_path.open("wb") as stream:
            write_float_vector(stream, [0.0] * contract.dense_dimensions)
    metadata_path.write_text(json.dumps({
        "contract_version": contract.version,
        "requested_model": contract.dense_model,
        "returned_model": contract.dense_model,
        "dimensions": contract.dense_dimensions,
        "source_checksum": source_checksum,
        "record_count": 1,
        "movie_ids_checksum": movie_ids_checksum(movies),
        "total_tokens": 100,
        "format": "little-endian float32 in ascending movie_id order",
    }))

    cache = generate_dense_cache(
        tmp_path, movies, contract, source_checksum
    )

    assert cache.metadata["total_tokens"] == 100


def write_manifest(
    path: Path,
    source_bytes: bytes,
    layout: str,
    shard: str,
    movie_ids: list[int],
    source_count: int,
) -> None:
    """Write one valid layout manifest fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "1",
        "layout_strategy": layout,
        "shard_id": shard,
        "input_checksum": hashlib.sha256(source_bytes).hexdigest(),
        "source_record_count": source_count,
        "shard_record_count": len(movie_ids),
        "movie_ids": movie_ids,
    }))


def test_prepare_layouts_requires_matching_complete_assignments(tmp_path: Path) -> None:
    """Both layouts must cover identical canonical movie IDs exactly once."""
    movies = [movie(2), movie(3, "bollywood")]
    source_bytes = json.dumps(movies).encode("utf-8")
    input_path = tmp_path / "movies.json"
    input_path.write_bytes(source_bytes)
    manifest_paths = []
    for layout, shard, ids in (
        ("hash", "a", [2]),
        ("hash", "b", [3]),
        ("industry", "a", [2]),
        ("industry", "b", [3]),
    ):
        path = tmp_path / layout / f"shard_{shard}.json"
        write_manifest(path, source_bytes, layout, shard, ids, len(movies))
        manifest_paths.append(path)

    layouts = prepare_layouts(input_path, manifest_paths)

    assert set(layouts) == {
        ("hash", "a"), ("hash", "b"),
        ("industry", "a"), ("industry", "b"),
    }
