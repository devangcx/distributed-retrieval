"""Generate and validate reproducible two-shard layout manifests.

Usage:
    python scripts/generate_layout_manifests.py
        Generate both hash and industry manifests from the canonical dataset.

    python scripts/generate_layout_manifests.py --strategy hash
        Generate only the hash manifests. Use "industry" for only the
        industry manifests.

    python scripts/generate_layout_manifests.py --input PATH --output-dir PATH
        Override the canonical dataset and manifest output locations.

Default output:
    data/processed/layouts/{strategy}/shard_{a,b}.json
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

DEFAULT_INPUT_PATH = Path("data/processed/movies.json")
DEFAULT_OUTPUT_DIR = Path("data/processed/layouts")
SCHEMA_VERSION = "1"
SHARD_IDS = ("a", "b")
INDUSTRIES = {"hollywood", "bollywood", "other_or_ambiguous"}

JsonObject = dict[str, Any]
ShardSelector = Callable[[JsonObject], str]


def parse_args() -> argparse.Namespace:
    """Parse paths and the layout strategy from the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help=f"canonical movie JSON array (default: {DEFAULT_INPUT_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"root directory for manifests (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--strategy",
        choices=("all", "hash", "industry"),
        default="all",
        help="layout strategy to generate (default: all)",
    )
    return parser.parse_args()


def load_movies(input_path: Path) -> tuple[list[JsonObject], str]:
    """Load the canonical corpus and return it with its SHA-256 checksum."""
    source_bytes = input_path.read_bytes()
    movies = json.loads(source_bytes)

    if not isinstance(movies, list):
        raise ValueError(f"Expected a JSON array in {input_path}")

    ensure_valid_source_movies(movies)
    return movies, hashlib.sha256(source_bytes).hexdigest()


def ensure_valid_source_movies(movies: list[JsonObject]) -> None:
    """Reject records that cannot be assigned reproducibly."""
    movie_ids = []

    for index, movie in enumerate(movies):
        if not isinstance(movie, dict):
            raise ValueError(f"Movie at index {index} is not a JSON object")

        movie_id = movie.get("movie_id")
        if not isinstance(movie_id, int) or isinstance(movie_id, bool):
            raise ValueError(f"Movie at index {index} has an invalid movie_id")
        movie_ids.append(movie_id)

        if movie.get("industry") not in INDUSTRIES:
            raise ValueError(f"Movie {movie_id} has an invalid industry")

    if len(movie_ids) != len(set(movie_ids)):
        raise ValueError("Canonical corpus contains duplicate movie IDs")


def select_hash_shard(movie: JsonObject) -> str:
    """Assign even movie IDs to A and odd movie IDs to B."""
    return "a" if movie["movie_id"] % 2 == 0 else "b"


def select_industry_shard(movie: JsonObject) -> str:
    """Assign Hollywood to A and all other industry classes to B."""
    return "a" if movie["industry"] == "hollywood" else "b"


def assign_movies_to_shards(
    movies: list[JsonObject],
    shard_selector: ShardSelector,
) -> dict[str, list[int]]:
    """Assign movie IDs and verify complete, disjoint shard membership."""
    shard_assignments = {shard_id: [] for shard_id in SHARD_IDS}

    for movie in movies:
        shard_id = shard_selector(movie)
        if shard_id not in shard_assignments:
            raise ValueError(f"Layout selector returned invalid shard {shard_id!r}")
        shard_assignments[shard_id].append(movie["movie_id"])

    for movie_ids in shard_assignments.values():
        movie_ids.sort()

    ensure_complete_layout(movies, shard_assignments)
    return shard_assignments


def ensure_complete_layout(
    movies: list[JsonObject],
    shard_assignments: dict[str, list[int]],
) -> None:
    """Ensure shard ID sets are disjoint and cover the complete corpus."""
    source_ids = {movie["movie_id"] for movie in movies}
    shard_a_ids = set(shard_assignments["a"])
    shard_b_ids = set(shard_assignments["b"])

    overlap = shard_a_ids & shard_b_ids
    if overlap:
        raise ValueError(f"Shard manifests overlap on movie IDs: {sorted(overlap)}")

    assigned_ids = shard_a_ids | shard_b_ids
    if assigned_ids != source_ids:
        missing = sorted(source_ids - assigned_ids)
        unexpected = sorted(assigned_ids - source_ids)
        raise ValueError(
            f"Layout manifests do not cover the corpus; "
            f"missing={missing}, unexpected={unexpected}"
        )


def build_manifest(
    strategy: str,
    shard_id: str,
    movie_ids: list[int],
    source_checksum: str,
    source_record_count: int,
) -> JsonObject:
    """Build the stable manifest format consumed by database loaders."""
    return {
        "schema_version": SCHEMA_VERSION,
        "layout_strategy": strategy,
        "shard_id": shard_id,
        "input_checksum": source_checksum,
        "source_record_count": source_record_count,
        "shard_record_count": len(movie_ids),
        "movie_ids": movie_ids,
    }


def write_strategy_manifests(
    strategy: str,
    shard_assignments: dict[str, list[int]],
    source_checksum: str,
    source_record_count: int,
    output_dir: Path,
) -> None:
    """Write one deterministic JSON manifest for each shard."""
    strategy_dir = output_dir / strategy
    strategy_dir.mkdir(parents=True, exist_ok=True)

    for shard_id in SHARD_IDS:
        manifest = build_manifest(
            strategy,
            shard_id,
            shard_assignments[shard_id],
            source_checksum,
            source_record_count,
        )
        output_path = strategy_dir / f"shard_{shard_id}.json"
        output_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {manifest['shard_record_count']} IDs to {output_path}")


def main() -> None:
    """Generate the requested manifests from the canonical movie corpus."""
    args = parse_args()
    movies, source_checksum = load_movies(args.input)
    selectors = {
        "hash": select_hash_shard,
        "industry": select_industry_shard,
    }
    if args.strategy == "all":
        requested_strategies = ("hash", "industry")
    else:
        requested_strategies = (args.strategy,)

    for strategy in requested_strategies:
        shard_assignments = assign_movies_to_shards(movies, selectors[strategy])
        write_strategy_manifests(
            strategy,
            shard_assignments,
            source_checksum,
            len(movies),
            args.output_dir,
        )


if __name__ == "__main__":
    main()
