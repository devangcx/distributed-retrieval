"""Generate final query vectors once, outside measured benchmark execution."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

BENCHMARK_DIRECTORY = Path(__file__).resolve().parent
QUERY_PATH = BENCHMARK_DIRECTORY / "queries.json"
OUTPUT_PATH = BENCHMARK_DIRECTORY / "representations.json"
DENSE_MODEL = "text-embedding-3-large"
DENSE_DIMENSIONS = 1024
SPARSE_MODEL = "Qdrant/bm25"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing representation artifact",
    )
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_representations() -> dict[str, Any]:
    """Generate paid OpenAI dense vectors and local sparse vectors for fixed queries.

    Return vectors keyed by query ID with model, token-use, and checksum metadata.
    This performs embedding work only. The caller saves the resulting document.
    """
    try:
        from fastembed import SparseTextEmbedding
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("OpenAI and FastEmbed dependencies are required") from error

    query_document = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    dense_queries = query_document["queries"]["vector_dense"]
    sparse_queries = query_document["queries"]["vector_sparse"]

    dense_response = OpenAI().embeddings.create(
        model=DENSE_MODEL,
        dimensions=DENSE_DIMENSIONS,
        input=[query["query_text"] for query in dense_queries],
        encoding_format="float",
    )
    dense_vectors = sorted(dense_response.data, key=lambda item: item.index)

    sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL)
    sparse_vectors = list(
        sparse_model.query_embed([query["query_text"] for query in sparse_queries])
    )

    representations: dict[str, Any] = {}
    for query, vector in zip(dense_queries, dense_vectors, strict=True):
        representations[query["id"]] = {"vector": vector.embedding}
    for query, vector in zip(sparse_queries, sparse_vectors, strict=True):
        representations[query["id"]] = {
            "indices": vector.indices.tolist(),
            "values": vector.values.tolist(),
        }

    return {
        "schema_version": "benchmark-representations-v1",
        "query_schema_version": query_document["schema_version"],
        "query_file_sha256": file_sha256(QUERY_PATH),
        "contract_version": query_document["vector_contract_version"],
        "dense_model": DENSE_MODEL,
        "dense_dimensions": DENSE_DIMENSIONS,
        "dense_returned_model": dense_response.model,
        "dense_total_tokens": dense_response.usage.total_tokens,
        "sparse_model": SPARSE_MODEL,
        "representations": representations,
    }


def main() -> None:
    """Save generated query vectors, refusing an existing artifact without --force."""
    from dotenv import load_dotenv

    arguments = parse_arguments()
    if OUTPUT_PATH.exists() and not arguments.force:
        raise FileExistsError(
            f"{OUTPUT_PATH} already exists; use --force only after an intentional query change"
        )

    load_dotenv()
    representation_document = generate_representations()

    temporary_path = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(representation_document, indent=2) + "\n", encoding="utf-8"
    )
    temporary_path.replace(OUTPUT_PATH)
    print(f"saved {len(representation_document['representations'])} representations")


if __name__ == "__main__":
    main()
