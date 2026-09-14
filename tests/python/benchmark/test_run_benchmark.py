import io
import json

from benchmark.run_benchmark import (
    DEFAULT_QUERY_PATH,
    build_request,
    configured_queries,
    load_json,
    run_requests,
)


def test_expands_all_explicit_query_configurations() -> None:
    # Arrange
    query_document = load_json(DEFAULT_QUERY_PATH)

    # Act
    configured = configured_queries(query_document)

    # Assert
    assert len(configured) == 109
    assert {query_kind for query_kind, _, _ in configured} == {
        "relational",
        "vector_dense",
        "vector_sparse",
    }


def test_builds_each_api_request_type() -> None:
    # Arrange
    configuration = {
        "layout": "industry",
        "routing": "selective",
        "shard": "b",
        "execution_order": "search_then_filter",
    }
    representations = {
        "dense": {"vector": [0.1, 0.2]},
        "sparse": {"indices": [10, 20], "values": [1.0, 2.0]},
    }

    # Act
    relational = build_request(
        "relational",
        {"id": "rel", "sql": "SELECT 1", "limit": 2},
        configuration,
        representations,
    )
    dense = build_request(
        "vector_dense",
        {"id": "dense", "vector_name": "overview_dense", "limit": 10},
        configuration,
        representations,
    )
    sparse = build_request(
        "vector_sparse",
        {"id": "sparse", "limit": 10},
        configuration,
        representations,
    )

    # Assert
    assert relational["sql"] == "SELECT 1"
    assert "execution_order" not in relational
    assert dense["vector"] == [0.1, 0.2]
    assert dense["vector_name"] == "overview_dense"
    assert sparse["indices"] == [10, 20]
    assert sparse["values"] == [1.0, 2.0]


def test_warmups_are_excluded_and_measured_records_are_written_immediately() -> None:
    # Arrange
    query_document = {
        "default_limit": 10,
        "queries": {
            "relational": [
                {
                    "id": "rel",
                    "sql": "SELECT 1 AS movie_id",
                    "limit": 1,
                    "correctness": {"type": "movie_ids", "expected": [1]},
                    "configurations": [
                        {"layout": "hash", "routing": "broadcast"}
                    ],
                }
            ]
        }
    }
    calls = []
    output = io.StringIO()

    def send_request(url, request_body, timeout_seconds):
        calls.append((url, request_body, timeout_seconds))
        return {
            "request_latency_ms": 5.0,
            "http_status": 200,
            "response": {
                "results": [{"movie_id": 1}],
                "total_ms": 4.0,
                "shard_stats": [],
            },
            "failure": None,
        }

    # Act
    measured_count = run_requests(
        query_document,
        {},
        output,
        "http://localhost:3000/query",
        warmups=1,
        repetitions=2,
        timeout_seconds=15.0,
        send_request=send_request,
    )
    records = [json.loads(line) for line in output.getvalue().splitlines()]

    # Assert
    assert len(calls) == 3
    assert measured_count == 2
    assert len(records) == 2
    assert [record["repetition"] for record in records] == [1, 2]
    assert records[0]["contacted_shards"] == ["a", "b"]
    assert records[0]["ranking"][0]["movie_id"] == 1
    assert records[0]["relational_results"] == [{"movie_id": 1}]
    assert records[0]["correctness"] == {"type": "movie_ids", "expected": [1]}


def test_applies_the_document_default_limit() -> None:
    # Arrange
    query_document = {
        "default_limit": 10,
        "queries": {
            "vector_dense": [
                {
                    "id": "dense",
                    "configurations": [
                        {
                            "layout": "hash",
                            "routing": "broadcast",
                            "execution_order": "filter_then_search",
                        }
                    ],
                }
            ]
        },
    }

    # Act
    configured = configured_queries(query_document)

    # Assert
    assert configured[0][1]["limit"] == 10
