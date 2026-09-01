"""Create and populate the four manifest-defined Qdrant collections.

The command validates everything offline by default. Passing ``--execute``
permits paid OpenAI embedding requests and writes to the two Qdrant shards.
Existing collections are never deleted or replaced by this script.
"""

import argparse
import array
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from scripts.ingestion_validation import JsonObject, ShardLoad, prepare_shard_load

DEFAULT_INPUT_PATH = Path("data/processed/movies.json")
DEFAULT_CONTRACT_PATH = Path("config/qdrant_contract.json")
DEFAULT_EMBEDDING_DIRECTORY = Path(
    "data/processed/embeddings/movie-retrieval-v1"
)
DEFAULT_MANIFEST_PATHS = (
    Path("data/processed/layouts/hash/shard_a.json"),
    Path("data/processed/layouts/hash/shard_b.json"),
    Path("data/processed/layouts/industry/shard_a.json"),
    Path("data/processed/layouts/industry/shard_b.json"),
)
QDRANT_URL_ENV = {"a": "QDRANT_SHARD_A_URL", "b": "QDRANT_SHARD_B_URL"}
EXPECTED_LAYOUTS = {("hash", "a"), ("hash", "b"),
                    ("industry", "a"), ("industry", "b")}
BATCH_SIZE = 64
UNKNOWN_VALUE = "[unknown]"
EMPTY_LIST_VALUE = "[none]"


@dataclass(frozen=True)
class VectorContract:
    """The fixed, versioned settings that determine stored vectors."""

    version: str
    dense_model: str
    dense_dimensions: int
    sparse_model: str
    collections: dict[str, str]
    overview_vector_name: str
    full_document_vector_name: str
    metadata_sparse_vector_name: str


@dataclass(frozen=True)
class DenseEmbeddingCache:
    """Validated paths and metadata for committed OpenAI dense vectors."""

    directory: Path
    metadata: JsonObject


def load_contract(path: Path) -> VectorContract:
    """Read and validate the small set of supported contract-v1 settings."""
    try:
        raw_contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read vector contract {path}: {error}") from error

    expected_values = {
        "contract_version": "movie-retrieval-v1",
        "dense_model": "text-embedding-3-large",
        "dense_dimensions": 1024,
        "dense_distance": "cosine",
        "sparse_model": "Qdrant/bm25",
        "sparse_modifier": "idf",
        "cast_limit": 10,
    }
    for field, expected_value in expected_values.items():
        if raw_contract.get(field) != expected_value:
            raise ValueError(
                f"vector contract {field} must be {expected_value!r}"
            )

    collections = raw_contract.get("collections")
    if collections != {
        "hash": "movies_hash_v1",
        "industry": "movies_industry_v1",
    }:
        raise ValueError("vector contract has unsupported collection names")

    vectors = raw_contract.get("vectors")
    expected_descriptions = {
        "canonical overview",
        "full movie document",
        "movie metadata without overview",
    }
    if not isinstance(vectors, dict) or set(vectors.values()) != expected_descriptions:
        raise ValueError("vector contract must name the three supported representations")
    if any(not isinstance(name, str) or not name for name in vectors):
        raise ValueError("vector contract names must be non-blank strings")

    vector_name_by_description = {
        description: name for name, description in vectors.items()
    }

    return VectorContract(
        version=raw_contract["contract_version"],
        dense_model=raw_contract["dense_model"],
        dense_dimensions=raw_contract["dense_dimensions"],
        sparse_model=raw_contract["sparse_model"],
        collections=collections,
        overview_vector_name=vector_name_by_description["canonical overview"],
        full_document_vector_name=vector_name_by_description[
            "full movie document"
        ],
        metadata_sparse_vector_name=vector_name_by_description[
            "movie metadata without overview"
        ],
    )


def normalize_text(value: object, missing: str = UNKNOWN_VALUE) -> str:
    """Collapse whitespace while preserving the source case and punctuation."""
    if value is None:
        return missing
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    return normalized or missing


def joined_values(values: Iterable[object]) -> str:
    """Join an ordered metadata list without silently dropping blank values."""
    normalized_values = [normalize_text(value) for value in values]
    return ", ".join(normalized_values) if normalized_values else EMPTY_LIST_VALUE


def release_year(movie: JsonObject) -> str:
    """Derive the release year from the authoritative canonical date."""
    release_date = movie.get("release_date")
    if not isinstance(release_date, str) or len(release_date) < 4:
        return UNKNOWN_VALUE
    return release_date[:4]


def cast_description(cast_member: JsonObject) -> str:
    """Represent one cast credit in canonical billing order."""
    name = normalize_text(cast_member.get("name"))
    character = normalize_text(cast_member.get("character_name"), missing="")
    return f"{name} as {character}" if character else name


def metadata_lines(movie: JsonObject) -> list[str]:
    """Build the shared deterministic metadata template."""
    return [
        f"Title: {normalize_text(movie.get('title'))}",
        f"Original title: {normalize_text(movie.get('original_title'))}",
        f"Release year: {release_year(movie)}",
        f"Genres: {joined_values(genre.get('name') for genre in movie['genres'])}",
        f"Industry: {normalize_text(movie.get('industry'))}",
        f"Countries: {joined_values(country.get('name') for country in movie['countries'])}",
        f"Directors: {joined_values(director.get('name') for director in movie['directors'])}",
        f"Cast: {joined_values(cast_description(member) for member in movie['cast'])}",
    ]


def overview_text(movie: JsonObject) -> str:
    """Return the normalized overview-only dense document."""
    return normalize_text(movie["overview"])


def metadata_sparse_text(movie: JsonObject) -> str:
    """Return metadata for exact lexical matching with BM25."""
    return "\n".join(metadata_lines(movie))


def full_document_text(movie: JsonObject) -> str:
    """Return metadata and overview as one deterministic dense document."""
    return "\n".join([*metadata_lines(movie), f"Overview: {overview_text(movie)}"])


def text_sha256(text: str) -> str:
    """Identify the exact normalized text supplied to an embedding model."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare_layouts(
    input_path: Path,
    manifest_paths: Iterable[Path],
) -> dict[tuple[str, str], ShardLoad]:
    """Validate the four manifests and their complete layout coverage."""
    layouts: dict[tuple[str, str], ShardLoad] = {}
    for manifest_path in manifest_paths:
        shard_load = prepare_shard_load(input_path, manifest_path)
        key = (
            shard_load.manifest["layout_strategy"],
            shard_load.manifest["shard_id"],
        )
        if key in layouts:
            raise ValueError(f"duplicate layout manifest for {key}")
        layouts[key] = shard_load

    if set(layouts) != EXPECTED_LAYOUTS:
        raise ValueError("exactly four hash/industry shard manifests are required")

    for layout_strategy in ("hash", "industry"):
        shard_a_ids = {movie["movie_id"] for movie in layouts[(layout_strategy, "a")].movies}
        shard_b_ids = {movie["movie_id"] for movie in layouts[(layout_strategy, "b")].movies}
        if shard_a_ids & shard_b_ids:
            raise ValueError(f"{layout_strategy} layout manifests overlap")
        if len(shard_a_ids | shard_b_ids) != layouts[(layout_strategy, "a")].manifest[
            "source_record_count"
        ]:
            raise ValueError(f"{layout_strategy} layout does not cover the corpus")
    return layouts


def movie_assignments(
    layouts: dict[tuple[str, str], ShardLoad],
) -> dict[str, dict[int, str]]:
    """Map each canonical movie ID to its physical shard in each layout."""
    assignments: dict[str, dict[int, str]] = {"hash": {}, "industry": {}}
    for (layout_strategy, shard_id), shard_load in layouts.items():
        for movie in shard_load.movies:
            assignments[layout_strategy][movie["movie_id"]] = shard_id
    return assignments


def batches(values: list[JsonObject], size: int) -> Iterable[list[JsonObject]]:
    """Yield stable, bounded movie batches for API and Qdrant requests."""
    for start in range(0, len(values), size):
        yield values[start:start + size]


def movie_ids_checksum(movies: list[JsonObject]) -> str:
    """Identify the exact ordered movie IDs represented by a dense cache."""
    encoded_ids = json.dumps(
        [movie["movie_id"] for movie in movies], separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded_ids).hexdigest()


def cache_file_paths(directory: Path) -> tuple[Path, Path, Path]:
    """Return metadata, overview, and full-document artifact paths."""
    return (
        directory / "metadata.json",
        directory / "overview.f32",
        directory / "full_document.f32",
    )


def validate_dense_cache(
    directory: Path,
    movies: list[JsonObject],
    contract: VectorContract,
    source_checksum: str,
) -> DenseEmbeddingCache | None:
    """Return a complete compatible cache, or None when no cache exists."""
    metadata_path, overview_path, full_document_path = cache_file_paths(directory)
    existing_paths = [
        path for path in (metadata_path, overview_path, full_document_path)
        if path.exists()
    ]
    if not existing_paths:
        return None
    if len(existing_paths) != 3:
        raise ValueError(f"dense embedding cache is incomplete: {directory}")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read dense cache metadata: {error}") from error

    expected_metadata = {
        "contract_version": contract.version,
        "requested_model": contract.dense_model,
        "dimensions": contract.dense_dimensions,
        "source_checksum": source_checksum,
        "record_count": len(movies),
        "movie_ids_checksum": movie_ids_checksum(movies),
    }
    for field, expected_value in expected_metadata.items():
        if metadata.get(field) != expected_value:
            raise ValueError(f"dense embedding cache has incompatible {field}")

    expected_bytes = len(movies) * contract.dense_dimensions * 4
    for vector_path in (overview_path, full_document_path):
        if vector_path.stat().st_size != expected_bytes:
            raise ValueError(f"dense embedding cache has invalid size: {vector_path}")
    return DenseEmbeddingCache(directory=directory, metadata=metadata)


def write_float_vector(stream: Any, values: list[float]) -> None:
    """Write one vector as portable little-endian float32 values."""
    vector = array.array("f", values)
    if vector.itemsize != 4:
        raise RuntimeError("this platform does not use four-byte floats")
    if sys.byteorder != "little":
        vector.byteswap()
    vector.tofile(stream)


def generate_dense_cache(
    directory: Path,
    movies: list[JsonObject],
    contract: VectorContract,
    source_checksum: str,
) -> DenseEmbeddingCache:
    """Generate paid dense vectors once and preserve them as commit artifacts."""
    existing_cache = validate_dense_cache(
        directory, movies, contract, source_checksum
    )
    if existing_cache is not None:
        return existing_cache

    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("openai is required to generate dense embeddings") from error
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("environment variable OPENAI_API_KEY is missing")

    directory.mkdir(parents=True, exist_ok=True)
    metadata_path, overview_path, full_document_path = cache_file_paths(directory)
    overview_temporary_path = overview_path.with_suffix(".f32.tmp")
    full_document_temporary_path = full_document_path.with_suffix(".f32.tmp")
    metadata_temporary_path = metadata_path.with_suffix(".json.tmp")
    openai_client = OpenAI()
    total_tokens = 0
    returned_model: str | None = None

    with (
        overview_temporary_path.open("wb") as overview_stream,
        full_document_temporary_path.open("wb") as full_document_stream,
    ):
        for movie_batch in batches(movies, BATCH_SIZE):
            dense_inputs = []
            for movie in movie_batch:
                dense_inputs.extend([overview_text(movie), full_document_text(movie)])
            response = openai_client.embeddings.create(
                model=contract.dense_model,
                dimensions=contract.dense_dimensions,
                input=dense_inputs,
                encoding_format="float",
            )
            total_tokens += response.usage.total_tokens
            if returned_model is None:
                returned_model = response.model
            elif response.model != returned_model:
                raise RuntimeError("OpenAI returned inconsistent model identifiers")

            vectors = [item.embedding for item in sorted(
                response.data, key=lambda item: item.index
            )]
            if len(vectors) != len(movie_batch) * 2:
                raise RuntimeError("OpenAI returned an unexpected embedding count")
            if any(len(vector) != contract.dense_dimensions for vector in vectors):
                raise RuntimeError("OpenAI returned an unexpected embedding dimension")
            for index in range(len(movie_batch)):
                write_float_vector(overview_stream, vectors[index * 2])
                write_float_vector(full_document_stream, vectors[index * 2 + 1])

    metadata = {
        "contract_version": contract.version,
        "requested_model": contract.dense_model,
        "returned_model": returned_model,
        "dimensions": contract.dense_dimensions,
        "source_checksum": source_checksum,
        "record_count": len(movies),
        "movie_ids_checksum": movie_ids_checksum(movies),
        "total_tokens": total_tokens,
        "format": "little-endian float32 in ascending movie_id order",
    }
    metadata_temporary_path.write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    overview_temporary_path.replace(overview_path)
    full_document_temporary_path.replace(full_document_path)
    metadata_temporary_path.replace(metadata_path)
    cache = validate_dense_cache(directory, movies, contract, source_checksum)
    if cache is None:
        raise RuntimeError("dense embedding cache was not created")
    return cache


def read_float_vectors(
    path: Path,
    start: int,
    count: int,
    dimensions: int,
) -> list[list[float]]:
    """Read a contiguous movie batch from a committed float32 artifact."""
    values = array.array("f")
    with path.open("rb") as stream:
        stream.seek(start * dimensions * 4)
        values.fromfile(stream, count * dimensions)
    if sys.byteorder != "little":
        values.byteswap()
    return [
        values[offset:offset + dimensions].tolist()
        for offset in range(0, len(values), dimensions)
    ]


def canonical_movies(layouts: dict[tuple[str, str], ShardLoad]) -> list[JsonObject]:
    """Return the corpus once, in ascending movie-ID order."""
    movies = [
        *layouts[("hash", "a")].movies,
        *layouts[("hash", "b")].movies,
    ]
    return sorted(movies, key=lambda movie: movie["movie_id"])


def qdrant_clients() -> dict[str, Any]:
    """Connect to the two explicitly configured physical Qdrant shards."""
    try:
        from qdrant_client import QdrantClient
    except ImportError as error:
        raise RuntimeError("qdrant-client is required for Qdrant loading") from error

    clients = {}
    for shard_id, environment_name in QDRANT_URL_ENV.items():
        url = os.getenv(environment_name)
        if not url:
            raise RuntimeError(f"environment variable {environment_name} is missing")
        clients[shard_id] = QdrantClient(url=url)
    return clients


def ensure_collections_do_not_exist(
    clients: dict[str, Any], contract: VectorContract
) -> None:
    """Refuse replacement before embeddings are generated or data is written."""
    for client in clients.values():
        existing_names = {item.name for item in client.get_collections().collections}
        conflicts = set(contract.collections.values()) & existing_names
        if conflicts:
            raise RuntimeError(
                f"refusing to replace existing Qdrant collections: {sorted(conflicts)}"
            )


def create_collections(clients: dict[str, Any], contract: VectorContract) -> None:
    """Create four empty collections and their required filter indexes."""
    from qdrant_client import models

    for client in clients.values():
        for collection_name in contract.collections.values():
            client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    contract.overview_vector_name: models.VectorParams(
                        size=contract.dense_dimensions,
                        distance=models.Distance.COSINE,
                    ),
                    contract.full_document_vector_name: models.VectorParams(
                        size=contract.dense_dimensions,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    contract.metadata_sparse_vector_name: models.SparseVectorParams(
                        modifier=models.Modifier.IDF
                    )
                },
            )
            for field_name, field_schema in (
                ("release_year", models.PayloadSchemaType.INTEGER),
                ("genre_ids", models.PayloadSchemaType.INTEGER),
                ("industry", models.PayloadSchemaType.KEYWORD),
                ("country_codes", models.PayloadSchemaType.KEYWORD),
            ):
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                )


def point_payload(
    movie: JsonObject,
    layout_strategy: str,
    shard_id: str,
    contract: VectorContract,
    source_checksum: str,
) -> JsonObject:
    """Duplicate only filter and provenance fields into Qdrant payload."""
    year = release_year(movie)
    return {
        "movie_id": movie["movie_id"],
        "release_year": int(year) if year != UNKNOWN_VALUE else None,
        "genre_ids": [genre["genre_id"] for genre in movie["genres"]],
        "industry": movie["industry"],
        "country_codes": [country["country_code"] for country in movie["countries"]],
        "layout_strategy": layout_strategy,
        "shard_id": shard_id,
        "embedding_version": contract.version,
        "source_checksum": source_checksum,
        "overview_text_checksum": text_sha256(overview_text(movie)),
        "full_document_text_checksum": text_sha256(full_document_text(movie)),
    }


def embed_and_upsert(
    clients: dict[str, Any],
    layouts: dict[tuple[str, str], ShardLoad],
    contract: VectorContract,
    dense_cache: DenseEmbeddingCache,
) -> int:
    """Read fixed dense vectors, generate BM25, and route both layouts."""
    try:
        from fastembed import SparseTextEmbedding
        from qdrant_client import models
    except ImportError as error:
        raise RuntimeError("qdrant-client[fastembed] is required for execution") from error

    sparse_model = SparseTextEmbedding(model_name=contract.sparse_model)
    assignments = movie_assignments(layouts)
    movies = canonical_movies(layouts)
    source_checksum = layouts[("hash", "a")].manifest["input_checksum"]
    _, overview_path, full_document_path = cache_file_paths(dense_cache.directory)

    for batch_start in range(0, len(movies), BATCH_SIZE):
        movie_batch = movies[batch_start:batch_start + BATCH_SIZE]
        overview_vectors = read_float_vectors(
            overview_path,
            batch_start,
            len(movie_batch),
            contract.dense_dimensions,
        )
        full_document_vectors = read_float_vectors(
            full_document_path,
            batch_start,
            len(movie_batch),
            contract.dense_dimensions,
        )

        sparse_vectors = list(sparse_model.embed(
            [metadata_sparse_text(movie) for movie in movie_batch]
        ))
        points_by_destination: dict[tuple[str, str], list[Any]] = {
            key: [] for key in EXPECTED_LAYOUTS
        }
        for index, movie in enumerate(movie_batch):
            vector = {
                contract.overview_vector_name: overview_vectors[index],
                contract.full_document_vector_name: full_document_vectors[index],
                contract.metadata_sparse_vector_name: models.SparseVector(
                    indices=sparse_vectors[index].indices.tolist(),
                    values=sparse_vectors[index].values.tolist(),
                ),
            }
            for layout_strategy in ("hash", "industry"):
                shard_id = assignments[layout_strategy][movie["movie_id"]]
                points_by_destination[(layout_strategy, shard_id)].append(
                    models.PointStruct(
                        id=movie["movie_id"],
                        vector=vector,
                        payload=point_payload(
                            movie,
                            layout_strategy,
                            shard_id,
                            contract,
                            source_checksum,
                        ),
                    )
                )

        for (layout_strategy, shard_id), points in points_by_destination.items():
            if points:
                clients[shard_id].upsert(
                    collection_name=contract.collections[layout_strategy],
                    points=points,
                    wait=True,
                )
    return len(movies)


def verify_collection_counts(
    clients: dict[str, Any],
    layouts: dict[tuple[str, str], ShardLoad],
    contract: VectorContract,
) -> None:
    """Require every physical collection to match its manifest count."""
    for (layout_strategy, shard_id), shard_load in layouts.items():
        collection_name = contract.collections[layout_strategy]
        actual_count = clients[shard_id].count(
            collection_name=collection_name,
            exact=True,
        ).count
        expected_count = shard_load.manifest["shard_record_count"]
        if actual_count != expected_count:
            raise RuntimeError(
                f"{collection_name} on shard {shard_id} has {actual_count} "
                f"points; expected {expected_count}"
            )


def parse_args() -> argparse.Namespace:
    """Parse offline validation or explicitly authorized execution options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Validate the contract and optionally build all four collections."""
    load_dotenv()
    args = parse_args()
    contract = load_contract(args.contract)
    layouts = prepare_layouts(args.input, DEFAULT_MANIFEST_PATHS)
    movies = canonical_movies(layouts)
    print(
        f"Validated {len(movies)} movies, four layout manifests, and "
        f"vector contract {contract.version}."
    )
    print(
        f"Dense input: {len(movies) * 2} texts using {contract.dense_model} "
        f"at {contract.dense_dimensions} dimensions."
    )

    if not args.execute:
        print("Validation complete; OpenAI and Qdrant were not contacted.")
        return
    clients = qdrant_clients()
    ensure_collections_do_not_exist(clients, contract)
    source_checksum = layouts[("hash", "a")].manifest["input_checksum"]
    dense_cache = generate_dense_cache(
        DEFAULT_EMBEDDING_DIRECTORY,
        movies,
        contract,
        source_checksum,
    )
    create_collections(clients, contract)
    movie_count = embed_and_upsert(clients, layouts, contract, dense_cache)
    verify_collection_counts(clients, layouts, contract)
    print(
        f"Loaded {movie_count} unique movies into four collections. Dense "
        f"cache token total: {dense_cache.metadata['total_tokens']}."
    )


if __name__ == "__main__":
    main()
