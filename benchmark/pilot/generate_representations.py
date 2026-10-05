"""Generate the pilot query vectors once, outside measured API execution."""

import json
from pathlib import Path

from dotenv import load_dotenv
from fastembed import SparseTextEmbedding
from openai import OpenAI

PILOT_DIRECTORY = Path(__file__).resolve().parent
QUERY_PATH = PILOT_DIRECTORY / "queries.json"
OUTPUT_PATH = PILOT_DIRECTORY / "representations.json"
DENSE_MODEL = "text-embedding-3-large"
DENSE_DIMENSIONS = 1024
SPARSE_MODEL = "Qdrant/bm25"


def main() -> None:
    load_dotenv()
    query_document = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    queries = query_document["queries"]

    dense_queries, sparse_queries = [], []
    for query in queries:
        if query["kind"] == "vector_dense":
            dense_queries.append(query)
        elif query["kind"] == "vector_sparse":
            sparse_queries.append(query)

    dense_response = OpenAI().embeddings.create(
        model=DENSE_MODEL,
        dimensions=DENSE_DIMENSIONS,
        input=[query["query_text"] for query in dense_queries],
        encoding_format="float",
    )
    dense_vectors = sorted(dense_response.data, key=lambda item: item.index)

    sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL)
    sparse_vectors = list(
        sparse_model.query_embed([query["query_text"]
                                 for query in sparse_queries])
    )

    representations: dict[str, object] = {}

    for query, vector in zip(dense_queries, dense_vectors, strict=True):
        representations[query["id"]] = {"vector": vector.embedding}

    for query, vector in zip(sparse_queries, sparse_vectors, strict=True):
        representations[query["id"]] = {
            "indices": vector.indices.tolist(),
            "values": vector.values.tolist(),
        }

    output = {
        "schema_version": query_document["schema_version"],
        "contract_version": "movie-retrieval-v1",
        "dense_model": DENSE_MODEL,
        "dense_dimensions": DENSE_DIMENSIONS,
        "sparse_model": SPARSE_MODEL,
        "representations": representations,
    }
    OUTPUT_PATH.write_text(json.dumps(
        output, indent=2) + "\n", encoding="utf-8")
    print(f"saved {len(representations)} representations to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
