import json
import math

from benchmark.generate_representations import OUTPUT_PATH, QUERY_PATH, file_sha256


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_saved_representations_match_every_vector_query() -> None:
    # Arrange
    query_document = load_json(QUERY_PATH)
    representation_document = load_json(OUTPUT_PATH)

    # Act
    dense_queries = query_document["queries"]["vector_dense"]
    sparse_queries = query_document["queries"]["vector_sparse"]
    expected_ids = {query["id"] for query in [*dense_queries, *sparse_queries]}

    # Assert
    assert representation_document["schema_version"] == "benchmark-representations-v1"
    assert representation_document["query_schema_version"] == "benchmark-v1"
    assert representation_document["query_file_sha256"] == file_sha256(QUERY_PATH)
    assert representation_document["contract_version"] == "movie-retrieval-v1"
    assert representation_document["dense_model"] == "text-embedding-3-large"
    assert representation_document["dense_dimensions"] == 1024
    assert representation_document["sparse_model"] == "Qdrant/bm25"
    assert set(representation_document["representations"]) == expected_ids


def test_saved_representations_have_valid_vector_values() -> None:
    # Arrange
    query_document = load_json(QUERY_PATH)
    representations = load_json(OUTPUT_PATH)["representations"]

    # Act
    dense_vectors = [
        representations[query["id"]]["vector"]
        for query in query_document["queries"]["vector_dense"]
    ]
    sparse_vectors = [
        representations[query["id"]]
        for query in query_document["queries"]["vector_sparse"]
    ]

    # Assert
    assert all(len(vector) == 1024 for vector in dense_vectors)
    assert all(math.isfinite(value) for vector in dense_vectors for value in vector)
    assert all(vector["indices"] for vector in sparse_vectors)
    assert all(
        len(vector["indices"]) == len(vector["values"])
        for vector in sparse_vectors
    )
    assert all(
        isinstance(index, int) and index >= 0
        for vector in sparse_vectors
        for index in vector["indices"]
    )
    assert all(
        math.isfinite(value)
        for vector in sparse_vectors
        for value in vector["values"]
    )
